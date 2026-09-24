"""ui/pages/auth.py — first run, login and identity creation, in plain language."""
from __future__ import annotations

import re
from typing import Optional

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

from security_core import SecurityCore
from ui import motion, theme
from ui.controller import AppController, Job
from ui.dialogs import confirm
from ui.widgets import (
    FitStack,
    Banner, Button, Card, Fingerprint, IconBadge, ListRow, Pill, Stepper, label, polish,
)

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")


def passphrase_strength(p: str) -> tuple[str, str, int]:
    """(label, pill kind, score 0-3). Length matters most; variety adds a little."""
    if len(p) < 12:
        return "Too short", "danger", 0
    kinds = sum(bool(re.search(rx, p)) for rx in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]")) + (len(p) >= 16) + (" " in p)
    if kinds >= 4:
        return "Strong", "success", 3
    if kinds >= 2:
        return "Good", "info", 2
    return "Weak", "warning", 1


class _Center(QScrollArea):
    """A centered, fixed-width column that scrolls instead of overlapping when the window is short."""

    def __init__(self, width: int = 540):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("Root")
        self.setWidget(inner)
        outer = QHBoxLayout(inner)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addStretch(1)
        self.column = QVBoxLayout()
        self.column.setSpacing(16)
        holder = QWidget()
        holder.setFixedWidth(width)
        holder.setLayout(self.column)
        outer.addWidget(holder)
        outer.addStretch(1)


def brand_header() -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 6)
    lay.setSpacing(8)
    lay.addWidget(IconBadge("shield", "primary", 56), 0, Qt.AlignmentFlag.AlignHCenter)
    t = label("A.N.Sx Vault", "display", wrap=False)
    t.setAlignment(Qt.AlignmentFlag.AlignCenter)
    s = label("Send files that only the right person can open.", "muted", wrap=False)
    s.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lay.addWidget(t)
    lay.addWidget(s)
    return w


