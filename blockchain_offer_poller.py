"""
blockchain_offer_poller.py — A.N.Sx Vault | Background Blockchain Poller
=========================================================================

Runs silently in the background after login.
Every 5 seconds it calls SessionBroker.getActiveOffers(my_eth_address).
When a new unclaimed offer is found, it emits offer_received(sender, session_code)
which the UI handles by auto-connecting to the TCP relay.

No user action needed. The receiver just opens the app and waits.
"""

from __future__ import annotations

import logging
import time

from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)

POLL_INTERVAL = 5   # seconds between blockchain polls


class BlockchainOfferPoller(QThread):
    """
    Background thread that polls SessionBroker.sol on Polygon Amoy every 5s.

    Signals:
        offer_received(sender: str, session_code: str, offer_index: int)
            — emitted when a new unclaimed, unexpired offer is found on-chain.

        status_changed(str)
            — optional status text for a sidebar indicator in the UI.
    """

    offer_received = pyqtSignal(str, str, int)   # sender_username, session_code, offer_index
    status_changed = pyqtSignal(str)

    def __init__(self, username: str):
        super().__init__()
        self.username  = username
        self._running  = True
        self._seen     = set()   # set of (sender, session_code) tuples already emitted

    def stop(self):
        self._running = False

    def run(self):
        logger.info("[OfferPoller] Started for operator '%s' (poll every %ds)", self.username, POLL_INTERVAL)

        # Lazy import so the poller can start even if web3 is slow to initialise
        try:
            from session_broker import get_session_broker
            broker = get_session_broker()
        except Exception as e:
            logger.error("[OfferPoller] Failed to load SessionBroker: %s", e)
            self.status_changed.emit("⚠ Blockchain unavailable")
            return

        if not broker.is_connected:
            self.status_changed.emit("⚠ Not connected to Amoy")
            logger.warning("[OfferPoller] SessionBroker not connected. Poller inactive.")
            return

        self.status_changed.emit("● Listening on Polygon Amoy…")

        while self._running:
            try:
                offers = broker.poll_active_offers(self.username)
                for offer in offers:
                    key = (offer["sender"], offer["session_code"])
                    if key not in self._seen:
                        self._seen.add(key)
                        logger.info(
                            "[OfferPoller] New offer: sender='%s' code='%s' idx=%d",
                            offer["sender"], offer["session_code"], offer["index"]
                        )
                        self.offer_received.emit(
                            offer["sender"],
                            offer["session_code"],
                            offer["index"],
                        )

            except Exception as e:
                logger.error("[OfferPoller] Poll error: %s", e)

            # Sleep in 0.5s steps so stop() is responsive
            for _ in range(POLL_INTERVAL * 2):
                if not self._running:
                    return
                time.sleep(0.5)

        logger.info("[OfferPoller] Stopped for operator '%s'.", self.username)
