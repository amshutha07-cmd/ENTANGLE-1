import re
import serial
import serial.tools.list_ports
import time

# Boards/adapters commonly used with the PN532 sketch (USB vendor ids: Arduino, CH340/CH341, CP210x, FTDI)
_KNOWN_VIDS = {0x2341, 0x2A03, 0x1A86, 0x10C4, 0x0403}
_NAME_HINTS = ("arduino", "ch340", "ch341", "cp210", "ftdi", "usb serial", "usb-serial", "wch")
_DEVICE_RE = re.compile(r"(ttyACM\d+|ttyUSB\d+|cu\.usbmodem|cu\.usbserial|^COM\d+$)", re.IGNORECASE)
_NEVER = ("bluetooth", "debug-console", "wlan", "irda")


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
                if line == "READY":
                    ready = True
                    break
                if line.startswith("ERROR"):
                    print(f"[NFC Bridge] Reader reported: {line}")
                    break
            if not ready:
                ser.close()
                print(f"[NFC Bridge] {self.port} did not identify as the ANSX reader; ignoring it.")
                return
            ser.timeout = 2
            self.ser = ser
            print(f"[NFC Bridge] Reader ready on {self.port}")
        except Exception as e:
            print(f"[NFC Bridge] Failed to connect: {e}")

    def is_connected(self) -> bool:
        return self.ser is not None and self.ser.is_open

    def close(self):
        try:
            if self.ser and self.ser.is_open:
                self.ser.close()
        except Exception:
            pass

    def write_payload_to_tag(self, payload: str, timeout=30) -> bool:
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
            self.ser.reset_input_buffer()
            cmd = f"WRITE:{hex_payload}\n"
            self.ser.write(cmd.encode())
            print(f"[NFC Bridge] WRITE command sent, waiting for card tap...")

            attempt_start = time.time()
            while (time.time() - attempt_start) < 12:  # 12s per attempt
                try:
                    if self.ser.in_waiting > 0:
                        line = self.ser.readline().decode(errors='ignore').strip()
                        print(f"[NFC Bridge] Arduino says: {line}")
                        if line == "SUCCESS: Written":
                            return True
                        if line.startswith("ERROR"):
                            print(f"[NFC Bridge] Write attempt failed ({line}), retrying...")
                            break  # break inner loop → retry outer loop
                        # WAITING_FOR_CARD / other status messages: keep waiting
                except (serial.SerialException, OSError) as e:
                    print(f"[NFC Bridge] Serial error during write: {e}")
                    return False
                time.sleep(0.1)

        print("[NFC Bridge] Timeout waiting for Arduino")
        return False

    def read_payload_from_tag(self, timeout=30) -> str | None:
        """
        Sends READ command. Arduino returns bytes as hex pairs (e.g. DATA:414E5358...).
        We decode hex → bytes → ASCII to get the original string back.
        """
        if not self.is_connected():
            return None

        self.ser.reset_input_buffer()
        self.ser.write(b"READ\n")

        start_time = time.time()
        while (time.time() - start_time) < timeout:
            try:
                if self.ser.in_waiting > 0:
                    line = self.ser.readline().decode(errors='ignore').strip()
                    print(f"[NFC Bridge] Arduino says: {line}")
                    if line.startswith("DATA:"):
                        hex_data = line[5:].strip()
                        try:
                            raw = bytes.fromhex(hex_data)
                            return raw.decode('ascii', errors='ignore').rstrip('\x00')
                        except Exception:
                            return hex_data   # fallback: return raw hex string
                    if line.startswith("ERROR"):
                        print(f"[NFC Bridge] Read failed: {line}")
                        return None
            except (serial.SerialException, OSError) as e:
                print(f"[NFC Bridge] Serial error during read: {e}")
                return None
            time.sleep(0.1)
        print("[NFC Bridge] Timeout waiting for Arduino")
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
