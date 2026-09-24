"""
transfer.py — A.N.Sx Vault | background polling of the relay inbox and outbox.

Sending and receiving themselves are controller jobs (ui/controller.py); all network logic lives in
RelayClient (tested against a real relay). This thread only keeps the lists fresh and announces new arrivals.
"""
from __future__ import annotations

import logging
import os
import time

from PyQt6.QtCore import QThread, pyqtSignal

from relay_client import RelayClient, RelayError

logger = logging.getLogger(__name__)

def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


class SyncPoller(QThread):
    """Polls inbox + outbox. Emits only on change; backs off (up to 60 s) while the relay is unreachable."""

    inbox_updated = pyqtSignal(list)
    outbox_updated = pyqtSignal(list)
    new_items = pyqtSignal(list)                 # inbox items not seen before in this session
    status = pyqtSignal(str)

    def __init__(self, client: RelayClient, interval: float = 8.0, on_start=None):
        super().__init__()
        self._client, self._interval, self._on_start = client, interval, on_start
        self._running = True
        self._seen: set[str] = set()
        self._last_in: list | None = None
        self._last_out: list | None = None

    def stop(self) -> None:
        self._running = False

    def poll_once(self) -> None:
        inbox, outbox = self._client.inbox(), self._client.outbox()
        fresh = [i for i in inbox if i["id"] not in self._seen]
        self._seen.update(i["id"] for i in inbox)
        if inbox != self._last_in:
            self._last_in = inbox
            self.inbox_updated.emit(inbox)
        if outbox != self._last_out:
            self._last_out = outbox
            self.outbox_updated.emit(outbox)
        if fresh:
            self.new_items.emit(fresh)

    def run(self) -> None:
        if self._on_start:                       # e.g. make sure this identity exists on the relay
            try:
                result = self._on_start()
                if result == "conflict":
                    self.status.emit("⚠ The relay already holds a DIFFERENT key for your name — sending/receiving disabled")
                    return
            except Exception as exc:             # never let a hook kill the poller
                logger.warning("start hook failed: %s", exc)
        delay, failing = self._interval, False
        while self._running:
            try:
                self.poll_once()
                if failing:
                    self.status.emit("● Relay connected")
                failing, delay = False, self._interval
                if self._last_in is not None and not getattr(self, "_announced", False):
                    self._announced = True
                    self.status.emit("● Relay connected")
            except RelayError as exc:
                failing = True
                delay = min(delay * 2, 60)
                self.status.emit(f"⚠ Relay unreachable ({exc.detail[:60]}) — retrying in {int(delay)}s")
            end = time.time() + delay
            while self._running and time.time() < end:
                time.sleep(0.25)
