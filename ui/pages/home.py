"""ui/pages/home.py — the landing page: what can I do, is everything working, what happened recently."""
from __future__ import annotations

import datetime

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

import platform_secret
from ui import icons, motion, theme
from ui.pages.base import Page
from ui.widgets import (
    Button, Card, ClickableCard, ElidedLabel, IconBadge, Pill, ProgressRing, Sparkline, clear_layout, label, time_ago,
)

ACTIVITY_STYLE = {
    "protected": ("shield", "success"), "sent": ("send", "primary"), "delivered": ("check", "success"),
    "received": ("download", "success"), "declined": ("x", "warning"), "restored": ("unlock", "info"),
    "security": ("key", "neutral"), "error": ("alert", "danger"),
}


ACTIVITY_LINK = {"protected": "vault", "restored": "vault", "sent": "sent", "delivered": "sent", "declined": "sent",
                 "received": "inbox"}
LINK_TIP = {"vault": "Open Protect", "sent": "See what you sent", "inbox": "Open your inbox"}


class _ActivityRow(QWidget):
    """A line in Recent activity that leads to where it happened."""
    clicked = pyqtSignal()

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.pos()):
            self.clicked.emit()

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
        else:
            super().keyPressEvent(e)


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
        head = QHBoxLayout()
        head.setSpacing(8)
        self.title = label(title, "h2", wrap=False)
        self.arrow = label("→", "mono", wrap=False)       # "this goes somewhere", while pointed at or focused
        self.arrow.setStyleSheet("font-size: 16px; font-weight: 700;")
        self.arrow.hide()
        head.addWidget(self.title)
        head.addWidget(self.arrow)
        head.addStretch(1)
        self.body.addLayout(head)
        self.default_text = text
        self.text = label(text, "muted")                  # replaced by live status when there is some
        self.body.addWidget(self.text)

    def enterEvent(self, e) -> None:
        self.arrow.show()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        if not self.hasFocus():
            self.arrow.hide()
        super().leaveEvent(e)

    def focusInEvent(self, e) -> None:
        self.arrow.show()
        super().focusInEvent(e)

    def focusOutEvent(self, e) -> None:
        if not self.underMouse():
            self.arrow.hide()
        super().focusOutEvent(e)


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


def _names(names: list) -> str:
    """sam · sam and alex · sam, alex and 2 others"""
    if len(names) <= 2:
        return " and ".join(names)
    rest = len(names) - 2
    return f"{names[0]}, {names[1]} and {rest} other{'s' if rest != 1 else ''}"


def _day_heading(ts: int) -> str:
    day = datetime.date.fromtimestamp(ts)
    today = datetime.date.today()
    if day == today:
        return "TODAY"
    if day == today - datetime.timedelta(days=1):
        return "YESTERDAY"
    return day.strftime("%a %d %b").upper()


def _greeting() -> str:
    h = datetime.datetime.now().hour
    return "Good morning" if h < 12 else "Good afternoon" if h < 18 else "Good evening"


