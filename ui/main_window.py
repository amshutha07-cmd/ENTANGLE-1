"""ui/main_window.py — the application shell: sign-in flow, sidebar, pages, status bar, toasts, tray."""
from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import QEvent, QObject, QTimer
from PyQt6.QtGui import QIcon, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QMainWindow, QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget,
)

import engine
from ui import icons, motion, theme
from ui.controller import AppController, pref, set_pref
from ui.pages.auth import AuthPage
from ui.pages.contacts import ContactsPage
from ui.pages.home import HomePage
from ui.pages.inbox import InboxPage
from ui.pages.send import SendPage
from ui.pages.settings import SettingsPage
from ui.pages.vault import VaultPage
from ui.widgets import Avatar, Banner, Button, ClickablePill, IconBadge, NavButton, Pill, ToastHost, label

logger = logging.getLogger(__name__)
APP_VERSION = "1.0"

NAV = [("home", "Home", "home"), ("vault", "Protect", "shield"), ("send", "Send", "send"),
       ("inbox", "Inbox", "inbox"), ("contacts", "People & keys", "users"), ("settings", "Settings", "settings")]


class _ActivityWatcher(QObject):
    """Resets the idle timer on any real user input."""

    def __init__(self, on_activity):
        super().__init__()
        self._cb = on_activity

    def eventFilter(self, obj, ev):
        if ev.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.KeyPress, QEvent.Type.MouseMove, QEvent.Type.Wheel):
            self._cb()
        return False