class LoginView(_Center):
    def __init__(self, ctl: AppController, on_create):
        super().__init__()
        self.ctl, self._on_create = ctl, on_create
        self._job: Optional[Job] = None
        self._selected: Optional[str] = None
        c = self.column
        c.addStretch(1)
        c.addWidget(brand_header())

        card = self.card = Card(padding=22, spacing=14)
        self.heading = label("Welcome back", "h1", wrap=False)
        card.body.addWidget(self.heading)
        card.body.addWidget(label("Who is unlocking the vault?", "muted"))
        self.people = QListWidget()
        self.people.setMaximumHeight(200)
        self.people.itemSelectionChanged.connect(self._picked)
        card.body.addWidget(self.people)

        # unlock panel (changes with the person's sign-in method)
        self.panel = QFrame()
        self.panel.setObjectName("CardAlt")
        pl = QVBoxLayout(self.panel)
        pl.setContentsMargins(18, 16, 18, 16)
        pl.setSpacing(10)
        self.hint = label("", "body")
        pl.addWidget(self.hint)
        top = QHBoxLayout()
        self.reader_pill = Pill("", "neutral")
        top.addWidget(self.reader_pill)
        top.addStretch()
        pl.addLayout(top)
        self.pass_edit = QLineEdit()
        self.pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.pass_edit.setPlaceholderText("Your passphrase")
        self.pass_edit.returnPressed.connect(self._unlock)
        pl.addWidget(self.pass_edit)
        self.unlock_btn = Button("Unlock", "primary", "unlock", "lg")
        self.unlock_btn.clicked.connect(self._unlock)
        pl.addWidget(self.unlock_btn)
        self.cancel_btn = Button("Cancel", "ghost")
        self.cancel_btn.clicked.connect(self._cancel)
        self.cancel_btn.hide()
        pl.addWidget(self.cancel_btn)
        self.error = label("", "error")
        self.error.hide()
        pl.addWidget(self.error)
        card.body.addWidget(self.panel)
        c.addWidget(card)

        foot = QHBoxLayout()
        self.create_btn = Button("Create a new identity", "ghost", "plus")
        self.create_btn.clicked.connect(on_create)
        self.remove_btn = Button("Remove this identity", "ghost", "trash", "sm")
        self.remove_btn.clicked.connect(self._remove)
        foot.addWidget(self.create_btn)
        foot.addStretch()
        foot.addWidget(self.remove_btn)
        c.addLayout(foot)
        c.addStretch(2)

    def _err(self, text: str) -> None:
        self.error.setText(text)
        self.error.setVisible(bool(text))

    def refresh(self) -> None:
        self.people.clear()
        for name in self.ctl.operators():
            mode = self.ctl.auth_mode(name)
            it = QListWidgetItem()
            it.setData(Qt.ItemDataRole.UserRole, name)
            it.setSizeHint(QSize(0, 62))
            self.people.addItem(it)
            self.people.setItemWidget(it, ListRow(name, "Signs in with a card" if mode == "nfc" else "Signs in with a passphrase",
                                                  avatar=name, right=[Pill("Card" if mode == "nfc" else "Passphrase", "neutral")]))
        self.people.setFixedHeight(min(self.people.count(), 3) * 66 + 6)
        if self.people.count():
            self.people.setCurrentRow(0)
        self._err("")

    def _picked(self) -> None:
        items = self.people.selectedItems()
        self._selected = items[0].data(Qt.ItemDataRole.UserRole) if items else None
        self.panel.setVisible(bool(self._selected))
        self.remove_btn.setVisible(bool(self._selected))
        if not self._selected:
            return
        nfc = self.ctl.auth_mode(self._selected) == "nfc"
        self.pass_edit.setVisible(not nfc)
        self.reader_pill.setVisible(nfc)
        if nfc:
            ok = self.ctl.reader_connected()
            self.hint.setText("Hold your card flat on the reader, then press Unlock.")
            self.reader_pill.set("Reader connected" if ok else "Reader not found", "success" if ok else "warning")
            self.unlock_btn.setText("Unlock with card")
        else:
            self.hint.setText("Enter your passphrase.")
            self.unlock_btn.setText("Unlock")
            self.pass_edit.setFocus()
        self._err("")

    def _busy(self, on: bool) -> None:
        self.unlock_btn.setEnabled(not on)
        self.people.setEnabled(not on)
        self.pass_edit.setEnabled(not on)
        self.create_btn.setEnabled(not on)
        self.cancel_btn.setVisible(on and self.ctl.auth_mode(self._selected or "") == "nfc")

    def _unlock(self) -> None:
        if not self._selected or self._job is not None:
            return
        name, pw = self._selected, self.pass_edit.text()
        if self.ctl.auth_mode(name) == "passphrase" and not pw:
            self._err("Enter your passphrase.")
            return
        self._err("")
        self._busy(True)
        self.hint.setText("Hold your card on the reader…" if self.ctl.auth_mode(name) == "nfc" else "Unlocking…")
        self._job = self.ctl.make_login_job(name, pw)
        self.ctl.run_job(self._job, on_success=self._ok, on_fail=self._fail, on_cancel=self._cancelled)

    def _ok(self, name: str) -> None:
        self._job = None
        self._busy(False)
        self.pass_edit.clear()
        self.ctl.begin_session(name)

    def _fail(self, message: str) -> None:
        self._job = None
        self._busy(False)
        self._err(message)
        self.pass_edit.selectAll()
        self._picked()
        self._err(message)
        motion.shake(self.card)                               # "that's not right", without a dialog in the way

    def _cancelled(self) -> None:
        self._job = None
        self._busy(False)
        self._picked()

    def _cancel(self) -> None:
        if self._job:
            self._job.cancel()

    def _remove(self) -> None:
        if not self._selected:
            return
        if confirm(self, "Remove this identity?",
                   f"'{self._selected}' will be deleted from this computer, including its keys. Files protected with it "
                   "can never be opened again unless you have a backup. This cannot be undone.",
                   ok="Remove permanently", danger=True):
            self.ctl.delete_identity(self._selected)
            if self.ctl.operators():
                self.refresh()
            else:
                self._on_create()


