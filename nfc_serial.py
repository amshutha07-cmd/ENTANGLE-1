import hashlib
import hmac
import logging
import re
import serial
import serial.tools.list_ports
import time

logger = logging.getLogger(__name__)


def _redact(line: str) -> str:
    """Reader output for the log. A READ answer carries the card's secret, which must never reach a log file."""
    return "DATA:<hidden>" if line.startswith("DATA:") else line


class ReaderError(Exception):
    """The card reader stopped responding (unplugged, cable glitch, board reset). The message is safe to show to a user."""


class WrongCard(ReaderError):
    """A different card was put on the reader between reading and writing."""


class CardLocked(ReaderError):
    """The card is protected with a key this computer does not have (it was set up on another computer)."""


PROTOCOL = 2          # arduino_nfc.ino: per-card keys, same-card writes, time-limited waits

READER_LOST = ("The card reader lost its connection. Unplug the USB cable, plug it back in, wait 3 seconds, and try again. "
               "(If it keeps happening: use a data-capable cable, and close the Arduino Serial Monitor if it is open.)")

# Boards/adapters commonly used with the PN532 sketch (USB vendor ids: Arduino, CH340/CH341, CP210x, FTDI)
_KNOWN_VIDS = {0x2341, 0x2A03, 0x1A86, 0x10C4, 0x0403}
_NAME_HINTS = ("arduino", "ch340", "ch341", "cp210", "ftdi", "usb serial", "usb-serial", "wch")
_DEVICE_RE = re.compile(r"(ttyACM\d+|ttyUSB\d+|cu\.usbmodem|cu\.usbserial|^COM\d+$)", re.IGNORECASE)
_NEVER = ("bluetooth", "debug-console", "wlan", "irda")


def card_key(uid_hex: str) -> str:
    """
    The card's own Mifare key (12 hex digits): HMAC of its UID with this computer's protected secret. Firmware v2 locks
    the card's secret sector with it, so a reader using the factory key (FF FF FF FF FF FF) gets nothing from the card.
    Identities only unlock on the computer that made them, so a key bound to this computer loses nothing.
    """
    import platform_secret
    mac = hmac.new(platform_secret.get_platform_secret(), b"ANSX card key\n" + bytes.fromhex(uid_hex), hashlib.sha256)
    return mac.hexdigest()[:12].upper()


def _decode_block(hex_data: str) -> str:
    try:
        return bytes.fromhex(hex_data.strip()).decode("ascii", errors="ignore").rstrip("\x00")
    except ValueError:
        return hex_data


def find_reader_port() -> str | None:
    """
    Return the serial port that looks like the Arduino/PN532 reader, or None.
    Only genuine USB serial devices qualify; built-in ports (e.g. macOS Bluetooth) never do.
    """
    candidates = []
    for p in serial.tools.list_ports.comports():
        dev = (p.device or "")
        blob = f"{dev} {p.description or ''} {p.manufacturer or ''}".lower()
        if any(bad in blob for bad in _NEVER):
            continue
        score = 0
        if p.vid in _KNOWN_VIDS:
            score += 3
        if any(h in blob for h in _NAME_HINTS):
            score += 2
        if _DEVICE_RE.search(dev):
            score += 1
        if score:
            candidates.append((score, dev))
    return max(candidates)[1] if candidates else None


def reader_status() -> str:
    """Cheap, non-blocking: "connected" (already talking to it), "found" (a plausible port exists), or "missing"."""
    with _bridge_lock:
        if _bridge_instance is not None and _bridge_instance.is_connected():
            return "connected"
    return "found" if find_reader_port() else "missing"


