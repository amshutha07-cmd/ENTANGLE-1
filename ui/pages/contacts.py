"""ui/pages/contacts.py — your identity, the people you know, and how to be sure it's really them."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

from ui.dialogs import confirm, info
from ui.pages.base import Page
from ui.widgets import (
    Avatar, Banner, Button, Card, EmptyState, Fingerprint, ListRow, Pill, label,
)

TRUST_PILL = {"verified": ("Verified", "success"), "unverified": ("Not verified", "warning"),
              "unknown": ("New", "neutral"), "changed": ("Key changed", "danger")}


class ContactsPage(Page):
    navigate = pyqtSignal(str)

    def __init__(self, ctl):
        super().__init__(ctl, "People & keys",
                         "Everyone has a key fingerprint. If yours matches what they see, nobody is impersonating either of you.")
        self._selected: Optional[str] = None

        # my identity
        me = Card(padding=20, spacing=8)
        top = QHBoxLayout()
        self.me_avatar = Avatar("?", 44)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.me_name = label("", "h2", wrap=False)
        col.addWidget(self.me_name)
        col.addWidget(label("Read this fingerprint to the people you want to trust you.", "muted"))
        top.addWidget(self.me_avatar)
        top.addLayout(col, 1)
        export = Button("Export my ID file", "secondary", "upload", "sm")
        export.clicked.connect(self._export)
        top.addWidget(export)
        me.body.addLayout(top)
        self.me_fp = Fingerprint()
        me.body.addWidget(self.me_fp)
        self.root.addWidget(me)

        # contacts master/detail
        bar = QHBoxLayout()
        bar.addWidget(label("PEOPLE YOU CAN SEND TO", "eyebrow", wrap=False), 1, Qt.AlignmentFlag.AlignBottom)
        refresh = Button("Refresh from relay", "secondary", "refresh", "sm")
        refresh.clicked.connect(self._refresh_directory)
        imp = Button("Import an ID file", "secondary", "download", "sm")
        imp.clicked.connect(self._import)
        bar.addWidget(refresh)
        bar.addWidget(imp)
        self.root.addLayout(bar)
        self.msg = label("", "muted")
        self.msg.hide()                                   # only takes space while there is something to say
        self.root.addWidget(self.msg)
        self.search = QLineEdit()                         # lives in the list it filters (see below)
        self.search.setPlaceholderText("Search people")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)

        row = QHBoxLayout()
        row.setSpacing(16)
        left = Card(padding=8, spacing=8)
        left.body.addWidget(self.search)
        self.list = QListWidget()
        self.list.setMinimumHeight(260)
        self.list.itemSelectionChanged.connect(self._picked)
        left.body.addWidget(self.list)
        self.empty = EmptyState("users", "No one yet", "Press “Refresh from relay” to find people, or import an ID file someone sent you.")
        left.body.addWidget(self.empty)

        self.detail = Card(padding=20, spacing=10)
        head = QHBoxLayout()
        self.d_avatar = Avatar("?", 46)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.d_name = label("", "h2", wrap=False)
        self.d_src = label("", "muted", wrap=False)
        col.addWidget(self.d_name)
        col.addWidget(self.d_src)
        self.d_pill = Pill("", "neutral")
        head.addWidget(self.d_avatar)
        head.addLayout(col, 1)
        head.addWidget(self.d_pill, 0, Qt.AlignmentFlag.AlignTop)
        self.detail.body.addLayout(head)
        self.detail.body.addWidget(label("Their key fingerprint:", "muted"))
        self.d_fp = Fingerprint()
        self.detail.body.addWidget(self.d_fp)
        self.d_banner = Banner("", "warning")
        self.detail.body.addWidget(self.d_banner)
        self.verify_btn = Button("I compared it — mark as verified", "primary", "check")
        self.verify_btn.clicked.connect(self._verify)
        self.remove_btn = Button("Remove this person", "ghost", "trash", "sm")
        self.remove_btn.clicked.connect(self._remove)
        self.detail.body.addWidget(self.verify_btn)
        self.detail.body.addWidget(self.remove_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.detail.body.addStretch()
        # Details (with their buttons) on the left: notifications stack up in the window's bottom-right corner.
        row.addWidget(self.detail, 6)
        row.addWidget(left, 5)
        self.root.addLayout(row)
        self.root.addStretch(1)

        ctl.contacts_changed.connect(self.refresh)
        ctl.session_started.connect(lambda _n: self.refresh())

    def on_show(self) -> None:
        self.refresh()

    def on_session_ended(self) -> None:
        self.me_fp.set("")
        self._selected = None

    def select(self, name: str) -> None:
        self._selected = name
        self.refresh()

    # ── data ─────────────────────────────────────────────────────────────────
    def refresh(self, *_a) -> None:
        name = self.ctl.operator or ""
        self.me_name.setText(name or "—")
        self.me_avatar.set_name(name or "?")
        self.me_fp.set(self.ctl.my_fingerprint())
        needle = self.search.text().strip().lower()
        everyone = self.ctl.contacts()
        contacts = [c for c in everyone if needle in c["operator"].lower()]
        self.search.setVisible(bool(everyone))
        if everyone:
            self.empty.set_text("No match", f"Nobody called “{self.search.text().strip()}”. Check the spelling, or press "
                                            "“Refresh from relay”.")
        else:
            self.empty.set_text("No one yet", "Press “Refresh from relay” to find people, or import an ID file someone "
                                              "sent you.")
        self.list.blockSignals(True)
        self.list.clear()
        for c in contacts:
            trust = self.ctl.trust(c["operator"])
            text, kind = TRUST_PILL[trust]
            li = QListWidgetItem()
            li.setData(Qt.ItemDataRole.UserRole, c["operator"])
            li.setSizeHint(QSize(0, 62))
            self.list.addItem(li)
            self.list.setItemWidget(li, ListRow(c["operator"], c.get("fingerprint", "")[:26] + "…", avatar=c["operator"],
                                                right=[Pill(text, kind)]))
            if c["operator"] == self._selected:
                self.list.setCurrentItem(li)
        self.list.blockSignals(False)
        if contacts and self.list.currentItem() is None:
            self.list.setCurrentRow(0)
        self.list.setVisible(bool(contacts))
        self.empty.setVisible(not contacts)
        self._picked()

    def _picked(self) -> None:
        li = self.list.currentItem()
        self._selected = li.data(Qt.ItemDataRole.UserRole) if li else None
        self.detail.setVisible(bool(self._selected))
        if not self._selected:
            return
        from security_core import SecurityCore
        c = SecurityCore.get_contact_info(self._selected) or {}
        trust = self.ctl.trust(self._selected)
        text, kind = TRUST_PILL[trust]
        self.d_avatar.set_name(self._selected)
        self.d_name.setText(self._selected)
        src = c.get("source", "")
        self.d_src.setText("Imported from a file you received" if "file-import" in src else
                           "Found on the relay" if "relay" in src else "Seen on your network" if "lan" in src else "")
        self.d_pill.set(text, kind)
        self.d_fp.set(c.get("fingerprint", ""))
        if trust == "verified":
            self.d_banner.set("You confirmed this fingerprint. Files you send them can only be opened by them.", "success")
        else:
            self.d_banner.set("Ask them to read you the fingerprint of THEIR key (People & keys → their own screen), "
                              "on a phone call or in person. If it matches the one above exactly, mark it verified.", "warning")
        self.verify_btn.setVisible(trust != "verified")

    # ── actions ──────────────────────────────────────────────────────────────
    def _verify(self) -> None:
        if self._selected and confirm(
                self, "Confirm fingerprint",
                f"Did {self._selected} read you EXACTLY this fingerprint, over a channel you trust?\n\n{self.d_fp.raw()}",
                ok="Yes, it matches"):
            self.ctl.verify_contact(self._selected)

    def _remove(self) -> None:
        if self._selected and confirm(self, "Remove this person?",
                                      f"{self._selected} is removed from your list. They come back (unverified) if you refresh from the relay.",
                                      ok="Remove", danger=True):
            self.ctl.remove_contact(self._selected)
            self._selected = None

    def _say(self, text: str) -> None:
        self.msg.setText(text)
        self.msg.setVisible(bool(text))

    def _refresh_directory(self) -> None:
        self._say("Looking people up…")
        self.ctl.run_job(self.ctl.make_directory_job(),
                         on_success=lambda r: (self._say(""), self.ctl.contacts_changed.emit()),
                         on_fail=self._say)

    def _export(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Where should the ID file be saved?")
        if folder:
            try:
                path = self.ctl.export_identity(folder)
                info(self, "ID file saved", f"{path}\n\nSend this file to the people you want to trust you. It contains no secrets.",
                     "success")
            except Exception as exc:
                info(self, "Could not save", str(exc), "error")

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose an ID file", "", "ANSX ID (*.ansx_id)")
        if path:
            try:
                name = self.ctl.import_contact(path)
                self.select(name)
                info(self, "Contact added", f"{name} was added and marked verified, because you received their file directly.", "success")
            except Exception as exc:
                info(self, "Could not import", str(exc), "error")