class CreateView(_Center):
    """Three steps: name → how to unlock → create."""

    def __init__(self, ctl: AppController, on_back_to_login):
        super().__init__()
        self.ctl, self._back_to_login = ctl, on_back_to_login
        self._method = "passphrase"
        self._job: Optional[Job] = None
        self.step = 0
        c = self.column
        c.addStretch(1)
        c.addWidget(brand_header())
        self.stepper = Stepper(["Your name", "How you unlock", "Create"])
        c.addWidget(self.stepper)

        self.card = Card(padding=24, spacing=14)
        self.stack = FitStack()                         # the card fits the current step instead of the tallest one
        self.card.body.addWidget(self.stack)
        c.addWidget(self.card)

        # step 0 — name
        s0 = QWidget()
        l0 = QVBoxLayout(s0)
        l0.setContentsMargins(0, 0, 0, 0)
        l0.setSpacing(10)
        l0.addWidget(label("Choose your name", "h1", wrap=False))
        l0.addWidget(label("This is how other people find you. Letters, digits, dot, dash or underscore, up to 32 characters. "
                           "You cannot change it later.", "muted"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("for example: alice")
        self.name_edit.textChanged.connect(self._name_changed)
        self.name_edit.returnPressed.connect(self._next)
        l0.addWidget(self.name_edit)
        self.name_msg = label("", "error")
        l0.addWidget(self.name_msg)
        l0.addStretch()
        self.stack.addWidget(s0)

        # step 1 — method
        s1 = QWidget()
        l1 = QVBoxLayout(s1)
        l1.setContentsMargins(0, 0, 0, 0)
        l1.setSpacing(10)
        l1.addWidget(label("How will you unlock the vault?", "h1"))
        self.opt_card = self._option("nfc", "NFC card", "Hold a card on your reader. Fast, and nothing to remember or type.")
        self.card_pill = Pill("", "neutral")
        self.opt_card.layout().addWidget(self.card_pill, 0, Qt.AlignmentFlag.AlignTop)
        self.opt_pass = self._option("key", "Passphrase", "Type a secret phrase. Works on any computer, no hardware needed.")
        l1.addWidget(self.opt_card)
        l1.addWidget(self.opt_pass)
        self.reader_row = QHBoxLayout()
        self.reader_pill = Pill("", "neutral")
        self.reader_refresh = Button("Check reader", "ghost", "refresh", "sm")
        self.reader_refresh.clicked.connect(self._method_changed)
        self.reader_row.addWidget(self.reader_pill)
        self.reader_row.addWidget(self.reader_refresh)
        self.reader_row.addStretch()
        l1.addLayout(self.reader_row)
        self.pass_box = QFrame()
        self.pass_box.setObjectName("CardAlt")
        pb = QVBoxLayout(self.pass_box)
        pb.setContentsMargins(16, 14, 16, 14)
        pb.setSpacing(8)
        self.pw1 = QLineEdit()
        self.pw1.setEchoMode(QLineEdit.EchoMode.Password)
        self.pw1.setPlaceholderText("Passphrase (at least 12 characters, a short sentence works well)")
        self.pw2 = QLineEdit()
        self.pw2.setEchoMode(QLineEdit.EchoMode.Password)
        self.pw2.setPlaceholderText("Type it again")
        self.pw_strength = Pill("", "neutral")
        self.pw_strength.hide()
        self.pw_msg = label("", "error")
        pb.addWidget(self.pw1)
        pb.addWidget(self.pw2)
        strength_row = QHBoxLayout()
        strength_row.addWidget(self.pw_strength)
        strength_row.addStretch()
        pb.addLayout(strength_row)
        pb.addWidget(self.pw_msg)
        pb.addWidget(Banner("There is no password reset. If you forget it, files protected with this identity cannot be opened.", "warning"))
        l1.addWidget(self.pass_box)
        l1.addStretch()
        self.pw1.textChanged.connect(self._pw_changed)
        self.pw2.textChanged.connect(self._pw_changed)
        self.pw1.returnPressed.connect(self.pw2.setFocus)            # Enter moves on, like a form should
        self.pw2.returnPressed.connect(lambda: self.next_btn.isEnabled() and self._next())
        self.stack.addWidget(s1)

        # step 2 — creating / done
        s2 = QWidget()
        l2 = QVBoxLayout(s2)
        l2.setContentsMargins(0, 0, 0, 0)
        l2.setSpacing(12)
        self.create_title = label("Creating your identity…", "h1", wrap=False)
        self.create_detail = label("", "muted")
        from ui.widgets import ProgressPanel
        self.progress = ProgressPanel(cancellable=False)
        self.done_box = QWidget()
        db = QVBoxLayout(self.done_box)
        db.setContentsMargins(0, 0, 0, 0)
        db.setSpacing(10)
        db.addWidget(label("Your key fingerprint — the identity others can verify with you:", "muted"))
        self.fp = Fingerprint()
        db.addWidget(self.fp)
        self.error_banner = Banner("", "danger")
        self.error_banner.hide()
        l2.addWidget(self.create_title)
        l2.addWidget(self.create_detail)
        l2.addWidget(self.progress)
        l2.addWidget(self.error_banner)
        l2.addWidget(self.done_box)
        l2.addStretch()
        self.done_box.hide()
        self.stack.addWidget(s2)

        # nav
        nav = QHBoxLayout()
        self.back_btn = Button("Back", "ghost", "back")
        self.next_btn = Button("Continue", "primary", size="lg")
        self.back_btn.clicked.connect(self._back)
        self.next_btn.clicked.connect(self._next)
        nav.addWidget(self.back_btn)
        nav.addStretch()
        nav.addWidget(self.next_btn)
        self.card.body.addLayout(nav)
        c.addStretch(2)

    # option cards
    def _option(self, icon: str, title: str, text: str) -> QFrame:
        f = QFrame()
        f.setObjectName("CardAlt")
        f.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(f)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.addWidget(IconBadge(icon, "primary", 40))
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(label(title, "h2", wrap=False))
        col.addWidget(label(text, "muted"))
        lay.addLayout(col, 1)
        f._key = "nfc" if icon == "nfc" else "passphrase"
        f.mouseReleaseEvent = lambda e, k=f._key: self._choose(k)
        return f

    def _choose(self, method: str) -> None:
        self._method = method
        self._method_changed()

    def _method_changed(self) -> None:
        for f, key in ((self.opt_card, "nfc"), (self.opt_pass, "passphrase")):
            f.setStyleSheet(f"QFrame#CardAlt {{ border: 2px solid {theme.color('primary')}; }}" if key == self._method else "")
        is_card = self._method == "nfc"
        self.pass_box.setVisible(not is_card)
        connected = self.ctl.reader_connected()
        self.card_pill.set("Reader found" if connected else "No reader", "success" if connected else "warning")
        self.reader_pill.setVisible(is_card)
        self.reader_refresh.setVisible(is_card)
        if is_card:
            self.reader_pill.set("Reader connected — ready" if connected else "Reader not found. Plug it in, then check again.",
                                 "success" if connected else "warning")
        self._update_next()

    # validation
    def _name_changed(self) -> None:
        n = self.name_edit.text()
        ok = bool(NAME_RE.match(n)) and ".." not in n
        exists = n in self.ctl.operators()
        self.name_edit.setProperty("invalid", "true" if (n and (not ok or exists)) else "false")
        polish(self.name_edit)
        self.name_msg.setText("" if not n or ok and not exists else
                              "That name already exists on this computer." if exists else
                              "Use letters, digits, dot, dash or underscore only (max 32).")
        self._update_next()

    def _pw_changed(self) -> None:
        p = self.pw1.text()
        if p:
            text, kind, _ = passphrase_strength(p)
            self.pw_strength.set(f"Strength: {text}", kind)
            self.pw_strength.show()
        else:
            self.pw_strength.hide()
        self.pw_msg.setText("Passphrases do not match." if self.pw2.text() and self.pw2.text() != p else "")
        self._update_next()

    def _valid(self) -> bool:
        if self.step == 0:
            n = self.name_edit.text()
            return bool(NAME_RE.match(n)) and ".." not in n and n not in self.ctl.operators()
        if self.step == 1:
            if self._method == "nfc":
                return self.ctl.reader_connected()
            return passphrase_strength(self.pw1.text())[2] >= 1 and self.pw1.text() == self.pw2.text()
        return False

    def _update_next(self) -> None:
        self.next_btn.setEnabled(self._valid())

    # navigation
    def start(self, first_run: bool) -> None:
        self.name_edit.clear()
        self.pw1.clear()
        self.pw2.clear()
        self.error_banner.hide()
        self.done_box.hide()
        self.progress.hide()
        self.step = 0
        self._method = "passphrase"
        self._go(0)
        self.name_edit.setFocus()

    def _go(self, i: int) -> None:
        self.step = i
        self.stack.setCurrentIndex(i)
        self.stepper.set_current(i)
        self.back_btn.setVisible(i < 2 and (i > 0 or bool(self.ctl.operators())))
        self.next_btn.setVisible(i < 2)
        self.next_btn.setText("Continue" if i == 0 else "Create my identity")
        if i == 1:
            self._method = "nfc" if self.ctl.reader_connected() else "passphrase"
            self._method_changed()
        self._update_next()

    def _back(self) -> None:
        if self.step == 0:
            self._back_to_login()
        else:
            self._go(self.step - 1)

    def _next(self) -> None:
        if not self._valid():
            return
        if self.step == 0:
            self._go(1)
        elif self.step == 1:
            self._create()

    def _create(self) -> None:
        self._go(2)
        self.error_banner.hide()
        self.done_box.hide()
        self.create_title.setText("Creating your identity…")
        name = self.name_edit.text()
        self.progress.start("Please wait")
        self.progress.indeterminate("Generating your encryption keys — a few seconds.")
        self.stepper.set_current(2)
        self.back_btn.hide()
        job = self.ctl.make_register_job(name, self._method, self.pw1.text() if self._method == "passphrase" else "")
        self._job = job
        self.ctl.run_job(job, on_success=self._created, on_fail=self._failed, on_progress=lambda t, p: self.progress.detail.setText(t))

    def _created(self, name: str) -> None:
        self._job = None
        self.progress.finish()
        self.create_title.setText("You're all set")
        self.create_detail.setText(f"Welcome, {name}. Your keys were created and protected on this computer.")
        self.fp.set(self.ctl_fp(name))
        motion.reveal(self.done_box, motion.SLOW)
        self.stepper.set_current(3)
        self.next_btn.setText("Open my vault")
        self.next_btn.setVisible(True)
        self.next_btn.setEnabled(True)
        self.next_btn.clicked.disconnect()
        self.next_btn.clicked.connect(lambda: self._finish(name))

    def ctl_fp(self, name: str) -> str:
        from security_core import public_key_fingerprint
        ident = SecurityCore.load_identity_for_user(name) or {}
        try:
            return public_key_fingerprint(ident.get("public_key", ""))
        except Exception:
            return ""

    def _finish(self, name: str) -> None:
        self.next_btn.clicked.disconnect()
        self.next_btn.clicked.connect(self._next)
        self.ctl.begin_session(name)

    def _failed(self, message: str) -> None:
        self._job = None
        self.progress.finish()
        self.create_title.setText("We could not create your identity")
        self.create_detail.setText("")
        self.error_banner.set(message, "danger")
        motion.reveal(self.error_banner)
        motion.shake(self.card)
        self.back_btn.show()
        self.next_btn.hide()


class AuthPage(QStackedWidget):
    def __init__(self, ctl: AppController):
        super().__init__()
        self.ctl = ctl
        self.setObjectName("Root")
        self.login = LoginView(ctl, lambda: self.show_create(False))
        self.create = CreateView(ctl, self.show_login)
        self.addWidget(self.login)
        self.addWidget(self.create)

    def show_login(self) -> None:
        if not self.ctl.operators():
            self.show_create(True)
            return
        self.login.refresh()
        self.setCurrentWidget(self.login)

    def show_create(self, first_run: bool) -> None:
        self.create.start(first_run)
        self.setCurrentWidget(self.create)
