"""
tcp_courier.py — A.N.Sx Vault | TCP WebSocket File Transfer
============================================================

Works from ANYWHERE in the world — no port forwarding, no public IP needed.
Both sender and receiver connect to the ANSX relay over TCP (WebSocket).
The relay bridges them in real-time. Pure streaming, no file stored on server.

SEND FLOW:
  1. Sender clicks SEND → UI shows a 6-char session code (e.g. "A3XK9Q")
  2. Sender's machine connects to relay as "sender"
  3. Receiver types that code into their app → connects as "receiver"
  4. Relay bridges both TCP connections
  5. Sender streams file bytes → receiver reassembles → done

RECEIVE FLOW:
  1. User enters session code shown by sender
  2. TCPCourierReceiver connects to relay as "receiver"
  3. Waits for sender → receives binary frames → saves file
"""

from __future__ import annotations

import logging
import os
import secrets
import struct
import threading
import time

from PyQt6.QtCore import QThread, pyqtSignal

import web3_bridge

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

CHUNK_SIZE   = 32 * 1024          # 32 KB chunks
DOWNLOAD_DIR = os.path.expanduser("~/.ansx_vault/p2p_incoming")
RELAY_URL    = web3_bridge.RELAY_URL   # e.g. https://ansxvault.onrender.com

# Convert https:// → wss://, http:// → ws://
def _ws_url(session_id: str, role: str) -> str:
    base = RELAY_URL.replace("https://", "wss://").replace("http://", "ws://")
    return f"{base}/v1/tcp/{session_id}/{role}"


def generate_session_code() -> str:
    """Generate a short human-readable 6-char session code."""
    return secrets.token_hex(3).upper()   # e.g. "A3XK9Q"


# ═══════════════════════════════════════════════════════════════════════════════
# SENDER
# ═══════════════════════════════════════════════════════════════════════════════

class TCPCourierSender(QThread):
    """
    Connects to the relay as 'sender', waits for receiver to join,
    then streams the file in 32 KB chunks over the WebSocket (TCP).

    Signals:
        status(str)         — human-readable status text for UI label
        progress(int)       — 0–100
        finished(bool, str) — (success, message)
    """

    status   = pyqtSignal(str)
    progress = pyqtSignal(int)
    finished = pyqtSignal(bool, str)

    def __init__(self, session_id: str, file_path: str):
        super().__init__()
        self.session_id = session_id
        self.file_path  = file_path
        self._cancel    = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            self._run_inner()
        except Exception as exc:
            logger.exception("[TCPSender] Unhandled error")
            self.finished.emit(False, f"Error: {exc}")

    def _run_inner(self):
        import websocket   # websocket-client (sync)

        url = _ws_url(self.session_id, "sender")
        self.status.emit(f"Connecting to relay…\nSession: {self.session_id}")
        self.progress.emit(2)

        ws = websocket.WebSocket()
        ws.connect(url, timeout=15)

        self.status.emit(f"Waiting for receiver to join…\nShare code: {self.session_id}")
        self.progress.emit(5)

        # Wait for READY signal (server sends WAITING first, then READY)
        while not self._cancel:
            msg = ws.recv()
            if isinstance(msg, str):
                if msg == "READY":
                    break
                if msg.startswith("ERROR"):
                    self.finished.emit(False, f"Relay error: {msg}")
                    ws.close()
                    return
            # keep waiting

        if self._cancel:
            ws.close()
            self.finished.emit(False, "Cancelled.")
            return

        self.status.emit("Receiver connected! Streaming file…")

        # Send file metadata first (JSON text frame)
        import json
        meta = json.dumps({
            "filename": os.path.basename(self.file_path),
            "filesize": os.path.getsize(self.file_path),
        })
        ws.send(meta)

        # Stream file in chunks
        total_size = os.path.getsize(self.file_path)
        sent = 0

        with open(self.file_path, "rb") as f:
            while not self._cancel:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                ws.send_binary(chunk)
                sent += len(chunk)
                pct = int((sent / total_size) * 95) + 3
                self.progress.emit(min(pct, 98))
                self.status.emit(
                    f"Sending… {sent // 1024} KB / {total_size // 1024} KB"
                )

        if self._cancel:
            ws.close()
            self.finished.emit(False, "Cancelled.")
            return

        # Signal end of file
        ws.send("EOF")
        self.progress.emit(100)
        ws.close()
        self.finished.emit(True, f"File sent successfully to receiver.")


