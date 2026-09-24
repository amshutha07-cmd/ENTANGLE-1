"""ui/pages/home.py — the landing page: what can I do, is everything working, what happened recently."""
from __future__ import annotations

import datetime

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

import platform_secret
from ui import icons, theme
from ui.pages.base import Page
from ui.widgets import clear_layout, Banner, Button, Card, ClickableCard, EmptyState, IconBadge, Pill, label, time_ago

ACTIVITY_STYLE = {
    "protected": ("shield", "success"), "sent": ("send", "primary"), "delivered": ("check", "success"),
    "received": ("download", "success"), "declined": ("x", "warning"), "restored": ("unlock", "info"),
    "security": ("key", "neutral"), "error": ("alert", "danger"),
}


class ActionCard(ClickableCard):
    def __init__(self, icon: str, title: str, text: str):
        super().__init__(padding=18, spacing=10)
        row = QHBoxLayout()
        self.badge = IconBadge(icon, "primary", 44)
        row.addWidget(self.badge)
        row.addStretch()
        self.pill = Pill("", "primary")
        self.pill.hide()
        row.addWidget(self.pill)
        self.body.addLayout(row)
        self.body.addWidget(label(title, "h2", wrap=False))
        self.body.addWidget(label(text, "muted"))


class StatusTile(Card):
    def __init__(self, icon: str, eyebrow: str):
        super().__init__(padding=18, spacing=6)
        top = QHBoxLayout()
        self.badge = IconBadge(icon, "neutral", 36)
        top.addWidget(self.badge)
        top.addWidget(label(eyebrow, "eyebrow", wrap=False), 1)
        self.body.addLayout(top)
        self.value = label("—", "h2", wrap=False)
        self.detail = label("", "muted")
        self.body.addWidget(self.value)
        self.body.addWidget(self.detail)
        self.action = Button("", "secondary", size="sm")
        self.action.hide()
        self.body.addWidget(self.action, 0, Qt.AlignmentFlag.AlignLeft)
        self.body.addStretch()

    def set(self, value: str, detail: str, kind: str, icon: str = "", action: str = "") -> None:
        self.value.setText(value)
        self.detail.setText(detail)
        self.badge.set(icon or self.badge._name, kind)
        self.action.setVisible(bool(action))
        self.action.setText(action)


def _greeting() -> str:
    h = datetime.datetime.now().hour
    return "Good morning" if h < 12 else "Good afternoon" if h < 18 else "Good evening"


