"""ui/pages/send.py — send a protected file in three clear steps: file → person → review."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QStackedWidget, QVBoxLayout, QWidget,
)

from security_core import VaultLedger
from ui.controller import Job
from ui.dialogs import confirm
from ui.pages.base import Page
from ui.widgets import (
    Avatar, Banner, Button, Card, EmptyState, Fingerprint, IconBadge, KeyValue, ListRow, Pill,
    ProgressPanel, Stepper, file_icon, friendly_date, human_size, label,
)

TRUST_PILL = {"verified": ("Verified", "success"), "unverified": ("Not verified", "warning"),
              "unknown": ("New", "neutral"), "changed": ("Key changed", "danger")}


class SendPage(Page):
    navigate = pyqtSignal(str)

    def __init__(self, ctl):
        super().__init__(ctl, "Send a file", "Only the person you choose can open it. You'll be told when they accept.")
        self.entry_id: Optional[str] = None
        self.recipient: Optional[str] = None
        self.step = 0
        self._job: Optional[Job] = None

        self.stepper = Stepper(["Choose a file", "Choose a person", "Review and send"])
        self.root.addWidget(self.stepper)
        self.card = Card(padding=24, spacing=14)
        self.stack = QStackedWidget()
        self.card.body.addWidget(self.stack)
        self.root.addWidget(self.card)

        # step 0 — file
        s0 = QWidget()
        l0 = QVBoxLayout(s0)
        l0.setContentsMargins(0, 0, 0, 0)
        l0.setSpacing(10)
        l0.addWidget(label("Which file do you want to send?", "h2"))
        self.files = QListWidget()
        self.files.itemSelectionChanged.connect(self._file_picked)
        self.files.itemDoubleClicked.connect(lambda _i: self._next())
        l0.addWidget(self.files)
        self.files_empty = EmptyState("shield", "Nothing to send yet",
                                      "Protect a file first, then come back here to send it.", "Protect a file",
                                      lambda: self.navigate.emit("vault"))
        l0.addWidget(self.files_empty)
        self.stack.addWidget(s0)

        # step 1 — person
        s1 = QWidget()
        l1 = QVBoxLayout(s1)
        l1.setContentsMargins(0, 0, 0, 0)
        l1.setSpacing(10)
        top = QHBoxLayout()
        top.addWidget(label("Who should receive it?", "h2", wrap=False), 1)
        self.refresh_btn = Button("Refresh people", "secondary", "refresh", "sm")
        self.refresh_btn.clicked.connect(self._refresh_people)
        top.addWidget(self.refresh_btn)
        l1.addLayout(top)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search by name")
        self.search.textChanged.connect(self._fill_people)
        l1.addWidget(self.search)
        self.people = QListWidget()
        self.people.itemSelectionChanged.connect(self._person_picked)
        self.people.itemDoubleClicked.connect(lambda _i: self._next())
        l1.addWidget(self.people)
        self.people_empty = EmptyState("users", "No one here yet",
                                       "Press “Refresh people” to look them up. They need to have opened the app at least once.")
        l1.addWidget(self.people_empty)
        self.people_msg = label("", "muted")
        l1.addWidget(self.people_msg)
        self.stack.addWidget(s1)

        # step 2 — review + progress + done
        s2 = QWidget()
        l2 = QVBoxLayout(s2)
        l2.setContentsMargins(0, 0, 0, 0)
        l2.setSpacing(12)
        self.review = QWidget()
        rl = QVBoxLayout(self.review)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(10)
        rl.addWidget(label("Ready to send", "h2", wrap=False))
        self.rv_file = KeyValue("File")
        self.rv_size = KeyValue("Size")
        who = QHBoxLayout()
        who.setContentsMargins(0, 2, 0, 2)
        to_lbl = label("To", "muted", wrap=False)
        to_lbl.setMinimumWidth(110)
        who.addWidget(to_lbl)
        self.rv_avatar = Avatar("?", 30)
        self.rv_name = label("", "body", wrap=False)
        self.rv_name.setStyleSheet("font-weight: 650;")
        self.rv_pill = Pill("", "neutral")
        who.addWidget(self.rv_avatar)
        who.addSpacing(6)
        who.addWidget(self.rv_name)
        who.addWidget(self.rv_pill)
        who.addStretch()
        rl.addWidget(self.rv_file)
        rl.addWidget(self.rv_size)
        rl.addLayout(who)
        rl.addWidget(label("Their key fingerprint:", "muted"))
        self.rv_fp = Fingerprint()
        rl.addWidget(self.rv_fp)
        self.trust_banner = Banner("", "warning", "I verified it", self._verify_now)
        rl.addWidget(self.trust_banner)
        self.send_btn = Button("Send securely", "primary", "send", "lg")
        self.send_btn.clicked.connect(self._send)
        rl.addWidget(self.send_btn)
        l2.addWidget(self.review)

        self.progress = ProgressPanel()
        self.progress.cancel_clicked.connect(self._cancel)
        l2.addWidget(self.progress)
        self.error = Banner("", "danger")
        self.error.hide()
        l2.addWidget(self.error)

        self.done = QWidget()
        dl = QVBoxLayout(self.done)
        dl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dl.setSpacing(10)
        dl.addWidget(IconBadge("check", "success", 64), 0, Qt.AlignmentFlag.AlignHCenter)
        self.done_title = label("Sent", "h1", wrap=False)
        self.done_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.done_text = label("", "muted")
        self.done_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dl.addWidget(self.done_title)
        dl.addWidget(self.done_text)
        row = QHBoxLayout()
        row.addStretch()
        again = Button("Send another", "secondary", "send")
        again.clicked.connect(self.reset)
        track = Button("See sent items", "primary", "inbox")
        track.clicked.connect(lambda: self.navigate.emit("sent"))
        row.addWidget(again)
        row.addWidget(track)
        row.addStretch()
        dl.addLayout(row)
        self.done.hide()
        l2.addWidget(self.done)
        l2.addStretch()
        self.stack.addWidget(s2)

        nav = QHBoxLayout()
        self.back_btn = Button("Back", "ghost", "back")
        self.next_btn = Button("Continue", "primary", size="lg")
        self.back_btn.clicked.connect(self._back)
        self.next_btn.clicked.connect(self._next)
        nav.addWidget(self.back_btn)
        nav.addStretch()
        nav.addWidget(self.next_btn)
        self.card.body.addLayout(nav)
        self.root.addStretch(1)

        ctl.vault_changed.connect(self._fill_files)
        ctl.contacts_changed.connect(self._fill_people)

    # ── lifecycle ────────────────────────────────────────────────────────────
    def on_show(self) -> None:
        self._fill_files()
        self._fill_people()
        self._go(self.step if self.step < 3 else 0)

    def on_session_ended(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.entry_id = self.recipient = None
        self._job = None
        self.search.clear()
        self.error.hide()
        self.done.hide()
        self.progress.finish()
        self._fill_files()
        self._fill_people()
        self._go(0)

    def preselect(self, entry_id: str) -> None:
        """Called from the Protect page's Send button: skip straight to choosing a person."""
        self.reset()
        self.entry_id = entry_id
        self._fill_files()
        self._go(1)

    # ── step 0/1 lists ───────────────────────────────────────────────────────
    def _fill_files(self) -> None:
        self.files.blockSignals(True)
        self.files.clear()
        entries = self.ctl.vault_entries()
        for e in entries:
            it = QListWidgetItem()
            it.setData(Qt.ItemDataRole.UserRole, e["id"])
            it.setSizeHint(QSize(0, 62))
            self.files.addItem(it)
            self.files.setItemWidget(it, ListRow(e["original_filename"], f"{human_size(e.get('size'))} · {friendly_date(e['date_vaulted'])}",
                                                   icon=file_icon(e["original_filename"])[0],
                                                   icon_kind=file_icon(e["original_filename"])[1]))
            if e["id"] == self.entry_id:
                self.files.setCurrentItem(it)
        self.files.blockSignals(False)
        self.files.setVisible(bool(entries))
        self.files_empty.setVisible(not entries)
        self._update_nav()

    def _fill_people(self) -> None:
        self.people.blockSignals(True)
        self.people.clear()
        needle = self.search.text().strip().lower()
        contacts = [c for c in self.ctl.contacts() if needle in c["operator"].lower()]
        for c in contacts:
            trust = self.ctl.trust(c["operator"])
            text, kind = TRUST_PILL[trust]
            it = QListWidgetItem()
            it.setData(Qt.ItemDataRole.UserRole, c["operator"])
            it.setSizeHint(QSize(0, 62))
            self.people.addItem(it)
            self.people.setItemWidget(it, ListRow(c["operator"], "Verified key" if trust == "verified" else "Key not verified yet",
                                                  avatar=c["operator"], right=[Pill(text, kind)]))
            if c["operator"] == self.recipient:
                self.people.setCurrentItem(it)
        self.people.blockSignals(False)
        have = bool(contacts)
        self.people.setVisible(have)
        self.people_empty.setVisible(not have)
        self.search.setVisible(bool(self.ctl.contacts()))
        self._update_nav()

    def _refresh_people(self) -> None:
        self.refresh_btn.setEnabled(False)
        self.people_msg.setText("Looking people up…")
        self.ctl.run_job(self.ctl.make_directory_job(), on_success=self._refreshed,
                         on_fail=lambda m: self._refreshed(None, m))

    def _refreshed(self, result, error: str = "") -> None:
        self.refresh_btn.setEnabled(True)
        self.people_msg.setText(error if error else "")
        self.ctl.contacts_changed.emit()

    def _file_picked(self) -> None:
        items = self.files.selectedItems()
        self.entry_id = items[0].data(Qt.ItemDataRole.UserRole) if items else None
        self._update_nav()

    def _person_picked(self) -> None:
        items = self.people.selectedItems()
        self.recipient = items[0].data(Qt.ItemDataRole.UserRole) if items else None
        self._update_nav()

    # ── wizard navigation ────────────────────────────────────────────────────
    def _go(self, i: int) -> None:
        self.step = i
        self.stack.setCurrentIndex(min(i, 2))
        self.stepper.set_current(i)
        self.back_btn.setVisible(0 < i < 3)
        self.next_btn.setVisible(i < 2)
        if i == 2:
            self._fill_review()
        self._update_nav()

    def _update_nav(self) -> None:
        if self.step == 0:
            self.next_btn.setEnabled(bool(self.entry_id))
        elif self.step == 1:
            self.next_btn.setEnabled(bool(self.recipient))

    def _back(self) -> None:
        if self._job is None:
            self._go(max(0, self.step - 1))

    def _next(self) -> None:
        if self.step == 0 and self.entry_id:
            self._go(1)
            if not self.ctl.contacts():
                self._refresh_people()
        elif self.step == 1 and self.recipient:
            self._go(2)

    # ── review / send ────────────────────────────────────────────────────────
    def _fill_review(self) -> None:
        self.error.hide()
        self.done.hide()
        self.progress.finish()
        self.review.show()
        self.back_btn.setEnabled(True)
        entry = VaultLedger.get(self.entry_id or "")
        info = None
        if self.recipient:
            from security_core import SecurityCore
            info = SecurityCore.get_contact_info(self.recipient)
        if not entry or not self.recipient:
            return
        self.rv_file.set(entry["original_filename"])
        self.rv_size.set(human_size(entry.get("size")))
        self.rv_avatar.set_name(self.recipient)
        self.rv_name.setText(self.recipient)
        self.rv_fp.set((info or {}).get("fingerprint", ""))
        trust = self.ctl.trust(self.recipient)
        text, kind = TRUST_PILL[trust]
        self.rv_pill.set(text, kind)
        if trust == "verified":
            self.trust_banner.set(f"You verified {self.recipient}'s key, so this can only be opened by them.", "success")
            self.trust_banner._btn.hide()
        else:
            self.trust_banner.set(f"You haven't verified {self.recipient}'s key yet. If this file is sensitive, compare the "
                                  "fingerprint above with them by phone or in person first.", "warning")
            self.trust_banner._btn.show()
        self.send_btn.setText("Resume sending" if self.ctl.has_pending_upload(self.entry_id, self.recipient) else "Send securely")

    def _verify_now(self) -> None:
        if self.recipient and confirm(
                self, "Confirm fingerprint",
                f"Did {self.recipient} read you exactly this fingerprint, over a channel you trust?\n\n{self.rv_fp.raw()}",
                ok="Yes, it matches"):
            self.ctl.verify_contact(self.recipient)
            self._fill_review()

    def _send(self) -> None:
        entry = VaultLedger.get(self.entry_id or "")
        if not entry or not self.recipient or self._job is not None:
            return
        self.error.hide()
        self.review.hide()
        self.back_btn.setEnabled(False)
        self.progress.start(f"Sending {entry['original_filename']} to {self.recipient}")
        try:
            job = self.ctl.make_send_job(entry, self.recipient)
        except Exception as exc:
            self._failed(str(exc))
            return
        self._job = job
        self.ctl.run_job(job, on_success=lambda r: self._sent(entry, r), on_fail=self._failed, on_cancel=self._cancelled,
                         on_progress=lambda text, pct: self.progress.update_progress(text, pct))

    def _sent(self, entry: dict, result: dict) -> None:
        self._job = None
        self.progress.finish()
        self.ctl.after_send(entry, result["to"])
        self.done_title.setText(f"Sent to {result['to']}")
        self.done_text.setText(f"“{entry['original_filename']}” is in {result['to']}'s inbox. They choose whether to accept it, "
                               "and you'll get a notification when they do.")
        self.done.show()
        self.stepper.set_current(3)
        self.next_btn.hide()
        self.back_btn.hide()
        self.ctl.toast.emit(f"Sent to {result['to']}.", "success")

    def _failed(self, message: str) -> None:
        self._job = None
        self.progress.finish()
        self.review.show()
        self.back_btn.setEnabled(True)
        self.error.set(message, "danger")
        self.error.show()
        if self.entry_id and self.recipient:
            self.send_btn.setText("Resume sending" if self.ctl.has_pending_upload(self.entry_id, self.recipient) else "Try again")

    def _cancelled(self) -> None:
        self._job = None
        self.progress.finish()
        self.review.show()
        self.back_btn.setEnabled(True)
        self.ctl.toast.emit("Sending cancelled. Nothing was delivered.", "info")

    def _cancel(self) -> None:
        if self._job:
            self._job.cancel()
            self.progress.cancel_btn.setEnabled(False)
            self.progress.detail.setText("Cancelling…")