class HardwareNFCBridge:
    proto = 1                                     # firmware protocol; 2 = per-card keys + same-card writes
    last_uid: str | None = None                   # card seen by the last successful read (protocol 2)
    last_key: str | None = None                   # "C" = the card's own key, "D" = still the factory key

    def __init__(self, baud_rate=115200):
        self.baud_rate = baud_rate
        self.port = find_reader_port()
        self.ser = None
        if not self.port:
            return
        try:
            ser = serial.Serial(self.port, self.baud_rate, timeout=1)
            # Opening the port resets the Arduino; the sketch prints READY when the PN532 answered.
            deadline = time.time() + 4
            ready = False
            while time.time() < deadline:
                line = ser.readline().decode(errors="ignore").strip()
                if line.startswith("ANSX_READER "):
                    self.proto = int(line.split()[1]) if line.split()[1].isdigit() else 1
                if line == "READY":
                    ready = True
                    break
                if line.startswith("ERROR"):
                    logger.warning(f"Reader reported: {line}")
                    break
            if not ready:
                ser.close()
                logger.info(f"{self.port} did not identify as the ANSX reader; ignoring it.")
                return
            ser.timeout = 2
            self.ser = ser
            if self.proto < PROTOCOL:
                self.proto = self._ask_version()
            logger.info(f"Reader ready on {self.port} (firmware protocol {self.proto})")
            if self.proto < PROTOCOL:
                logger.warning("The reader runs old firmware: cards keep the factory key and a card swap between read and "
                               "write is not detected. Upload arduino_nfc/arduino_nfc.ino to the Arduino to fix this.")
        except Exception as e:
            logger.warning(f"Failed to connect: {e}")

    def is_connected(self) -> bool:
        if self.ser is None or not self.ser.is_open:
            return False
        try:
            self.ser.in_waiting                       # raises if the USB device was unplugged or reset
            return True
        except (serial.SerialException, OSError):
            self._drop()
            return False

    def _drop(self) -> None:
        """The device vanished: forget it, so the next attempt reconnects from scratch instead of reusing a dead port."""
        try:
            if self.ser is not None:
                self.ser.close()
        except Exception:
            pass
        self.ser = None

    def close(self):
        try:
            if self.ser and self.ser.is_open:
                self.ser.close()
        except Exception:
            pass

    # ── protocol 2 ───────────────────────────────────────────────────────────────────────────────
    def _ask_version(self) -> int:
        """Boards that did not reset on open never printed their version: ask. Firmware v1 ignores the question."""
        line = self._exchange("VERSION", 1.5, answers=("ANSX_READER",))
        return int(line.split()[1]) if line and line.split()[1:2] and line.split()[1].isdigit() else 1

    def _exchange(self, command: str, wait: float, answers=("UID:", "DATA:", "SUCCESS", "ERROR")) -> str:
        """Send one command and return the first answer line, or "" if none came within `wait` seconds."""
        try:
            self.ser.reset_input_buffer()
            self.ser.write((command + "\n").encode())
            end = time.time() + wait
            while time.time() < end:
                if self.ser.in_waiting > 0:
                    line = self.ser.readline().decode(errors="ignore").strip()
                    logger.debug("Arduino says: %s", _redact(line))
                    if line.startswith(answers):
                        return line
                else:
                    time.sleep(0.02)
        except (serial.SerialException, OSError) as e:
            logger.warning(f"Reader disconnected: {e}")
            self._drop()
            raise ReaderError(READER_LOST) from e
        return ""

    def wait_for_card(self, timeout: float = 30, cancelled=None) -> str | None:
        """UID (8 hex) of the card put on the reader within `timeout` s, or None. Asks in short rounds so Cancel works."""
        end = time.time() + timeout
        while time.time() < end:
            if cancelled and cancelled():
                return None
            ms = int(min(2.0, max(0.2, end - time.time())) * 1000)
            line = self._exchange(f"UID:{ms}", ms / 1000 + 3)
            if line.startswith("UID:"):
                uid = line[4:].strip().upper()
                if len(uid) != 8:
                    raise ReaderError("This kind of card is not supported. Use a Mifare Classic 1K card.")
                return uid
            if line and line != "ERROR: No card":
                logger.warning(f"Waiting for a card: {line}")
        return None

    def _raise_for(self, line: str) -> None:
        if line == "ERROR: Wrong card":
            raise WrongCard("A different card is on the reader now. Put back the card you started with and try again.")
        if line == "ERROR: Auth Failed":
            raise CardLocked("This card is protected with a key from another computer, so it cannot be used here. "
                             "Use a blank card, or the computer this card was set up on.")

    def read_payload_from_tag(self, timeout=30, cancelled=None) -> str | None:
        """The secret stored on the card, "" for a blank card, or None if no card was read in time."""
        if not self.is_connected():
            return None
        if self.proto < PROTOCOL:
            return self._read_v1(timeout)
        self.last_uid = self.last_key = None
        uid = self.wait_for_card(timeout, cancelled)
        if not uid:
            return None
        line = self._exchange(f"READ:{uid}:{card_key(uid)}", 8)
        if line.startswith("DATA:") and line.count(":") == 2:
            _, kind, hex_data = line.split(":")
            self.last_uid, self.last_key = uid, kind
            return _decode_block(hex_data)
        self._raise_for(line)
        logger.warning(f"Read failed: {line or 'no answer'}")
        return None

    def write_payload_to_tag(self, payload: str, timeout=30, same_card: bool = False) -> bool:
        """
        Store `payload` (up to 16 ASCII characters) on the card and lock it with the card's own key. With `same_card`,
        only the card from the last read is accepted (raises WrongCard otherwise), which is what registration needs.
        """
        if not self.is_connected():
            return False
        if self.proto < PROTOCOL:
            return self._write_v1(payload, timeout)
        raw = payload.encode("ascii")
        if len(raw) > 16:
            raise ValueError("A card holds at most 16 characters.")
        data = raw.ljust(16, b"\0").hex().upper()
        uid = self.last_uid if same_card else self.wait_for_card(timeout)
        if same_card and not uid:
            raise ValueError("same_card needs a successful read first.")
        if not uid:
            return False
        end = time.time() + timeout
        while time.time() < end:
            line = self._exchange(f"WRITE:{uid}:{card_key(uid)}:{data}", 8)
            if line == "SUCCESS: Written":
                self.last_uid, self.last_key = uid, "C"
                return True
            self._raise_for(line)
            logger.warning(f"Write attempt failed ({line or 'no answer'}), retrying...")   # e.g. card lifted for a moment
            time.sleep(0.5)
        logger.warning("Timeout waiting for Arduino")
        return False

    def lock_card(self, payload: str) -> bool:
        """
        After a successful login with a card that still has the factory key: write the same secret back, which locks the
        card with its own key. True if the card was locked now; False if there was nothing to do (or old firmware).
        """
        if self.proto < PROTOCOL or self.last_key != "D" or not self.last_uid:
            return False
        return self.write_payload_to_tag(payload, timeout=10, same_card=True)

    # ── protocol 1 (firmware not updated yet) ────────────────────────────────────────────────────
    def _write_v1(self, payload: str, timeout=30) -> bool:
        """
        Sends WRITE command. Payload encoded as hex string so Arduino
        stores exactly 16 raw bytes (avoids getBytes() null-terminator truncation).
        Retries on ERROR so a single bad card tap doesn't abort the whole write.
        """
        if not self.is_connected():
            return False

        # Encode payload to hex — Arduino's hex branch handles this perfectly
        hex_payload = payload.encode('utf-8').hex().upper()
        
        start_time = time.time()
        while (time.time() - start_time) < timeout:
            # Re-send the WRITE command each loop so Arduino waits for the next tap
            try:
                self.ser.reset_input_buffer()
                cmd = f"WRITE:{hex_payload}\n"
                self.ser.write(cmd.encode())
            except (serial.SerialException, OSError) as e:
                logger.warning(f"Reader disconnected while writing: {e}")
                self._drop()
                raise ReaderError(READER_LOST) from e
            logger.info("WRITE command sent, waiting for card tap...")

            attempt_start = time.time()
            while (time.time() - attempt_start) < 12:  # 12s per attempt
                try:
                    if self.ser.in_waiting > 0:
                        line = self.ser.readline().decode(errors='ignore').strip()
                        logger.debug("Arduino says: %s", _redact(line))
                        if line == "SUCCESS: Written":
                            return True
                        if line.startswith("ERROR"):
                            logger.warning(f"Write attempt failed ({line}), retrying...")
                            break  # break inner loop → retry outer loop
                        # WAITING_FOR_CARD / other status messages: keep waiting
                except (serial.SerialException, OSError) as e:
                    logger.warning(f"Serial error during write: {e}")
                    self._drop()
                    raise ReaderError(READER_LOST) from e
                time.sleep(0.1)

        logger.warning("Timeout waiting for Arduino")
        return False

    def _read_v1(self, timeout=30) -> str | None:
        """
        Sends READ command. Arduino returns bytes as hex pairs (e.g. DATA:414E5358...).
        We decode hex → bytes → ASCII to get the original string back.
        """
        if not self.is_connected():
            return None

        try:
            self.ser.reset_input_buffer()
            self.ser.write(b"READ\n")
        except (serial.SerialException, OSError) as e:
            logger.warning(f"Reader disconnected while reading: {e}")
            self._drop()
            raise ReaderError(READER_LOST) from e

        start_time = time.time()
        while (time.time() - start_time) < timeout:
            try:
                if self.ser.in_waiting > 0:
                    line = self.ser.readline().decode(errors='ignore').strip()
                    logger.debug("Arduino says: %s", _redact(line))
                    if line.startswith("DATA:"):
                        hex_data = line[5:].strip()
                        try:
                            raw = bytes.fromhex(hex_data)
                            return raw.decode('ascii', errors='ignore').rstrip('\x00')
                        except Exception:
                            return hex_data   # fallback: return raw hex string
                    if line.startswith("ERROR"):
                        logger.warning(f"Read failed: {line}")
                        return None
            except (serial.SerialException, OSError) as e:
                logger.warning(f"Serial error during read: {e}")
                self._drop()
                raise ReaderError(READER_LOST) from e
            time.sleep(0.1)
        logger.warning("Timeout waiting for Arduino")
        return None


