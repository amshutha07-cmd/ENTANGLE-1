"""
single_instance.py — one running copy of the app per vault folder.

Two copies on the same vault would each poll the relay and write the same settings / vault files, and one would
silently overwrite the other's changes. A second launch therefore just asks the running copy to come to the front,
then exits. The channel is a local socket readable only by this user, and the only message it understands is "show".
"""
from __future__ import annotations

import hashlib
import os
from typing import Callable, Optional

from PyQt6.QtNetwork import QLocalServer, QLocalSocket


def server_name(vault_home: str) -> str:
    """One name per vault folder (so separate profiles / test folders never collide)."""
    return "ansx-vault-" + hashlib.sha256(os.path.abspath(vault_home).encode()).hexdigest()[:16]


def notify_running(name: str, timeout_ms: int = 400) -> bool:
    """True if a copy is already running for this vault (it has been asked to show itself)."""
    sock = QLocalSocket()
    sock.connectToServer(name)
    if not sock.waitForConnected(timeout_ms):
        return False
    sock.write(b"show\n")
    sock.flush()
    sock.waitForBytesWritten(timeout_ms)
    sock.disconnectFromServer()
    return True


def listen(name: str, on_show: Callable[[], None]) -> Optional[QLocalServer]:
    """Accept "show" requests from later launches. Returns the server (keep a reference), or None if unavailable."""
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)     # this user only
    if not server.listen(name):
        QLocalServer.removeServer(name)                   # a socket left behind by a copy that crashed
        if not server.listen(name):
            return None

    def accept() -> None:
        while server.hasPendingConnections():
            conn = server.nextPendingConnection()

            def read(c=conn) -> None:
                if b"show" in bytes(c.readAll()):
                    on_show()
            conn.readyRead.connect(read)
            conn.disconnected.connect(conn.deleteLater)
            if conn.bytesAvailable():                     # the message may have arrived before we connected
                read()
    server.newConnection.connect(accept)
    return server
