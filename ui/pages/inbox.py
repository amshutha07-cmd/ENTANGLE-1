"""ui/pages/inbox.py — files people sent you (accept or decline), and what happened to the files you sent."""
from __future__ import annotations

import datetime
import os
import time
from typing import Optional

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFileDialog, QHBoxLayout, QListWidget, QListWidgetItem, QStackedWidget, QVBoxLayout, QWidget,
)

from relay_client import RelayError
from ui.controller import Job
from ui.dialogs import confirm
from ui.pages.base import Page
from ui.pages.vault import open_folder
from ui.widgets import (
    Avatar, Banner, Button, Card, EmptyState, Fingerprint, KeyValue, ListRow, Pill, ProgressPanel,
    human_size, label, time_ago,
)

TRUST_PILL = {"verified": ("Verified", "success"), "unverified": ("Not verified", "warning"),
              "unknown": ("New sender", "neutral"), "changed": ("Key changed", "danger")}
SENT_PILL = {"uploading": ("Uploading", "info"), "ready": ("Waiting for pickup", "warning"),
             "delivered": ("Delivered", "success"), "rejected": ("Declined", "danger"),
             "expired": ("Expired", "neutral"), "cancelled": ("Cancelled", "neutral")}


def _days_left(expires: int) -> str:
    left = int(expires) - int(time.time())
    if left <= 0:
        return "expired"
    if left < 3600:
        return f"{left // 60} minutes"
    if left < 86400:
        return f"{left // 3600} hours"
    return f"{left // 86400} days"


