"""ui/pages/vault.py — protect a file, and manage the files you have already protected."""
from __future__ import annotations

import os
from typing import Optional

from PyQt6.QtCore import QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from ui.controller import Job
from ui.dialogs import confirm
from ui.pages.base import Page
from ui.widgets import (
    clear_layout,
    Banner, Button, Card, DropZone, EmptyState, IconBadge, ProgressPanel, file_icon, friendly_date, human_size, label,
)


def open_folder(path: str) -> None:
    folder = path if os.path.isdir(path) else os.path.dirname(path)
    QDesktopServices.openUrl(QUrl.fromLocalFile(folder))


class VaultRow(QWidget):
    send_clicked = pyqtSignal(str)
    restore_clicked = pyqtSignal(str)
    remove_clicked = pyqtSignal(str)

    def __init__(self, entry: dict):
        super().__init__()
        self.entry_id = entry["id"]
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(14)
        lay.addWidget(IconBadge(*file_icon(entry["original_filename"]), 42))
        col = QVBoxLayout()
        col.setSpacing(2)
        name = label(entry["original_filename"], "body", wrap=False)
        name.setToolTip(entry["original_filename"])
        name.setStyleSheet("font-weight: 650;")
        st = entry.get("storage") or {}
        cloud, inline = st.get("cloud", 0), st.get("inline", 0)
        where = (f"{cloud} of {cloud + inline} pieces in cloud storage" if cloud
                 else "all pieces inside the package" if st else "")
        sub = " · ".join(x for x in (human_size(entry.get("size")), friendly_date(entry["date_vaulted"]), where) if x and x != "—")
        col.addWidget(name)
        col.addWidget(label(sub, "muted", wrap=False))
        lay.addLayout(col, 1)
        send = Button("Send", "primary", "send", "sm")
        restore = Button("Restore", "secondary", "download", "sm")
        remove = Button("", "ghost", "trash", "sm")
        remove.setToolTip("Remove from my vault")
        send.clicked.connect(lambda: self.send_clicked.emit(self.entry_id))
        restore.clicked.connect(lambda: self.restore_clicked.emit(self.entry_id))
        remove.clicked.connect(lambda: self.remove_clicked.emit(self.entry_id))
        for b in (send, restore, remove):
            lay.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)