# ── Singleton bridge so Write and Read workers share ONE serial connection ──
_bridge_instance: HardwareNFCBridge | None = None
_bridge_lock = __import__('threading').Lock()

_last_failed_attempt = 0.0


def get_shared_bridge() -> HardwareNFCBridge:
    """Process-wide bridge. A failed attempt is not retried for 3 s so the UI never stalls on a missing reader."""
    global _bridge_instance, _last_failed_attempt
    with _bridge_lock:
        if _bridge_instance is None or not _bridge_instance.is_connected():
            if _bridge_instance is not None:
                _bridge_instance.close()
            if _bridge_instance is not None and time.time() - _last_failed_attempt < 3:
                return _bridge_instance
            _bridge_instance = HardwareNFCBridge()
            if not _bridge_instance.is_connected():
                _last_failed_attempt = time.time()
        return _bridge_instance


# ── PyQt6 QThread Wrappers ────────────────────────────────────────────────
from PyQt6.QtCore import QThread, pyqtSignal

class NFCWriteWorker(QThread):
    """Runs the hardware NFC write operation in the background."""
    finished = pyqtSignal(bool)

    def __init__(self, payload: str):
        super().__init__()
        self.payload = payload

    def run(self):
        bridge = get_shared_bridge()
        success = bridge.write_payload_to_tag(self.payload)
        self.finished.emit(success)

class NFCReadWorker(QThread):
    """Runs the hardware NFC read operation in the background."""
    finished = pyqtSignal(str)

    def __init__(self):
        super().__init__()

    def run(self):
        bridge = get_shared_bridge()
        payload = bridge.read_payload_from_tag()
        self.finished.emit(payload if payload else "")
