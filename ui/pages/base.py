"""ui/pages/base.py — every screen is a scrollable page with a title, optional action buttons and content."""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from ui.widgets import Banner, SectionHeader


class Page(QScrollArea):
    def __init__(self, ctl, title: str, subtitle: str = ""):
        super().__init__()
        self.ctl = ctl
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("Root")
        self.setWidget(inner)
        self.root = QVBoxLayout(inner)
        self.root.setContentsMargins(36, 30, 36, 30)
        self.root.setSpacing(18)
        head = QHBoxLayout()
        self.header = SectionHeader(title, subtitle)
        head.addWidget(self.header, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        head.addLayout(self.actions)
        self.root.addLayout(head)

    def refresh_while_visible(self, fn, seconds: int = 60) -> QTimer:
        """Re-run fn every `seconds` while this page is on screen (keeps "5 min ago" style times true)."""
        self._periodic = getattr(self, "_periodic", []) + [fn]
        t = QTimer(self)
        t.timeout.connect(self._run_periodic)                # a bound method: gone with the page, never dangling
        t.start(seconds * 1000)
        return t

    def _run_periodic(self) -> None:
        if self.isVisible():
            for fn in getattr(self, "_periodic", []):
                fn()

    def add_connection_banner(self, texts: dict) -> Banner:
        """
        A notice under the page title while the relay can't be used, e.g. {"offline": "…", "conflict": "…"}.
        It keeps itself up to date and offers a way to the connection settings.
        """
        nav = getattr(self, "navigate", None)
        self._conn_texts = texts
        self.conn_banner = Banner("", "warning", "Connection settings", (lambda: nav.emit("settings")) if nav else None)
        self.conn_banner.hide()
        self.root.insertWidget(1, self.conn_banner)
        # Bound method, not a lambda: Qt disconnects it when this page is destroyed. The controller outlives pages,
        # and a lambda would keep calling into a deleted page (a crash, not just an error).
        self.ctl.relay_state_changed.connect(self._relay_state_changed)
        return self.conn_banner

    def _relay_state_changed(self, state: str, _message: str) -> None:
        self._show_connection(state)

    def _show_connection(self, state: str) -> None:
        text = self._conn_texts.get(state, "")
        if text:
            self.conn_banner.set(text, "danger" if state == "conflict" else "warning")
        self.conn_banner.setVisible(bool(text))

    def notify_if_away(self, text: str, kind: str) -> None:
        """
        A toast for a job that finished while the person was on ANOTHER page. On this page the result is already shown
        in place, and a second copy of the same message in the corner is just noise.
        """
        if not self.isVisible():
            self.ctl.toast.emit(text, kind)

    def on_show(self) -> None:
        """Called each time the page becomes visible; refresh anything that may have changed."""

    def on_session_ended(self) -> None:
        """Called when the vault locks; clear anything sensitive."""
