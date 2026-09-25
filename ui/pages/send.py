"""ui/pages/send.py — send a protected file in three clear steps: file → person → review."""
from __future__ import annotations

import os
from typing import Optional

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout, QWidget,
)

from security_core import VaultLedger
from ui.controller import Job
from ui.dialogs import verify_fingerprint
from ui.pages.base import Page
from ui import art, motion
from ui.widgets import (
    Avatar, Banner, Button, Card, EmptyState, Fingerprint, KeyValue, ListRow, Pill,
    FitStack, ProgressPanel, Stepper, escape_goes_back, file_icon, friendly_date, human_size, label, on_enter,
    time_ago,
)

TRUST_PILL = {"verified": ("Verified", "success"), "unverified": ("Not verified", "warning"),
              "unknown": ("New", "neutral"), "changed": ("Key changed", "danger")}


SEARCH_FROM = 6          # show a search box once there are more files than this


def _fit_rows(lst, most: int = 6, row: int = 62) -> None:
    """A list as tall as its rows (up to `most`, then it scrolls) instead of a fixed three-row window."""
    h = min(max(lst.count(), 1), most) * row + 2 * lst.frameWidth() + 4
    lst.setMinimumHeight(h)
    lst.setMaximumHeight(h)


_MOD = "⌘" if __import__("sys").platform == "darwin" else "Ctrl"