class HomePage(Page):
    navigate = pyqtSignal(str)

    def __init__(self, ctl):
        super().__init__(ctl, "Home", "")
        self.card_protect = ActionCard("shield", "Protect a file", "Encrypt it and keep it safe, ready to send.")
        self.card_send = ActionCard("send", "Send a file", "Only the person you choose can open it.")
        self.card_inbox = ActionCard("inbox", "Inbox", "Files people have sent you.")
        row = QHBoxLayout()
        row.setSpacing(14)
        for c, key in ((self.card_protect, "vault"), (self.card_send, "send"), (self.card_inbox, "inbox")):
            row.addWidget(c, 1)
            c.clicked.connect(lambda k=key: self.navigate.emit(k))
        self.root.addLayout(row)

        self.checklist = Card(padding=18, spacing=8)
        self.check_rows: dict[str, QLabel] = {}
        self.checklist.body.addWidget(label("Finish setting up", "h2", wrap=False))
        self.checklist.body.addWidget(label("A couple of quick steps and everything works end to end.", "muted"))
        for key, text in (("relay", "Connect to a relay so people can reach you"),
                          ("storage", "Add cloud storage for larger files (recommended)"),
                          ("verify", "Verify a friend's key so you know it's really them")):
            r = QHBoxLayout()
            ic = QLabel()
            self.check_rows[key] = ic
            r.addWidget(ic)
            r.addWidget(label(text, "body"), 1)
            self.checklist.body.addLayout(r)
        btns = QHBoxLayout()
        self.setup_btn = Button("Open settings", "primary", "settings", "sm")
        self.setup_btn.clicked.connect(lambda: self.navigate.emit("settings"))
        btns.addWidget(self.setup_btn)
        btns.addStretch()
        self.checklist.body.addLayout(btns)
        self.root.addWidget(self.checklist)

        tiles = QHBoxLayout()
        tiles.setSpacing(14)
        self.tile_relay = StatusTile("wifi", "CONNECTION")
        self.tile_storage = StatusTile("cloud", "CLOUD STORAGE")
        self.tile_security = StatusTile("shield", "SECURITY")
        for t in (self.tile_relay, self.tile_storage, self.tile_security):
            tiles.addWidget(t, 1)
        self.tile_relay.action.clicked.connect(lambda: self.navigate.emit("settings"))
        self.tile_storage.action.clicked.connect(lambda: self.navigate.emit("settings"))
        self.root.addLayout(tiles)

        self.root.addWidget(label("RECENT ACTIVITY", "eyebrow", wrap=False))
        self.activity = Card(padding=6, spacing=0)
        self.root.addWidget(self.activity)
        self.root.addStretch(1)

        ctl.relay_state_changed.connect(lambda *_: self.refresh())
        ctl.inbox_changed.connect(lambda *_: self.refresh())
        ctl.activity_changed.connect(self.refresh)
        ctl.vault_changed.connect(self.refresh)
        ctl.contacts_changed.connect(self.refresh)

    def on_show(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        ctl = self.ctl
        name = ctl.operator or ""
        self.header.title.setText(f"{_greeting()}, {name}" if name else "Home")

        n = len(ctl.inbox)
        self.card_inbox.pill.setVisible(bool(n))
        self.card_inbox.pill.set(f"{n} waiting", "primary")

        state, msg = ctl.relay_state, ctl.relay_message
        rt = self.tile_relay
        if state == "online":
            rt.set("Online", "You can send and receive files.", "success", "check")
        elif state == "connecting":
            rt.set("Connecting…", "Reaching the relay.", "info", "wifi")
        elif state == "conflict":
            rt.set("Name conflict", msg, "danger", "alert", "Open settings")
        elif state == "offline":
            rt.set("Offline", msg or "Cannot reach the relay.", "warning", "alert", "Check relay")
        else:
            rt.set("Not connected", "", "neutral", "wifi")

        targets = ctl.storage_targets()
        if targets:
            names = ", ".join(t["name"] for t in targets[:3])
            self.tile_storage.set(f"{len(targets)} account{'s' if len(targets) != 1 else ''}", names, "success", "cloud")
        else:
            self.tile_storage.set("Not set up", "Pieces are kept inside the package. Files up to 10 MB.", "warning", "cloud", "Set up storage")

        mode = ctl.auth_mode(name) if name else "nfc"
        try:
            backend = platform_secret.backend_name()
        except Exception:
            backend = "file"
        where = {"tpm2": "your computer's TPM chip", "keyring": "your system's secure storage",
                 "file": "a protected file (weaker)"}.get(backend, backend)
        self.tile_security.set("Card unlock" if mode == "nfc" else "Passphrase unlock",
                               f"Your keys are protected by {where}.", "success" if backend != "file" else "warning", "shield")

        verified = any(c.get("verified") for c in ctl.contacts())
        done = {"relay": state == "online", "storage": bool(targets), "verify": verified}
        for key, ic in self.check_rows.items():
            ic.setPixmap(icons.pixmap("check" if done[key] else "clock", theme.color("success" if done[key] else "text_faint"), 18))
        self.checklist.setVisible(not (done["relay"] and done["storage"]))

        self._fill_activity()

    def _fill_activity(self) -> None:
        lay = self.activity.body
        clear_layout(lay)
        items = __import__("activity").recent(8, operator=self.ctl.operator or "")
        if not items:
            lay.addWidget(EmptyState("activity", "Nothing yet", "Protect or send your first file and it will show up here."))
            return
        for it in items:
            icon, kind = ACTIVITY_STYLE.get(it["kind"], ("info", "neutral"))
            row = QWidget()
            hl = QHBoxLayout(row)
            hl.setContentsMargins(12, 9, 12, 9)
            hl.setSpacing(12)
            hl.addWidget(IconBadge(icon, kind, 34))
            col = QVBoxLayout()
            col.setSpacing(0)
            col.addWidget(label(it["title"], "body", wrap=False))
            if it.get("detail"):
                col.addWidget(label(it["detail"], "muted", wrap=False))
            hl.addLayout(col, 1)
            hl.addWidget(label(time_ago(it["ts"]), "faint", wrap=False))
            lay.addWidget(row)