class KeyChip(QPushButton):
    """Your key fingerprint, short like a wallet address. A click copies all of it, to read to someone verifying you."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("chip", "key")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._fp = ""
        self.clicked.connect(self._copy)
        self.hide()

    def set_fingerprint(self, fp: str) -> None:
        changed = fp != self._fp
        self._fp = fp
        groups = fp.split()
        self._short = f"{groups[0]} … {groups[-1]}" if len(groups) > 2 else fp
        self.setIcon(icons.icon("key", theme.color("primary"), 16))      # re-coloured on every refresh (theme)
        self.setToolTip("Your key fingerprint. Click to copy all of it, to read to someone who wants to verify you.")
        self.setAccessibleName(f"Your key fingerprint {fp}. Copy")
        self.setVisible(bool(fp))                         # shown first, so the reveal below can play
        if changed:
            motion.scramble(self, self._short)            # your key resolves like a hash when it first appears
        elif getattr(self, "_ansx_scramble", None) is None:
            self.setText(self._short)

    def _copy(self) -> None:
        QApplication.clipboard().setText(self._fp)
        self.setIcon(icons.icon("check", theme.color("primary"), 16))
        self.setText("Copied")
        motion.later(1400, self._restore)

    def _restore(self) -> None:
        self.set_fingerprint(self._fp)


class HomePage(Page):
    navigate = pyqtSignal(str)
    send_to_requested = pyqtSignal(str)                   # "send again" chips -> Send, person already chosen

    def __init__(self, ctl):
        super().__init__(ctl, "Home", "")
        self.key_chip = KeyChip()
        self.actions.addWidget(self.key_chip, 0, Qt.AlignmentFlag.AlignVCenter)
        self.ticker = label("", "eyebrow", wrap=False)    # "FRI 26 SEP · 3 PROTECTED · 1 WAITING · ONLINE"
        self.header.layout().addWidget(self.ticker)
        self._fp_for: tuple = ("", "")
        self.card_protect = ActionCard("shield", "Protect a file", "Encrypt it and keep it safe, ready to send.")
        self.card_send = ActionCard("send", "Send a file", "Only the person you choose can open it.")
        self.card_inbox = ActionCard("inbox", "Inbox", "Files people have sent you.")
        self.again_row = QHBoxLayout()                    # the people you send to most, one click away
        self.again_row.setSpacing(6)
        self.card_send.body.addLayout(self.again_row)
        row = QHBoxLayout()
        row.setSpacing(14)
        for c, key in ((self.card_protect, "vault"), (self.card_send, "send"), (self.card_inbox, "inbox")):
            row.addWidget(c, 1)
            c.clicked.connect(lambda k=key: self.navigate.emit(k))
        self.root.addLayout(row)

        self.checklist = Card(padding=18, spacing=8)
        self.check_rows: dict[str, QLabel] = {}
        head = QHBoxLayout()
        head.addWidget(label("Finish setting up", "h2", wrap=False), 1)
        self.check_ring = ProgressRing(40)
        head.addWidget(self.check_ring, 0, Qt.AlignmentFlag.AlignTop)
        self.checklist.body.addLayout(head)
        self.check_progress = label("", "muted")
        self.checklist.body.addWidget(self.check_progress)
        self.check_actions: dict[str, Button] = {}
        self.check_texts: dict[str, QLabel] = {}
        for key, text, action, icon, page in (
                ("relay", "Connect to a relay so people can reach you", "Connect", "wifi", "settings"),
                ("storage", "Add cloud storage for larger files (recommended)", "Add storage", "cloud", "settings"),
                ("verify", "Verify a friend's key so you know it's really them", "Verify someone", "key", "contacts")):
            r = QHBoxLayout()
            ic = QLabel()
            self.check_rows[key] = ic
            r.addWidget(ic)
            self.check_texts[key] = label(text, "body")
            r.addWidget(self.check_texts[key], 1)
            b = Button(action, "secondary", icon, "sm")
            b.clicked.connect(lambda _c=False, p=page: self.navigate.emit(p))
            self.check_actions[key] = b
            r.addWidget(b)
            self.checklist.body.addLayout(r)
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

        head = QHBoxLayout()
        head.addWidget(label("RECENT ACTIVITY", "eyebrow", wrap=False), 1, Qt.AlignmentFlag.AlignBottom)
        self.spark = Sparkline(150, 26)                   # the last 14 days, at a glance
        head.addWidget(self.spark, 0, Qt.AlignmentFlag.AlignBottom)
        self.root.addLayout(head)
        self.activity = Card(padding=6, spacing=0)
        self.root.addWidget(self.activity)
        self.root.addStretch(1)

        ctl.relay_state_changed.connect(self._changed)      # bound methods: disconnected with the page
        self.refresh_while_visible(self._fill_activity)
        ctl.inbox_changed.connect(self._changed)
        ctl.outbox_changed.connect(self._changed)             # "last sent …" on the Send tile
        ctl.vault_changed.connect(self._changed)              # "N files protected" on the Protect tile
        ctl.activity_changed.connect(self.refresh)
        ctl.vault_changed.connect(self.refresh)
        ctl.contacts_changed.connect(self.refresh)

    def on_show(self) -> None:
        self.refresh()

    def _changed(self, *_a) -> None:
        self.refresh()

    def refresh(self) -> None:
        ctl = self.ctl
        name = ctl.operator or ""
        self.header.title.setText(f"{_greeting()}, {name}" if name else "Home")
        if self._fp_for[0] != name:                       # the fingerprint is worked out once per identity
            self._fp_for = (name, ctl.my_fingerprint() if name else "")
        self.key_chip.set_fingerprint(self._fp_for[1])

        n = len(ctl.inbox)
        self.card_inbox.pill.setVisible(bool(n))
        self.card_inbox.pill.set(f"{n} waiting", "primary")
        self._tile_status(n)

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
        where = {"tpm2": "Keys are sealed by this computer's TPM chip.",
                 "keyring": "Keys are sealed in your system's secure storage.",
                 "file": "Keys are sealed in a file on this computer (less protected than a keychain)."}.get(
            backend, f"Keys are sealed by {backend}.")
        self.tile_security.set("Card unlock" if mode == "nfc" else "Passphrase unlock", where,
                               "success" if backend != "file" else "warning", "shield")

        verified = any(c.get("verified") for c in ctl.contacts())
        done = {"relay": state == "online", "storage": bool(targets), "verify": verified}
        for key, ic in self.check_rows.items():
            ic.setPixmap(icons.pixmap("check" if done[key] else "clock", theme.color("success" if done[key] else "text_faint"), 18))
            self.check_actions[key].setVisible(not done[key])
            self.check_texts[key].setProperty("role", "muted" if done[key] else "body")
            self.check_texts[key].style().polish(self.check_texts[key])
        n = sum(done.values())
        self.check_progress.setText(f"{n} of {len(done)} done. Each step takes a minute or two.")
        self.check_ring.set(n, len(done))
        self.checklist.setVisible(not (done["relay"] and done["storage"]))

        self._fill_activity()

    def _tile_status(self, waiting: int) -> None:
        """The three big tiles double as a status line: what is protected, what went out last, what is waiting."""
        ctl = self.ctl
        n = len(ctl.vault_entries()) if ctl.operator else 0
        link = {"online": "ONLINE", "connecting": "CONNECTING", "offline": "OFFLINE", "conflict": "NAME CONFLICT"}.get(
            ctl.relay_state, "NOT CONNECTED")
        self.ticker.setText(" · ".join([datetime.datetime.now().strftime("%a %d %b").upper(), f"{n} PROTECTED",
                                        f"{waiting} WAITING", link]))
        self.ticker.setVisible(bool(ctl.operator))
        self.card_protect.text.setText(f"{n} file{'s' if n != 1 else ''} protected. Add more any time."
                                       if n else self.card_protect.default_text)
        latest = max(ctl.outbox or [], key=lambda o: o.get("created", 0), default=None)
        if latest:
            what, when = ctl.sent_file_name(latest["id"]), time_ago(latest["created"])
            self.card_send.text.setText(f"Last: {what} to {latest['to']}, {when}." if what
                                        else f"Last sent to {latest['to']}, {when}.")
        else:
            self.card_send.text.setText(self.card_send.default_text)
        self._fill_again()
        senders = sorted({i.get("from", "") for i in ctl.inbox or []} - {""})
        self.card_inbox.text.setText(f"{waiting} file{'s' if waiting != 1 else ''} from {_names(senders)}, waiting for "
                                     "you to accept." if waiting and senders else
                                     f"{waiting} file{'s' if waiting != 1 else ''} waiting for you to accept."
                                     if waiting else
                                     (f"Nothing new. People send to your name, {ctl.operator}." if ctl.operator
                                      else self.card_inbox.default_text))

    def _fill_again(self) -> None:
        clear_layout(self.again_row)
        recent: list = []
        for o in sorted(self.ctl.outbox or [], key=lambda o: o.get("created", 0), reverse=True):
            if o.get("to") and o["to"] not in recent:
                recent.append(o["to"])
        if not recent:
            return
        self.again_row.addWidget(label("Send again", "faint", wrap=False))
        for name in recent[:3]:
            chip = QPushButton(name)
            chip.setProperty("chip", "person")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            chip.setToolTip(f"Send {name} a file")
            chip.clicked.connect(lambda _c=False, n=name: self.send_to_requested.emit(n))
            self.again_row.addWidget(chip)
        self.again_row.addStretch(1)

    def _fill_spark(self) -> None:
        items = __import__("activity").recent(1000, operator=self.ctl.operator or "")
        today = datetime.date.today()
        days = [today - datetime.timedelta(days=13 - i) for i in range(14)]
        counts = {d: 0 for d in days}
        for it in items:
            d = datetime.date.fromtimestamp(it.get("ts", 0))
            if d in counts and it.get("kind") != "security":   # files moving, not locking and unlocking
                counts[d] += 1
        values = [counts[d] for d in days]
        self.spark.set_values(values, f"{sum(values)} things done in the last 14 days · {values[-1]} today")

    def _fill_activity(self) -> None:
        self._fill_spark()
        lay = self.activity.body
        clear_layout(lay)
        items = __import__("activity").recent(8, operator=self.ctl.operator or "")
        if not items:                                     # one quiet line, not a tall empty box below the fold
            row = QWidget()
            hl = QHBoxLayout(row)
            hl.setContentsMargins(12, 10, 12, 10)
            hl.setSpacing(12)
            hl.addWidget(IconBadge("activity", "neutral", 34))
            hl.addWidget(label("Nothing yet. Files you protect, send and receive will show up here.", "muted"), 1)
            lay.addWidget(row)
            return
        day_shown = None
        for it in items:
            day = _day_heading(it["ts"])
            if day != day_shown and len({_day_heading(i["ts"]) for i in items}) > 1:
                head = label(day, "eyebrow", wrap=False)   # TODAY / YESTERDAY / WED 24 SEP, once the list spans days
                head.setContentsMargins(12, 8 if day_shown else 2, 0, 2)
                lay.addWidget(head)
            day_shown = day
            icon, kind = ACTIVITY_STYLE.get(it["kind"], ("info", "neutral"))
            target = ACTIVITY_LINK.get(it["kind"])
            row = _ActivityRow() if target else QWidget()
            hl = QHBoxLayout(row)
            hl.setContentsMargins(12, 9, 12, 9)
            hl.setSpacing(12)
            hl.addWidget(IconBadge(icon, kind, 34))
            col = QVBoxLayout()
            col.setSpacing(0)
            col.addWidget(ElidedLabel(it["title"], "body"))
            if it.get("detail"):
                col.addWidget(ElidedLabel(it["detail"], "muted"))
            hl.addLayout(col, 1)
            hl.addWidget(label(time_ago(it["ts"]), "faint", wrap=False))
            if target:
                row.setObjectName("ActivityRow")
                row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
                row.setCursor(Qt.CursorShape.PointingHandCursor)
                row.setFocusPolicy(Qt.FocusPolicy.TabFocus)
                row.setToolTip(LINK_TIP[target])
                row.clicked.connect(lambda t=target: self.navigate.emit(t))
                go = QLabel()
                go.setPixmap(icons.pixmap("chevron", theme.color("text_faint"), 16))
                hl.addWidget(go)
            lay.addWidget(row)