class SendPage(Page):
    navigate = pyqtSignal(str)

    def __init__(self, ctl):
        super().__init__(ctl, "Send a file", "Only the person you choose can open it. You'll be told when they accept.")
        self.entry_id: Optional[str] = None
        self.recipient: Optional[str] = None
        self._recipient_preset = False                       # person chosen first (People / Inbox)
        self.entry_ids: list[str] = []                        # several files / people chosen at once
        self.recipients: list[str] = []
        self._batch: list = []                                # (entry, person) still to send
        self._batch_done: list = []
        self._batch_failed: list = []
        self.step = 0
        self._job: Optional[Job] = None

        self.add_connection_banner({
            "offline": "You're offline, so nothing can be sent right now. You can still pick a file and a person; "
                       "sending works again as soon as the relay is reachable.",
            "conflict": "Sending is turned off: this relay already has a different key under your name. "
                        "Use a different relay, or ask its administrator."})
        self.stepper = Stepper(["Choose a file", "Choose a person", "Review and send"])
        self.stepper.step_clicked.connect(self._step_clicked)
        self.root.addWidget(self.stepper)
        escape_goes_back(self, self._escape_back)
        self.card = Card(padding=24, spacing=14)
        self.stack = FitStack()                         # as tall as the current step, not the tallest one
        self.card.body.addWidget(self.stack)
        self.root.addWidget(self.card)

        # step 0 — file
        s0 = QWidget()
        l0 = QVBoxLayout(s0)
        l0.setContentsMargins(0, 0, 0, 0)
        l0.setSpacing(10)
        self.files_heading = label("Which file do you want to send?", "h2")
        l0.addWidget(self.files_heading)
        self.file_search = QLineEdit()
        self.file_search.setPlaceholderText("Search your files")
        self.file_search.setClearButtonEnabled(True)
        self.file_search.textChanged.connect(lambda _t: self._fill_files())
        self.file_search.hide()                          # only worth having once the list is long
        l0.addWidget(self.file_search)
        self.files = QListWidget()
        self.files.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)   # ⌘/Ctrl or Shift: several
        self.files.itemSelectionChanged.connect(self._file_picked)
        self.files.itemDoubleClicked.connect(lambda _i: self._next())
        on_enter(self.files, self._next)
        l0.addWidget(self.files)
        self.files_hint = label(f"Tip: hold {_MOD} or Shift to choose several files.", "faint")
        l0.addWidget(self.files_hint)
        self.files_empty = EmptyState("shield", "Nothing to send yet",
                                      "Choose a file: it is protected first, then you pick who gets it.",
                                      "Choose a file…", self._pick_new_file)
        l0.addWidget(self.files_empty)
        self.new_file_btn = Button("Protect and send a new file…", "ghost", "plus", "sm")
        self.new_file_btn.setToolTip("Pick any file: it is protected first, then you choose who gets it")
        self.new_file_btn.clicked.connect(self._pick_new_file)
        l0.addWidget(self.new_file_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.prep = ProgressPanel()                          # protecting a new file before sending it
        self.prep.cancel_clicked.connect(lambda: self._job is not None and self._job.cancel())
        l0.addWidget(self.prep)
        self.prep_error = Banner("", "danger")
        self.prep_error.hide()
        l0.addWidget(self.prep_error)
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
        self.people.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.people.itemSelectionChanged.connect(self._person_picked)
        self.people.itemDoubleClicked.connect(lambda _i: self._next())
        on_enter(self.people, self._next)
        l1.addWidget(self.people)
        self.people_hint = label(f"Tip: hold {_MOD} or Shift to send to several people.", "faint")
        l1.addWidget(self.people_hint)
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
        self.rv_fp_label = label("Their key fingerprint:", "muted")
        rl.addWidget(self.rv_fp_label)
        self.rv_fp = Fingerprint()
        rl.addWidget(self.rv_fp)
        self.trust_banner = Banner("", "warning", "I verified it", self._verify_now)
        rl.addWidget(self.trust_banner)
        self.send_btn = Button("Send securely", "primary", "send", "lg")
        self.send_btn.clicked.connect(self._send)
        send_row = QHBoxLayout()
        self.review_back = Button("Back", "ghost", "back")
        self.review_back.clicked.connect(self._back)
        send_row.addWidget(self.review_back)
        send_row.addStretch()
        send_row.addWidget(self.send_btn)
        rl.addLayout(send_row)
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
        dl.addWidget(art.Illustration("plane", 140), 0, Qt.AlignmentFlag.AlignHCenter)   # off it goes
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
        self._show_connection(self.ctl.relay_state)
        self._fill_files()
        self._fill_people()
        self._go(self.step if self.step < 3 else 0)

    def on_session_ended(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.entry_id = self.recipient = None
        self.entry_ids, self.recipients = [], []
        self._recipient_preset = False
        self.files_heading.setText("Which file do you want to send?")
        self._job = None
        self.search.clear()
        self.error.hide()
        self.done.hide()
        self.progress.finish()
        self._fill_files()
        self._fill_people()
        self._go(0)

    def send_to(self, name: str) -> None:
        """Start sending to `name` (from People & keys, or replying to an inbox item): pick a file, then review."""
        self.reset()
        self.recipient = name
        self.recipients = [name]
        self._recipient_preset = True
        self._fill_people()
        self.files_heading.setText(f"Which file should {name} get?")
        self._go(0)

    def preselect(self, entry_id: str) -> None:
        """Called from the Protect page's Send button: skip straight to choosing a person."""
        self.reset()
        self.entry_id = entry_id
        self.entry_ids = [entry_id]
        self._fill_files()
        self._go(1)

    # ── step 0/1 lists ───────────────────────────────────────────────────────
    def _fill_files(self) -> None:
        self.files.blockSignals(True)
        self.files.clear()
        everything = self.ctl.vault_entries()
        needle = self.file_search.text().strip().lower()
        entries = [e for e in everything if needle in e["original_filename"].lower()]
        self.file_search.setVisible(len(everything) > SEARCH_FROM)
        for e in entries:
            it = QListWidgetItem()
            it.setData(Qt.ItemDataRole.UserRole, e["id"])
            it.setSizeHint(QSize(0, 62))
            self.files.addItem(it)
            self.files.setItemWidget(it, ListRow(e["original_filename"], f"{human_size(e.get('size'))} · {friendly_date(e['date_vaulted'])}",
                                                   icon=file_icon(e["original_filename"])[0],
                                                   icon_kind=file_icon(e["original_filename"])[1]))
            if e["id"] == self.entry_id or e["id"] in self.entry_ids:
                it.setSelected(True)
                if e["id"] == self.entry_id:
                    self.files.setCurrentItem(it, self.files.selectionModel().SelectionFlag.Select)
        self.files.blockSignals(False)
        _fit_rows(self.files)
        self.files.setVisible(bool(entries))
        self.files_heading.setVisible(bool(everything))
        self.new_file_btn.setVisible(bool(everything))       # with no files, the empty state offers the same
        self.files_empty.setVisible(not entries)
        if everything and not entries:
            self.files_empty.set_text("No match", f"None of your files is called “{needle}”.")
        else:
            self.files_empty.set_text("Nothing to send yet", "Protect a file first, then come back here to send it.")
        if self.step == 0:
            self.next_btn.setVisible(bool(entries))          # nothing to continue with: the empty state's button leads
        self._update_nav()

    def _fill_people(self) -> None:
        self.people.blockSignals(True)
        self.people.clear()
        needle = self.search.text().strip().lower()
        contacts = [c for c in self.ctl.contacts() if needle in c["operator"].lower()]
        last = {}                                               # when I last sent to each person
        for o in self.ctl.outbox or []:
            last[o["to"]] = max(last.get(o["to"], 0), o.get("created", 0))
        # the people you actually send to come first, then verified people, then everyone alphabetically
        contacts.sort(key=lambda c: (-last.get(c["operator"], 0), self.ctl.trust(c["operator"]) != "verified",
                                     c["operator"].lower()))
        for c in contacts:
            trust = self.ctl.trust(c["operator"])
            text, kind = TRUST_PILL[trust]
            it = QListWidgetItem()
            it.setData(Qt.ItemDataRole.UserRole, c["operator"])
            it.setSizeHint(QSize(0, 62))
            self.people.addItem(it)
            sub = "Verified key" if trust == "verified" else "Key not verified yet"
            if c["operator"] in last:
                sub += f" · you last sent them a file {time_ago(last[c['operator']])}"
            self.people.setItemWidget(it, ListRow(c["operator"], sub, avatar=c["operator"], right=[Pill(text, kind)]))
            if c["operator"] == self.recipient or c["operator"] in self.recipients:
                it.setSelected(True)
                if c["operator"] == self.recipient:
                    self.people.setCurrentItem(it, self.people.selectionModel().SelectionFlag.Select)
        self.people.blockSignals(False)
        have = bool(contacts)
        _fit_rows(self.people)
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
        self.entry_ids = [i.data(Qt.ItemDataRole.UserRole) for i in items]
        self.entry_id = self.entry_ids[0] if self.entry_ids else None
        self._update_nav()

    def _person_picked(self) -> None:
        items = self.people.selectedItems()
        self.recipients = [i.data(Qt.ItemDataRole.UserRole) for i in items]
        self.recipient = self.recipients[0] if self.recipients else None
        self._update_nav()

    # ── wizard navigation ────────────────────────────────────────────────────
    # ── protect a new file, then carry on sending it ─────────────────────────
    def _pick_new_file(self) -> None:
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self, "Choose a file to send")
        if path:
            self.protect_and_continue(path)

    def protect_and_continue(self, path: str) -> None:
        """Protect `path` right here, then go straight to choosing who gets it (one flow instead of two pages)."""
        if self._job is not None:
            return
        self._go(0)
        self.prep_error.hide()
        self.new_file_btn.setEnabled(False)
        self.prep.start(f"Protecting {os.path.basename(path)} before sending")
        job = self.ctl.make_protect_job(path)
        job.page = "send"                                   # progress elsewhere leads back here, not to Protect
        self._job = job
        self._update_nav()
        self.ctl.run_job(job, on_success=self._prepared, on_fail=self._prep_failed, on_cancel=self._prep_cancelled,
                         on_progress=lambda text, pct: self.prep.update_progress(text, pct))

    def _prep_done(self) -> None:
        self._job = None
        self.prep.finish()
        self.new_file_btn.setEnabled(True)

    def _prepared(self, res) -> None:
        self._prep_done()
        self.ctl.after_protect(res)
        if res.storage_configured and res.upload_errors:
            self.ctl.toast.emit("Some pieces could not reach your cloud storage, so they travel inside the package. "
                                "Check your storage in Settings.", "warning")
        self.entry_id = res.entry["id"]
        self.entry_ids = [self.entry_id]
        self._fill_files()
        self._next()                                        # straight on to "Choose a person"

    def _prep_failed(self, message: str) -> None:
        self._prep_done()
        self._update_nav()
        self.prep_error.set(message, "danger")
        motion.reveal(self.prep_error)

    def _prep_cancelled(self) -> None:
        self._prep_done()
        self._update_nav()

    def _go(self, i: int) -> None:
        self.step = i
        self.stack.setCurrentIndex(min(i, 2))
        self.stepper.set_current(i)
        self.stepper.clickable = i < 3 and self._job is None
        self.back_btn.setVisible(i == 1)                   # step 3 has its own Back beside "Send securely"
        self.next_btn.setVisible(i == 1 or (i == 0 and self.files.count() > 0))   # no files: the empty state leads
        if i == 2:
            self._fill_review()
        self._update_nav()

    def _chosen_files(self) -> list[str]:
        ids = [i for i in self.entry_ids if i]
        return ids if len(ids) > 1 else ([self.entry_id] if self.entry_id else [])

    def _chosen_people(self) -> list[str]:
        names = [n for n in self.recipients if n]
        return names if len(names) > 1 else ([self.recipient] if self.recipient else [])

    def _update_nav(self) -> None:
        if self.step == 0:
            n = len(self._chosen_files())
            self.next_btn.setEnabled(n > 0 and self._job is None)
            self.next_btn.setText(f"Continue with {n} files" if n > 1 else "Continue")
        elif self.step == 1:
            n = len(self._chosen_people())
            self.next_btn.setEnabled(n > 0)
            self.next_btn.setText(f"Continue with {n} people" if n > 1 else "Continue")
        self.files_hint.setVisible(self.files.count() > 1)
        self.people_hint.setVisible(self.people.count() > 1)

    def _back(self) -> None:
        if self._job is None:
            self._go(max(0, self.step - 1))

    def _escape_back(self) -> None:
        if self._job is None and self.step in (1, 2):
            self._back()

    def _step_clicked(self, i: int) -> None:
        """A finished step in the indicator was clicked: go back to it (not while working, not once sent)."""
        if self._job is None and self.step < 3:
            self._go(i)

    def _next(self) -> None:
        if self.step == 0 and self.entry_id and self._recipient_preset and self.recipient:
            self._go(2)                                        # the person was chosen before the file: review now
        elif self.step == 0 and self.entry_id:
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
        if self._is_batch():
            self._fill_batch_review()
            return
        for w in (self.rv_fp_label, self.rv_fp):
            w.show()
        self.rv_file.key_label.setText("File")
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

    def _is_batch(self) -> bool:
        return len(self._chosen_files()) > 1 or len(self._chosen_people()) > 1

    def _fill_batch_review(self) -> None:
        """Several files and/or people: one summary instead of one fingerprint."""
        entries = [e for e in (VaultLedger.get(i) for i in self._chosen_files()) if e]
        people = self._chosen_people()
        names = [e["original_filename"] for e in entries]
        self.rv_file.key_label.setText("Files" if len(names) > 1 else "File")
        self.rv_file.set(", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else ""))
        self.rv_size.set(human_size(sum(e.get("size") or 0 for e in entries)))
        self.rv_avatar.set_name(people[0] if people else "?")
        self.rv_name.setText(", ".join(people[:3]) + (f" and {len(people) - 3} more" if len(people) > 3 else ""))
        unverified = [p for p in people if self.ctl.trust(p) != "verified"]
        ok = len(people) - len(unverified)
        self.rv_pill.set(f"{ok} of {len(people)} verified" if len(people) > 1 else
                         TRUST_PILL[self.ctl.trust(people[0])][0], "success" if not unverified else "warning")
        for w in (self.rv_fp_label, self.rv_fp):              # one fingerprint cannot describe several people
            w.hide()
        self.trust_banner._btn.hide()
        if not unverified:
            self.trust_banner.set("You verified everyone's key, so only they can open these files.", "success")
        else:
            who = ", ".join(unverified[:3]) + (f" and {len(unverified) - 3} more" if len(unverified) > 3 else "")
            self.trust_banner.set(f"Not verified yet: {who}. If these files are sensitive, compare fingerprints in "
                                  "People & keys first.", "warning")
        n = len(entries) * len(people)
        self.send_btn.setText(f"Send {len(entries)} file{'s' if len(entries) != 1 else ''} to "
                              f"{len(people)} {'people' if len(people) != 1 else 'person'} ({n} sends)")

    def _verify_now(self) -> None:
        if self.recipient and verify_fingerprint(self, self.recipient, self.rv_fp.raw()):
            self.ctl.verify_contact(self.recipient)
            self._fill_review()

    def _send(self) -> None:
        if self._is_batch():
            self._send_batch()
            return
        entry = VaultLedger.get(self.entry_id or "")
        if not entry or not self.recipient or self._job is not None:
            return
        self.error.hide()
        self.review.hide()
        self.back_btn.setEnabled(False)
        self.stepper.clickable = False
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
        self.ctl.after_send(entry, result["to"], result.get("id", ""))
        self.done_title.setText(f"Sent to {result['to']}")
        self.done_text.setText(f"“{entry['original_filename']}” is in {result['to']}'s inbox. They choose whether to accept it, "
                               "and you'll get a notification when they do.")
        motion.reveal(self.done, motion.SLOW)
        self.stepper.set_current(3)
        self.stepper.clickable = False
        self.next_btn.hide()
        self.back_btn.hide()
        self.notify_if_away(f"Sent to {result['to']}.", "success")

    def _failed(self, message: str) -> None:
        self._job = None
        self.progress.finish()
        self.stepper.clickable = True
        self.review.show()
        self.back_btn.setEnabled(True)
        self.error.set(message, "danger")
        motion.reveal(self.error)
        if self.entry_id and self.recipient:
            self.send_btn.setText("Resume sending" if self.ctl.has_pending_upload(self.entry_id, self.recipient) else "Try again")

    def _cancelled(self) -> None:
        self._job = None
        self.progress.finish()
        self.stepper.clickable = True
        self.review.show()
        self.back_btn.setEnabled(True)
        self.ctl.toast.emit("Sending cancelled. Nothing was delivered.", "info")

    # ── several files to several people: one send after another ───────────────
    def _send_batch(self) -> None:
        if self._job is not None:
            return
        entries = [e for e in (VaultLedger.get(i) for i in self._chosen_files()) if e]
        self._batch = [(e, p) for e in entries for p in self._chosen_people()]
        self._batch_done, self._batch_failed, self._batch_total = [], [], len(self._batch)
        if not self._batch:
            return
        self.error.hide()
        self.review.hide()
        self.back_btn.setEnabled(False)
        self.stepper.clickable = False
        self._batch_next()

    def _batch_next(self) -> None:
        entry, person = self._batch.pop(0)
        n = len(self._batch_done) + len(self._batch_failed) + 1
        self.progress.start(f"Sending {n} of {self._batch_total}: {entry['original_filename']} to {person}")
        try:
            job = self.ctl.make_send_job(entry, person)
        except Exception as exc:
            self._batch_failed_one(entry, person, str(exc))
            return
        self._job = job
        self.ctl.run_job(job, on_success=lambda r, e=entry: self._batch_sent_one(e, r),
                         on_fail=lambda m, e=entry, p=person: self._batch_failed_one(e, p, m),
                         on_cancel=self._batch_cancelled,
                         on_progress=lambda text, pct: self.progress.update_progress(text, pct))

    def _batch_sent_one(self, entry: dict, result: dict) -> None:
        self._job = None
        self.ctl.after_send(entry, result["to"], result.get("id", ""))
        self._batch_done.append((entry["original_filename"], result["to"]))
        self._batch_continue()

    def _batch_failed_one(self, entry: dict, person: str, message: str) -> None:
        self._job = None
        self._batch_failed.append((entry["original_filename"], person, message))
        self._batch_continue()

    def _batch_continue(self) -> None:
        if self._batch:                                     # one failure does not stop the others
            self._batch_next()
            return
        self.progress.finish()
        done, failed = self._batch_done, self._batch_failed
        if not done:                                        # nothing went: back to the review with the reason
            self.stepper.clickable = True
            self.review.show()
            self.back_btn.setEnabled(True)
            self.error.set(f"Nothing was sent. {failed[0][2]}", "danger")
            motion.reveal(self.error)
            return
        people = sorted({p for _f, p in done})
        self.done_title.setText(f"Sent {len(done)} of {self._batch_total}" if failed else
                                f"Sent to {', '.join(people[:3])}" + (" and more" if len(people) > 3 else ""))
        text = f"{len(done)} send{'s' if len(done) != 1 else ''} are waiting in people's inboxes. You'll get a " \
               "notification as each is accepted."
        if failed:
            why = "; ".join(f"{f} to {p}: {m}" for f, p, m in failed[:3]) + ("; …" if len(failed) > 3 else "")
            text += f" Not sent: {why}. Send those again when the problem is fixed."
        self.done_text.setText(text)
        motion.reveal(self.done, motion.SLOW)
        self.stepper.set_current(3)
        self.stepper.clickable = False
        self.next_btn.hide()
        self.back_btn.hide()
        self.notify_if_away(f"Sent {len(done)} of {self._batch_total}." if failed else f"Sent {len(done)} files.",
                            "warning" if failed else "success")

    def _batch_cancelled(self) -> None:
        left = len(self._batch)
        self._batch = []
        self._job = None
        self.progress.finish()
        self.stepper.clickable = True
        self.review.show()
        self.back_btn.setEnabled(True)
        sent = len(self._batch_done)
        self.ctl.toast.emit(f"Stopped. {sent} sent, {left + 1} not sent." if sent else "Sending cancelled. Nothing was "
                            "delivered.", "info")

    def _cancel(self) -> None:
        if self._job:
            self._job.cancel()
            self.progress.cancel_btn.setEnabled(False)
            self.progress.detail.setText("Cancelling…")