# ═══════════════════════════════════════════════════════════════════════════════
# RECEIVER
# ═══════════════════════════════════════════════════════════════════════════════

class TCPCourierReceiver(QThread):
    """
    Connects to the relay as 'receiver' using the session code from sender.
    Receives binary frames, reassembles file, emits incoming_file(path).

    Signals:
        status(str)            — human-readable status for UI label
        progress(int)          — 0–100
        incoming_file(str)     — path of saved file when complete
        finished(bool, str)    — (success, message)
    """

    status        = pyqtSignal(str)
    progress      = pyqtSignal(int)
    incoming_file = pyqtSignal(str)
    finished      = pyqtSignal(bool, str)

    def __init__(self, session_id: str):
        super().__init__()
        self.session_id = session_id
        self._cancel    = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            self._run_inner()
        except Exception as exc:
            logger.exception("[TCPReceiver] Unhandled error")
            self.finished.emit(False, f"Error: {exc}")

    def _run_inner(self):
        import websocket
        import json

        url = _ws_url(self.session_id, "receiver")
        self.status.emit(f"Connecting to relay…\nSession: {self.session_id}")
        self.progress.emit(2)

        ws = websocket.WebSocket()
        ws.connect(url, timeout=15)

        self.status.emit("Connected. Waiting for sender to start…")
        self.progress.emit(5)

        # Wait for READY
        while not self._cancel:
            msg = ws.recv()
            if isinstance(msg, str):
                if msg == "READY":
                    break
                if msg.startswith("ERROR"):
                    self.finished.emit(False, f"Relay error: {msg}")
                    ws.close()
                    return

        if self._cancel:
            ws.close()
            self.finished.emit(False, "Cancelled.")
            return

        # Receive metadata (first text frame after READY)
        meta_raw = ws.recv()
        if isinstance(meta_raw, bytes):
            meta_raw = meta_raw.decode()

        try:
            meta      = json.loads(meta_raw)
            filename  = meta.get("filename", f"received_{int(time.time())}.bin")
            filesize  = meta.get("filesize", 0)
        except Exception:
            filename  = f"ghost_map_{int(time.time())}.png"
            filesize  = 0

        self.status.emit(f"Receiving: {filename}\n({filesize // 1024} KB)")

        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        save_path = os.path.join(DOWNLOAD_DIR, f"tcp_{self.session_id}_{filename}")

        received = 0
        with open(save_path, "wb") as out:
            while not self._cancel:
                msg = ws.recv()
                if isinstance(msg, str):
                    if msg in ("DONE", "EOF"):
                        break
                    if msg.startswith("ERROR"):
                        self.finished.emit(False, f"Transfer error: {msg}")
                        ws.close()
                        return
                    continue
                # binary chunk
                out.write(msg)
                received += len(msg)
                if filesize > 0:
                    pct = int((received / filesize) * 95) + 3
                    self.progress.emit(min(pct, 98))
                self.status.emit(
                    f"Receiving… {received // 1024} KB"
                    + (f" / {filesize // 1024} KB" if filesize else "")
                )

        ws.close()

        if self._cancel:
            self.finished.emit(False, "Cancelled.")
            return

        self.progress.emit(100)
        logger.info("[TCPReceiver] File saved → %s", save_path)
        self.incoming_file.emit(save_path)
        self.finished.emit(True, f"File received: {filename}")