class InboxPage(Page):
    navigate = pyqtSignal(str)

    def __init__(self, ctl):
        super().__init__(ctl, "Inbox", "Nothing is opened until you accept it.")
        self._job: Optional[Job] = None
        self._current: Optional[dict] = None

        self.open_btn = Button("Open a package file…", "ghost", "folder", "sm")
        self.open_btn.setToolTip("For a package you received outside the relay (USB stick, chat, e-mail).")
        self.open_btn.clicked.connect(self._open_package)
        self.actions.addWidget(self.open_btn)

        tabs = QHBoxLayout()
        tabs.setSpacing(6)
        self.tab_in = Button("Received", "secondary", "inbox", "sm")
        self.tab_out = Button("Sent", "ghost", "send", "sm")
        self.tab_in.clicked.connect(lambda: self.show_tab("received"))
        self.tab_out.clicked.connect(lambda: self.show_tab("sent"))
        tabs.addWidget(self.tab_in)
        tabs.addWidget(self.tab_out)
        tabs.addStretch()
        self.root.addLayout(tabs)

        self.progress = ProgressPanel()
        self.progress.cancel_clicked.connect(self._cancel)
        self.root.addWidget(self.progress)
        self.result = Banner("", "success", "Show in folder", lambda: open_folder(self._last_path) if self._last_path else None)
        self._last_path = ""
        self.result.hide()
        self.root.addWidget(self.result)

        self.stack = QStackedWidget()
        self.root.addWidget(self.stack, 1)

        # ── received: list + detail ──
        rec = QWidget()
        rl = QHBoxLayout(rec)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(16)
        left = Card(padding=8, spacing=0)
        self.inbox_list = QListWidget()
        self.inbox_list.setMinimumWidth(320)
        self.inbox_list.itemSelectionChanged.connect(self._picked)
        left.body.addWidget(self.inbox_list)
        self.inbox_empty = EmptyState("inbox", "Inbox is empty", "When someone sends you a file it appears here within seconds.",
                                      "Copy my name", self._copy_my_name)
        left.body.addWidget(self.inbox_empty)
        rl.addWidget(left, 5)

        self.detail = Card(padding=22, spacing=10)
        head = QHBoxLayout()
        self.d_avatar = Avatar("?", 46)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.d_from = label("", "h2", wrap=False)
        self.d_sub = label("", "muted", wrap=False)
        col.addWidget(self.d_from)
        col.addWidget(self.d_sub)
        self.d_pill = Pill("", "neutral")
        head.addWidget(self.d_avatar)
        head.addLayout(col, 1)
        head.addWidget(self.d_pill, 0, Qt.AlignmentFlag.AlignTop)
        self.detail.body.addLayout(head)
        self.d_size = KeyValue("Size")
        self.d_expires = KeyValue("Available for")
        self.detail.body.addWidget(self.d_size)
        self.detail.body.addWidget(self.d_expires)
        self.detail.body.addWidget(label("Sender's key fingerprint:", "muted"))
        self.d_fp = Fingerprint()
        self.detail.body.addWidget(self.d_fp)
        self.d_banner = Banner("", "warning", "I verified it", self._verify)
        self.detail.body.addWidget(self.d_banner)
        self.accept_btn = Button("Accept and open", "primary", "download", "lg")
        self.decline_btn = Button("Decline", "danger", "x")
        self.accept_btn.clicked.connect(self._accept)
        self.decline_btn.clicked.connect(self._decline)
        row = QHBoxLayout()
        row.addWidget(self.accept_btn, 1)
        row.addWidget(self.decline_btn)
        self.detail.body.addLayout(row)
        self.detail.body.addStretch()
        rl.addWidget(self.detail, 6)
        self.stack.addWidget(rec)

        # ── sent ──
        sent = QWidget()
        sl = QVBoxLayout(sent)
        sl.setContentsMargins(0, 0, 0, 0)
        card = Card(padding=8, spacing=0)
        self.sent_list = QListWidget()
        card.body.addWidget(self.sent_list)
        self.sent_empty = EmptyState("send", "Nothing sent yet", "Files you send will be tracked here until they're picked up.")
        card.body.addWidget(self.sent_empty)
        sl.addWidget(card)
        self.cancel_out_btn = Button("Cancel this transfer", "danger", "x", "sm")
        self.cancel_out_btn.setToolTip("Only possible until the receiver has picked it up.")
        self.cancel_out_btn.clicked.connect(self._cancel_outgoing)
        self.cancel_out_btn.hide()
        sl.addWidget(self.cancel_out_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.sent_list.itemSelectionChanged.connect(self._sent_picked)
        self.stack.addWidget(sent)

        ctl.inbox_changed.connect(self._fill_inbox)
        ctl.outbox_changed.connect(self._fill_sent)
        ctl.contacts_changed.connect(self._fill_inbox)
        self._fill_inbox([])
        self._fill_sent([])

    # ── tabs ─────────────────────────────────────────────────────────────────
    def show_tab(self, which: str) -> None:
        received = which == "received"
        self.stack.setCurrentIndex(0 if received else 1)
        self.tab_in.setProperty("variant", "secondary" if received else "ghost")
        self.tab_out.setProperty("variant", "ghost" if received else "secondary")
        for b in (self.tab_in, self.tab_out):
            b.style().unpolish(b)
            b.style().polish(b)
            b.refresh_icon()

    def on_show(self) -> None:
        self._fill_inbox(self.ctl.inbox)
        self._fill_sent(self.ctl.outbox)

    def on_session_ended(self) -> None:
        self._current = None
        self.result.hide()
        self.progress.finish()
        self._fill_inbox([])
        self._fill_sent([])

    # ── received list ────────────────────────────────────────────────────────
    def _fill_inbox(self, items: Optional[list] = None) -> None:
        items = self.ctl.inbox if items is None or not isinstance(items, list) else items
        keep = self._current["id"] if self._current else None
        self.inbox_list.blockSignals(True)
        self.inbox_list.clear()
        for it in items:
            trust = self.ctl.trust(it["from"], it.get("sender_fingerprint", ""))
            text, kind = TRUST_PILL[trust]
            li = QListWidgetItem()
            li.setData(Qt.ItemDataRole.UserRole, it)
            li.setSizeHint(QSize(0, 62))
            self.inbox_list.addItem(li)
            self.inbox_list.setItemWidget(li, ListRow(it["from"], f"{human_size(it['size'])} · {time_ago(it['created'])}",
                                                      avatar=it["from"], right=[Pill(text, kind)]))
            if it["id"] == keep:
                self.inbox_list.setCurrentItem(li)
        self.inbox_list.blockSignals(False)
        if items and self.inbox_list.currentItem() is None:
            self.inbox_list.setCurrentRow(0)
        self.inbox_list.setVisible(bool(items))
        self.inbox_empty.setVisible(not items)
        if not items and self.ctl.operator:
            self.inbox_empty.set_text("Inbox is empty", f"People send you files by your name, {self.ctl.operator}. Share it "
                                                        "with them; a new file appears here within seconds.")
        self._picked()

    def _copy_my_name(self) -> None:
        from PyQt6.QtWidgets import QApplication
        if self.ctl.operator:
            QApplication.clipboard().setText(self.ctl.operator)
            self.ctl.toast.emit(f"Copied “{self.ctl.operator}”. Send it to the people who will send you files.", "success")

    def _picked(self) -> None:
        li = self.inbox_list.currentItem()
        self._current = li.data(Qt.ItemDataRole.UserRole) if li else None
        self.detail.setVisible(bool(self._current))
        it = self._current
        if not it:
            return
        trust = self.ctl.trust(it["from"], it.get("sender_fingerprint", ""))
        text, kind = TRUST_PILL[trust]
        self.d_avatar.set_name(it["from"])
        self.d_from.setText(it["from"])
        self.d_sub.setText(f"Sent {time_ago(it['created'])}")
        self.d_pill.set(text, kind)
        self.d_size.set(human_size(it["size"]))
        self.d_expires.set(_days_left(it["expires"]))
        self.d_fp.set(it.get("sender_fingerprint", ""))
        b = self.d_banner
        if trust == "verified":
            b.set(f"You verified {it['from']}'s key, so this really is from them.", "success")
            b._btn.hide()
        elif trust == "changed":
            b.set(f"WARNING: {it['from']}'s key is different from the one you saved. Someone may be pretending to be them. "
                  "Do not accept unless you have confirmed with them another way.", "danger")
            b._btn.hide()
        else:
            b.set(f"You haven't verified {it['from']}'s key. Compare the fingerprint above with them by phone before "
                  "opening anything sensitive.", "warning")
            b._btn.setVisible(trust == "unverified")
        busy = self._job is not None
        self.accept_btn.setEnabled(not busy)
        self.decline_btn.setEnabled(not busy)

    def _verify(self) -> None:
        it = self._current
        if it and confirm(self, "Confirm fingerprint",
                          f"Did {it['from']} read you exactly this fingerprint, over a channel you trust?\n\n{it.get('sender_fingerprint', '')}",
                          ok="Yes, it matches"):
            self.ctl.verify_contact(it["from"])
            self._picked()

    # ── accept / decline ─────────────────────────────────────────────────────
    def _accept(self) -> None:
        it = self._current
        if not it or self._job is not None:
            return
        trust = self.ctl.trust(it["from"], it.get("sender_fingerprint", ""))
        if trust == "changed" and not confirm(
                self, "Key has changed",
                f"{it['from']}'s key differs from the one you saved. Only continue if you have confirmed with them another way.",
                ok="Accept anyway", danger=True):
            return
        self.result.hide()
        self.accept_btn.setEnabled(False)
        self.decline_btn.setEnabled(False)
        self.progress.start(f"Receiving from {it['from']}")
        try:
            job = self.ctl.make_accept_job(it)
        except Exception as exc:
            self._failed(str(exc))
            return
        self._job = job
        self.ctl.run_job(job, on_success=self._received, on_fail=self._failed, on_cancel=self._cancelled,
                         on_progress=lambda text, pct: self.progress.update_progress(text, pct))

    def _received(self, info: dict) -> None:
        self._job = None
        self.progress.finish()
        self.ctl.after_receive(info)
        self._last_path = info["path"]
        note, kind = self.ctl.signature_note(info)
        self.result.set(f"Saved “{os.path.basename(info['path'])}” from {info['from']} to your Downloads folder. {note}", kind)
        self.result.show()
        self.notify_if_away(f"Received {os.path.basename(info['path'])}.", "success")
        self._picked()
        open_folder(info["path"])

    def _failed(self, message: str) -> None:
        self._job = None
        self.progress.finish()
        self.result.set(message, "danger")
        self._last_path = ""
        self.result._btn.hide()
        self.result.show()
        self._picked()

    def _cancelled(self) -> None:
        self._job = None
        self.progress.finish()
        self.ctl.toast.emit("Cancelled. The file is still in your inbox.", "info")
        self._picked()

    def _cancel(self) -> None:
        if self._job:
            self._job.cancel()
            self.progress.cancel_btn.setEnabled(False)

    def _decline(self) -> None:
        it = self._current
        if not it or not confirm(self, "Decline this file?",
                                 f"The file from {it['from']} will be deleted from the relay. They will see that you declined.",
                                 ok="Decline", danger=True):
            return
        try:
            self.ctl.decline(it)
            self.ctl.toast.emit("Declined and deleted.", "info")
        except Exception as exc:
            msg = exc.detail if isinstance(exc, RelayError) else str(exc)
            self.ctl.toast.emit(f"Could not decline: {msg}", "error")

    def _open_package(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open a package file", "", "Packages (*.png)")
        if not path or self._job is not None:
            return
        self.result.hide()
        self.progress.start("Opening the package")
        job = self.ctl.make_open_package_job(path)
        self._job = job
        self.ctl.run_job(job, on_success=lambda info: self._received({**info, "from": info.get("signer") or "a package file"}),
                         on_fail=self._failed, on_cancel=self._cancelled,
                         on_progress=lambda text, pct: self.progress.update_progress(text, pct))

    # ── sent list ────────────────────────────────────────────────────────────
    def _fill_sent(self, items: Optional[list] = None) -> None:
        items = self.ctl.outbox if items is None or not isinstance(items, list) else items
        self.sent_list.clear()
        for it in items:
            text, kind = SENT_PILL.get(it["state"], (it["state"], "neutral"))
            when = datetime.datetime.fromtimestamp(it["created"]).strftime("%b %d, %H:%M")
            right = [Pill(text, kind)]
            li = QListWidgetItem()
            li.setSizeHint(QSize(0, 62))
            li.setData(Qt.ItemDataRole.UserRole, it)
            self.sent_list.addItem(li)
            self.sent_list.setItemWidget(li, ListRow(f"To {it['to']}", f"{human_size(it['size'])} · {when}",
                                                     avatar=it["to"], right=right))
        self.sent_list.setVisible(bool(items))
        self.sent_empty.setVisible(not items)
        self.cancel_out_btn.hide()

    def _sent_picked(self) -> None:
        li = self.sent_list.currentItem()
        it = li.data(Qt.ItemDataRole.UserRole) if li else None
        self.cancel_out_btn.setVisible(bool(it) and it["state"] in ("ready", "uploading"))

    def _cancel_outgoing(self) -> None:
        li = self.sent_list.currentItem()
        it = li.data(Qt.ItemDataRole.UserRole) if li else None
        if not it or not confirm(self, "Cancel this transfer?",
                                 f"The file for {it['to']} will be deleted from the relay so they cannot pick it up.",
                                 ok="Cancel transfer", danger=True):
            return
        try:
            self.ctl.cancel_outgoing(it["id"])
        except Exception as exc:
            msg = exc.detail if isinstance(exc, RelayError) else str(exc)
            self.ctl.toast.emit(f"Could not cancel: {msg}", "error")