class MainWindow(QMainWindow):
    def __init__(self, ctl: Optional[AppController] = None):
        super().__init__()
        self.ctl = ctl or AppController()
        self.setWindowTitle("A.N.Sx Vault")
        self.setMinimumSize(980, 680)
        self.resize(1200, 800)

        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.root_stack = QStackedWidget()
        outer.addWidget(self.root_stack, 1)

        # ── sign-in flow ──
        self.auth = AuthPage(self.ctl)
        self.root_stack.addWidget(self.auth)

        # ── application shell ──
        shell = QWidget()
        shell_v = QVBoxLayout(shell)
        shell_v.setContentsMargins(0, 0, 0, 0)
        shell_v.setSpacing(0)
        body = QHBoxLayout()
        body.setSpacing(0)
        body.addWidget(self._build_sidebar())
        self.content = QStackedWidget()
        body.addWidget(self.content, 1)
        shell_v.addLayout(body, 1)
        shell_v.addWidget(self._build_status_bar())
        self.root_stack.addWidget(shell)

        self.pages = {
            "home": HomePage(self.ctl), "vault": VaultPage(self.ctl), "send": SendPage(self.ctl),
            "inbox": InboxPage(self.ctl), "contacts": ContactsPage(self.ctl), "settings": SettingsPage(self.ctl),
        }
        for page in self.pages.values():
            self.content.addWidget(page)
        self._wire()

        self.toasts = ToastHost(root)
        self._tray = self._make_tray()
        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.timeout.connect(self._idle_lock)
        self._watcher = _ActivityWatcher(self._activity)
        QApplication.instance().installEventFilter(self._watcher)
        for i, (key, text, _i) in enumerate(NAV, start=1):
            seq = QKeySequence(f"Ctrl+{i}")
            QShortcut(seq, self, activated=lambda k=key: self._shortcut(k))
            self.nav[key].setToolTip(f"{text}   {seq.toString(QKeySequence.SequenceFormat.NativeText)}")
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self._lock)
        QShortcut(QKeySequence("Ctrl+O"), self, activated=self._protect_shortcut)

        if self.ctl.operator:                       # attached to a session that is already running: adopt its state
            self._session_started(self.ctl.operator)
            self._set_waiting(len(self.ctl.inbox))
            self._relay_state(self.ctl.relay_state, self.ctl.relay_message)
        else:
            self.show_auth()

    # ── construction helpers ─────────────────────────────────────────────────
    def _build_sidebar(self) -> QFrame:
        side = QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(232)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(14, 20, 14, 14)
        lay.setSpacing(4)
        brand = QHBoxLayout()
        brand.setContentsMargins(6, 0, 0, 14)
        brand.addWidget(IconBadge("shield", "primary", 36))
        name = label("A.N.Sx Vault", "h2", wrap=False)
        brand.addWidget(name, 1)
        lay.addLayout(brand)
        self.nav: dict[str, NavButton] = {}
        for key, text, icon in NAV:
            b = NavButton(text, icon)
            b.clicked.connect(lambda _c, k=key: self.go(k))
            lay.addWidget(b)
            self.nav[key] = b
        lay.addStretch(1)
        chip = QFrame()
        chip.setObjectName("CardAlt")
        cl = QHBoxLayout(chip)
        cl.setContentsMargins(10, 10, 8, 10)
        cl.setSpacing(10)
        self.chip_avatar = Avatar("?", 34)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.chip_name = label("", "body", wrap=False)
        self.chip_name.setStyleSheet("font-weight: 650;")
        self.chip_state = label("Unlocked", "faint", wrap=False)
        col.addWidget(self.chip_name)
        col.addWidget(self.chip_state)
        lock = Button("", "ghost", "lock", "sm")
        lock.setToolTip("Lock (Ctrl+L)")
        lock.clicked.connect(self._lock)
        cl.addWidget(self.chip_avatar)
        cl.addLayout(col, 1)
        cl.addWidget(lock)
        lay.addWidget(chip)
        return side

    def _build_status_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("StatusBar")
        bar.setFixedHeight(38)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(10)
        self.relay_pill = ClickablePill("Not connected", "neutral")
        self.storage_pill = ClickablePill("", "neutral")
        for pill in (self.relay_pill, self.storage_pill):          # a status you can see is a status you can fix
            pill.clicked.connect(lambda: self.root_stack.currentIndex() == 1 and self.go("settings"))
        self.engine_banner_pill = Pill("", "danger")
        self.engine_banner_pill.hide()
        lay.addWidget(self.relay_pill)
        lay.addWidget(self.storage_pill)
        lay.addWidget(self.engine_banner_pill)
        lay.addStretch(1)
        lay.addWidget(label(f"v{APP_VERSION}", "faint", wrap=False))
        return bar

    def _make_tray(self) -> Optional[QSystemTrayIcon]:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        tray = QSystemTrayIcon(QIcon(icons.pixmap("shield", theme.color("primary"), 32)), self)
        tray.setToolTip("A.N.Sx Vault")
        tray.show()
        return tray

    def _wire(self) -> None:
        ctl = self.ctl
        for key in ("home", "vault", "send", "inbox", "contacts", "settings"):
            page = self.pages[key]
            if hasattr(page, "navigate"):
                page.navigate.connect(self.go)
        self.pages["vault"].send_requested.connect(self._send_entry)
        self.pages["settings"].theme_changed.connect(self.set_theme)
        self.pages["settings"].lock_requested.connect(self._lock)
        self.pages["settings"].autolock_changed.connect(lambda _m: self._activity())
        ctl.session_started.connect(self._session_started)
        ctl.session_ended.connect(self._session_ended)
        ctl.toast.connect(self.toasts_show)
        ctl.new_transfers.connect(self._new_transfers)
        ctl.inbox_changed.connect(lambda items: self._set_waiting(len(items)))
        ctl.relay_state_changed.connect(self._relay_state)
        ctl.vault_changed.connect(self._storage_pill)

    # ── navigation ───────────────────────────────────────────────────────────
    def go(self, key: str) -> None:
        tab = None
        if key == "sent":
            key, tab = "inbox", "sent"
        page = self.pages[key]
        changed = self.content.currentWidget() is not page
        self.content.setCurrentWidget(page)
        if changed:
            motion.fade_in(page, motion.FAST)
        for k, b in self.nav.items():
            b.setChecked(k == key)
        page.on_show()
        if tab:
            page.show_tab(tab)
        elif key == "inbox":
            page.show_tab("received")

    def _set_waiting(self, n: int) -> None:
        """Inbox count on the sidebar badge and in the window title (seen in the taskbar / window list)."""
        self.nav["inbox"].set_badge(n)
        self.setWindowTitle(f"A.N.Sx Vault ({n} waiting)" if n else "A.N.Sx Vault")

    def _shortcut(self, key: str) -> None:
        if self.root_stack.currentIndex() == 1:
            self.go(key)

    def _protect_shortcut(self) -> None:
        """Ctrl/⌘+O: straight to choosing a file to protect."""
        if self.root_stack.currentIndex() == 1:
            self.go("vault")
            self.pages["vault"].drop.choose()

    def _send_entry(self, entry_id: str) -> None:
        self.pages["send"].preselect(entry_id)
        self.go("send")

    # ── session lifecycle ────────────────────────────────────────────────────
    def show_auth(self) -> None:
        self.root_stack.setCurrentIndex(0)
        self.auth.show_login()

    def _session_started(self, name: str) -> None:
        self.chip_name.setText(name)
        self.chip_avatar.set_name(name)
        self.root_stack.setCurrentIndex(1)
        motion.fade_in(self.root_stack.currentWidget(), motion.NORMAL)
        self._storage_pill()
        self._engine_check()
        self.go("home")
        self._activity()
        self.ctl.run_job(self.ctl.make_directory_job())          # quietly learn who is on the relay

    def _session_ended(self) -> None:
        self._idle.stop()
        for page in self.pages.values():
            page.on_session_ended()
        self._set_waiting(0)
        self.show_auth()

    def _lock(self) -> None:
        if self.root_stack.currentIndex() == 1:
            self.ctl.logout()

    def _idle_lock(self) -> None:
        if self.ctl.operator:
            self.ctl.logout()
            self.toasts_show("Locked after a period of inactivity.", "info")

    def _activity(self) -> None:
        if not self.ctl.operator:
            return
        mins = int(pref("auto_lock_minutes", 10) or 0)
        if mins > 0:
            self._idle.start(mins * 60 * 1000)
        else:
            self._idle.stop()

    # ── status / feedback ────────────────────────────────────────────────────
    def toasts_show(self, message: str, kind: str = "info") -> None:
        self.toasts.show_toast(message, kind)

    def _relay_state(self, state: str, message: str) -> None:
        text, kind = {"online": ("Online", "success"), "connecting": ("Connecting…", "info"),
                      "offline": ("Offline", "warning"), "conflict": ("Name conflict", "danger"),
                      "idle": ("Not connected", "neutral")}.get(state, (state, "neutral"))
        self.relay_pill.set(f"● {text}" if state != "idle" else text, kind)
        self.relay_pill.setToolTip(f"{message}\nClick to open connection settings." if message
                                   else "Click to open connection settings.")
        motion.breathe(self.relay_pill, state == "connecting")

    def _storage_pill(self) -> None:
        n = len(self.ctl.storage_targets())
        self.storage_pill.set(f"Cloud storage: {n}" if n else "Storage: inside package", "success" if n else "neutral")
        self.storage_pill.setToolTip("Click to manage cloud storage.")

    def _engine_check(self) -> None:
        ok = engine.available()
        self.engine_banner_pill.setVisible(not ok)
        if not ok:
            self.engine_banner_pill.set("Encryption engine missing — run build_engine.py", "danger")

    def _new_transfers(self, items: list) -> None:
        names = ", ".join(sorted({i["from"] for i in items}))
        self.toasts.show_toast(f"New file from {names}. Click here to open your inbox.", "info", 7000,
                               on_click=lambda: self.root_stack.currentIndex() == 1 and self.go("inbox"))
        QApplication.alert(self)
        if self._tray and not self.isActiveWindow():
            self._tray.showMessage("A.N.Sx Vault", f"New file from {names}", QSystemTrayIcon.MessageIcon.Information, 6000)

    # ── theme ────────────────────────────────────────────────────────────────
    def set_theme(self, name: str) -> None:
        theme.apply(QApplication.instance(), name)
        set_pref("theme", name)
        self.retheme()

    def retheme(self) -> None:
        for cls in (IconBadge,):
            for w in self.findChildren(cls):
                w.refresh()
        for w in self.findChildren(Button):
            w.refresh_icon()
        for w in self.findChildren(NavButton):
            w.refresh_icon()
        for w in self.findChildren(Banner):
            w.refresh()
        if self._tray:
            self._tray.setIcon(QIcon(icons.pixmap("shield", theme.color("primary"), 32)))
        page = self.content.currentWidget()
        if self.root_stack.currentIndex() == 1 and page is not None:
            page.on_show()

    # ── shutdown ─────────────────────────────────────────────────────────────
    def closeEvent(self, event) -> None:
        try:
            self.ctl.logout()
        finally:
            if self._tray:
                self._tray.hide()
            super().closeEvent(event)