class VaultPage(Page):
    navigate = pyqtSignal(str)
    send_requested = pyqtSignal(str)          # entry id

    def __init__(self, ctl):
        super().__init__(ctl, "Protect a file",
                         "Your file is encrypted, split into 12 pieces, and stored safely. Any 8 pieces can rebuild it.")
        self._job: Optional[Job] = None

        self.storage_banner = Banner(
            "Cloud storage isn't set up yet. Your pieces are kept inside the protected package, which works for files up to "
            "10 MB. Add cloud storage for larger files and extra safety.", "info", "Set up storage",
            lambda: self.navigate.emit("settings"))
        self.root.addWidget(self.storage_banner)

        self.drop = DropZone("Drop a file here to protect it", "or click to choose one from your computer")
        self.drop.file_chosen.connect(self.protect_file)
        self.root.addWidget(self.drop)

        self.progress = ProgressPanel()
        self.progress.cancel_clicked.connect(self._cancel)
        self.root.addWidget(self.progress)

        self.result = Banner("", "success")
        self.result.hide()
        self.root.addWidget(self.result)

        self.root.addWidget(label("YOUR PROTECTED FILES", "eyebrow", wrap=False))
        self.list_card = Card(padding=6, spacing=0)
        self.root.addWidget(self.list_card)
        self.root.addStretch(1)

        ctl.vault_changed.connect(self.refresh)

    def on_show(self) -> None:
        self.storage_banner.setVisible(not self.ctl.storage_targets())
        self.refresh()

    def on_session_ended(self) -> None:
        self.result.hide()
        self.progress.finish()

    # ── protecting ───────────────────────────────────────────────────────────
    def protect_file(self, path: str) -> None:
        if self._job is not None:
            return
        self.result.hide()
        self.drop.setEnabled(False)
        self.progress.start(f"Protecting {os.path.basename(path)}")
        job = self.ctl.make_protect_job(path)
        self._job = job
        self.ctl.run_job(job, on_success=self._protected, on_fail=self._failed, on_cancel=self._cancelled,
                         on_progress=lambda text, pct: self.progress.update_progress(text, pct))

    def _done(self) -> None:
        self._job = None
        self.drop.setEnabled(True)
        self.progress.finish()

    def _protected(self, res) -> None:
        self._done()
        self.ctl.after_protect(res)
        name = res.entry["original_filename"]
        if res.storage_configured and res.upload_errors:
            msg = (f"“{name}” is protected, but {len(res.upload_errors)} piece(s) could not be uploaded, so they are kept "
                   f"inside the package instead. Check your storage in Settings. ({next(iter(res.upload_errors.values()))})")
            self.result.set(msg, "warning")
        elif res.cloud:
            self.result.set(f"“{name}” is protected. {res.cloud} pieces are in your cloud storage and the rest travel inside the package.", "success")
        else:
            self.result.set(f"“{name}” is protected. All 12 pieces are kept inside the package.", "success")
        self.result.show()
        self.ctl.toast.emit(f"{name} is protected.", "success")

    def _failed(self, message: str) -> None:
        self._done()
        self.result.set(message, "danger")
        self.result.show()
        self.ctl.toast.emit("Could not protect the file.", "error")

    def _cancelled(self) -> None:
        self._done()
        self.ctl.toast.emit("Cancelled.", "info")

    def _cancel(self) -> None:
        if self._job:
            self._job.cancel()
            self.progress.cancel_btn.setEnabled(False)
            self.progress.detail.setText("Cancelling…")

    # ── list ─────────────────────────────────────────────────────────────────
    def refresh(self) -> None:
        lay = self.list_card.body
        clear_layout(lay)
        entries = self.ctl.vault_entries()
        if not entries:
            lay.addWidget(EmptyState("shield", "No protected files yet",
                                     "Drop a file above. You can then send it, or restore it any time."))
            return
        for e in entries:
            row = VaultRow(e)
            row.send_clicked.connect(self.send_requested)
            row.restore_clicked.connect(self._restore)
            row.remove_clicked.connect(self._remove)
            lay.addWidget(row)

    def _restore(self, entry_id: str) -> None:
        from security_core import VaultLedger
        entry = VaultLedger.get(entry_id)
        if not entry or self._job is not None:
            return
        self.result.hide()
        self.progress.start(f"Restoring {entry['original_filename']}")
        job = self.ctl.make_restore_job(entry)
        self._job = job
        self.ctl.run_job(job, on_success=self._restored, on_fail=self._failed, on_cancel=self._cancelled,
                         on_progress=lambda text, pct: self.progress.update_progress(text, pct))

    def _restored(self, info: dict) -> None:
        self._done()
        self.ctl.after_restore(os.path.basename(info["path"]))
        note, kind = self.ctl.signature_note(info)
        self.result.set(f"Restored to {info['path']}" + ("" if kind == "success" else f". {note}"), kind)
        self.result.show()
        self.ctl.toast.emit("File restored to your Downloads folder.", "success")
        open_folder(info["path"])

    def _remove(self, entry_id: str) -> None:
        from security_core import VaultLedger
        entry = VaultLedger.get(entry_id)
        if not entry:
            return
        if confirm(self, "Remove from your vault?",
                   f"“{entry['original_filename']}” will no longer be listed and its protected package is deleted from this "
                   "computer. Pieces in your cloud storage are not deleted; remove them in your provider's dashboard if you want.",
                   ok="Remove", danger=True):
            self.ctl.delete_entry(entry_id)
