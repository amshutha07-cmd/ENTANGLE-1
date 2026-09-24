import os
import socket
import struct
import time
import logging
from threading import Thread, Event
from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)

MAGIC_HEADER = b"ANSX"
MAGIC_NACK   = b"NACK"
MAGIC_ACK    = b"ACK!"
CHUNK_SIZE   = 8192  # 8KB payload per UDP packet
UDP_PORT     = 8099
TIMEOUT_SEC  = 0.5
MAX_RETRIES  = 20

class ANSX_UDP_Protocol:
    """
    Custom Reliable-UDP (R-UDP) Protocol for A.N.Sx Vault.
    Packet Structure:
        [ANSX] (4) + [SEQ_NUM] (4) + [TOTAL_CHUNKS] (4) + [PAYLOAD] (<=8192)
    """

    @staticmethod
    def pack_chunk(seq: int, total: int, payload: bytes) -> bytes:
        header = struct.pack("!4sII", MAGIC_HEADER, seq, total)
        return header + payload

    @staticmethod
    def unpack_chunk(data: bytes):
        if len(data) < 12 or not data.startswith(MAGIC_HEADER):
            return None
        header = data[:12]
        payload = data[12:]
        _, seq, total = struct.unpack("!4sII", header)
        return seq, total, payload

    @staticmethod
    def pack_nack(missing_seqs: list[int]) -> bytes:
        # We can pack up to ~1000 missing sequences in one packet comfortably.
        count = min(len(missing_seqs), 1000)
        fmt = f"!4sI{count}I"
        return struct.pack(fmt, MAGIC_NACK, count, *missing_seqs[:count])

    @staticmethod
    def pack_ack() -> bytes:
        return MAGIC_ACK


class _P2PTransmitterThread(Thread):
    def __init__(self, target_ip: str, file_path: str, progress_callback=None, finished_callback=None):
        super().__init__()
        self.target_ip = target_ip
        self.file_path = file_path
        self.progress_callback = progress_callback
        self.finished_callback = finished_callback
        self._cancel = Event()

    def cancel(self):
        self._cancel.set()

    def run(self):
        try:
            with open(self.file_path, "rb") as f:
                file_data = f.read()

            chunks = [file_data[i:i+CHUNK_SIZE] for i in range(0, len(file_data), CHUNK_SIZE)]
            total = len(chunks)

            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(TIMEOUT_SEC)

            # Initial Burst
            for seq, payload in enumerate(chunks):
                if self._cancel.is_set(): return
                packet = ANSX_UDP_Protocol.pack_chunk(seq, total, payload)
                sock.sendto(packet, (self.target_ip, UDP_PORT))
                # Slight throttle so we don't drop on localhost/fast wifi due to buffer fills
                time.sleep(0.0001) 

            if self.progress_callback:
                self.progress_callback(50)  # Sent first wave

            retries = 0
            while not self._cancel.is_set() and retries < MAX_RETRIES:
                try:
                    data, addr = sock.recvfrom(65535)
                    if data == MAGIC_ACK:
                        break # Fully Delivered!
                    
                    if data.startswith(MAGIC_NACK):
                        # Decode NACK format
                        struct_len = len(data)
                        count = struct.unpack("!I", data[4:8])[0]
                        fmt = f"!{count}I"
                        missing_seqs = struct.unpack(fmt, data[8:8+count*4])
                        
                        # Retransmit missing packets
                        for seq in missing_seqs:
                            packet = ANSX_UDP_Protocol.pack_chunk(seq, total, chunks[seq])
                            sock.sendto(packet, (self.target_ip, UDP_PORT))
                            time.sleep(0.0001)

                except socket.timeout:
                    retries += 1
                    # Send a pulse of the last chunk to prove we are alive and provoke a NACK
                    packet = ANSX_UDP_Protocol.pack_chunk(total-1, total, chunks[-1])
                    sock.sendto(packet, (self.target_ip, UDP_PORT))

            sock.close()

            success = not self._cancel.is_set() and retries < MAX_RETRIES
            if self.finished_callback:
                self.finished_callback(success)

        except Exception as e:
            logger.error(f"UDP Transmitter Failed: {e}")
            if self.finished_callback:
                self.finished_callback(False)


class UDPCourierSender(QThread):
    progress = pyqtSignal(int)
    finished = pyqtSignal(bool)

    def __init__(self, target_ip: str, file_path: str):
        super().__init__()
        self.target_ip = target_ip
        self.file_path = file_path
        self._thread = None

    def run(self):
        self._thread = _P2PTransmitterThread(
            self.target_ip, self.file_path,
            progress_callback=self.progress.emit,
            finished_callback=self.finished.emit
        )
        self._thread.start()
        self._thread.join()

    def cancel(self):
        if self._thread:
            self._thread.cancel()


class UDPListenerDaemon(QThread):
    """
    Background daemon embedded into main.py that constantly listens for incoming ANSX UDP Ghost Maps.
    """
    incoming_file = pyqtSignal(str) # Path to the reconstructed Ghost Map .png

    def __init__(self):
        super().__init__()
        self._running = True

    def stop(self):
        self._running = False

    def run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # Bind to all interfaces to listen natively
            sock.bind(("0.0.0.0", UDP_PORT))
        except Exception as e:
            logger.error(f"UDP Bind Failed. Port {UDP_PORT} in use? {e}")
            return
            
        sock.settimeout(0.1)

        download_cache = {}    # seq -> payload bytes
        expected_total = -1
        sender_addr = None
        last_packet_time = 0

        while self._running:
            try:
                data, addr = sock.recvfrom(65535)
                unpacked = ANSX_UDP_Protocol.unpack_chunk(data)
                if unpacked:
                    seq, total, payload = unpacked
                    
                    # New incoming file transfer session reset
                    if expected_total == -1 or addr != sender_addr:
                        expected_total = total
                        sender_addr = addr
                        download_cache.clear()
                    
                    download_cache[seq] = payload
                    last_packet_time = time.time()
                    
                    # Check if complete on every packet iteration
                    if len(download_cache) == expected_total:
                        # Full image recreated!
                        sock.sendto(ANSX_UDP_Protocol.pack_ack(), sender_addr)
                        self._process_complete_file(download_cache, expected_total)
                        # Reset for next file
                        expected_total = -1
                        sender_addr = None
                        download_cache.clear()

            except socket.timeout:
                # If we are in the middle of a transfer and it paused for 500ms, send NACK
                if expected_total > 0 and (time.time() - last_packet_time > 0.5):
                    missing = [i for i in range(expected_total) if i not in download_cache]
                    if missing and sender_addr:
                        nack_packet = ANSX_UDP_Protocol.pack_nack(missing)
                        sock.sendto(nack_packet, sender_addr)
                        last_packet_time = time.time() # Reset wait period

        sock.close()

    def _process_complete_file(self, cache: dict, total: int):
        file_bytes = bytearray()
        for i in range(total):
            file_bytes.extend(cache[i])
            
        downloads_dir = os.path.expanduser("~/.ansx_vault/p2p_incoming")
        os.makedirs(downloads_dir, exist_ok=True)
        filename = f"ghost_map_{int(time.time())}.png"
        filepath = os.path.join(downloads_dir, filename)
        
        with open(filepath, "wb") as f:
            f.write(file_bytes)
            
        logger.info(f"[ANSX-UDP] Complete Ghost Map received directly via P2P. Saved to {filepath}")
        self.incoming_file.emit(filepath)
