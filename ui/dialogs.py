"""ui/dialogs.py — confirmation and storage-setup dialogs, styled like the rest of the app."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget,
)

import cloud_dispatcher
from ui import icons, theme
from ui.widgets import clear_layout, Banner, Button, IconBadge, label, polish


def confirm(parent, title: str, text: str, ok: str = "Confirm", danger: bool = False, cancel: str = "Cancel") -> bool:
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setModal(True)
    dlg.setMinimumWidth(420)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(24, 24, 24, 20)
    lay.setSpacing(14)
    head = QHBoxLayout()
    head.setSpacing(14)
    head.addWidget(IconBadge("alert" if danger else "info", "danger" if danger else "primary", 44), 0, Qt.AlignmentFlag.AlignTop)
    col = QVBoxLayout()
    col.addWidget(label(title, "h2"))
    col.addWidget(label(text, "body"))
    head.addLayout(col, 1)
    lay.addLayout(head)
    row = QHBoxLayout()
    row.addStretch()
    no = Button(cancel, "secondary")
    yes = Button(ok, "danger" if danger else "primary")
    no.clicked.connect(dlg.reject)
    yes.clicked.connect(dlg.accept)
    no.setDefault(True)
    row.addWidget(no)
    row.addWidget(yes)
    lay.addLayout(row)
    return dlg.exec() == QDialog.DialogCode.Accepted


def info(parent, title: str, text: str, kind: str = "info") -> None:
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setMinimumWidth(420)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(24, 24, 24, 20)
    lay.setSpacing(14)
    head = QHBoxLayout()
    head.setSpacing(14)
    head.addWidget(IconBadge({"info": "info", "success": "check", "error": "alert"}[kind],
                             {"info": "primary", "success": "success", "error": "danger"}[kind], 44), 0, Qt.AlignmentFlag.AlignTop)
    col = QVBoxLayout()
    col.addWidget(label(title, "h2"))
    col.addWidget(label(text, "body"))
    head.addLayout(col, 1)
    lay.addLayout(head)
    ok = Button("OK", "primary")
    ok.clicked.connect(dlg.accept)
    row = QHBoxLayout()
    row.addStretch()
    row.addWidget(ok)
    lay.addLayout(row)
    dlg.exec()


HELP = {
    "r2": ("In Cloudflare open R2 → your bucket. Create an API token under R2 → Manage API Tokens with "
           "“Object Read & Write” for this bucket only. The Account ID is on the R2 overview page."),
    "b2": ("In Backblaze open B2 → Buckets → your bucket (keep it Private). Create an Application Key for "
           "that bucket with Read and Write. The region is inside the bucket's endpoint, e.g. s3.us-west-004… → us-west-004."),
    "s3": ("Create a private bucket and an IAM user allowed only s3:PutObject and s3:GetObject on it, "
           "then create an access key for that user."),
    "custom": "Enter the S3 endpoint your provider gives you, plus the bucket name and an access key with read and write access.",
}


class StorageDialog(QDialog):
    """Add or edit one cloud storage account, and test it before saving."""

    def __init__(self, controller, parent=None, existing: Optional[dict] = None):
        super().__init__(parent)
        self.ctl = controller
        self._existing = existing
        self._tested_ok = False
        self._result: Optional[dict] = None
        self.setWindowTitle("Add cloud storage" if not existing else "Edit cloud storage")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(12)
        lay.addWidget(label("Cloud storage account", "h2"))
        lay.addWidget(label("Your file's encrypted pieces are stored here. The provider cannot read them.", "muted"))

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self.provider = QComboBox()
        for key, meta in cloud_dispatcher.PROVIDERS.items():
            self.provider.addItem(meta["label"], key)
        self.name = QLineEdit()
        self.name.setPlaceholderText("A label for you, e.g. my-r2")
        self.bucket = QLineEdit()
        self.bucket.setPlaceholderText("bucket name")
        self.access = QLineEdit()
        self.secret = QLineEdit()
        self.secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.account = QLineEdit()
        self.account.setPlaceholderText("32 characters")
        self.region = QLineEdit()
        self.endpoint = QLineEdit()
        self.endpoint.setPlaceholderText("https://…")
        rows = [("Provider", self.provider), ("Label", self.name), ("Bucket", self.bucket),
                ("Access key", self.access), ("Secret key", self.secret),
                ("Account ID", self.account), ("Region", self.region), ("Endpoint", self.endpoint)]
        self._rows: dict[str, tuple] = {}
        for text, widget in rows:
            lb = label(text, "muted", wrap=False)
            form.addRow(lb, widget)
            self._rows[text] = (lb, widget)
        lay.addLayout(form)

        self.help = Banner("", "info")
        lay.addWidget(self.help)

        self.results = QVBoxLayout()
        self.results.setSpacing(4)
        lay.addLayout(self.results)
        self.error = label("", "error")
        lay.addWidget(self.error)

        row = QHBoxLayout()
        self.test_btn = Button("Test connection", "secondary", "wifi")
        self.save_btn = Button("Save", "primary", "check")
        cancel = Button("Cancel", "ghost")
        row.addWidget(self.test_btn)
        row.addStretch()
        row.addWidget(cancel)
        row.addWidget(self.save_btn)
        lay.addLayout(row)

        self.provider.currentIndexChanged.connect(self._provider_changed)
        for w in (self.name, self.bucket, self.access, self.secret, self.account, self.region, self.endpoint):
            w.textChanged.connect(self._edited)
        self.test_btn.clicked.connect(self._test)
        self.save_btn.clicked.connect(self._save)
        cancel.clicked.connect(self.reject)
        if existing:
            self.name.setText(existing.get("name", ""))
            self.bucket.setText(existing.get("bucket", ""))
            self.access.setText(existing.get("access_key", ""))
            self.secret.setText(existing.get("secret_key", ""))
        self._provider_changed()

    # which provider fields are relevant
    def _provider_changed(self) -> None:
        key = self.provider.currentData()
        needs = cloud_dispatcher.PROVIDERS[key]["needs"]
        show = {"Account ID": "account_id" in needs, "Region": "region" in needs, "Endpoint": "endpoint_url" in needs}
        for text, on in show.items():
            for w in self._rows[text]:
                w.setVisible(on)
        self.help.set(f"{cloud_dispatcher.PROVIDERS[key]['note']} {HELP[key]}", "info")
        self._edited()

    def _edited(self) -> None:
        self._tested_ok = False
        self.error.setText("")
        self._clear_results()

    def _clear_results(self) -> None:
        clear_layout(self.results)

    def _build(self) -> Optional[dict]:
        key = self.provider.currentData()
        try:
            target = cloud_dispatcher.build_target(
                key, self.name.text(), self.bucket.text(), self.access.text(), self.secret.text(),
                account_id=self.account.text(), region=self.region.text(), endpoint_url=self.endpoint.text())
        except ValueError as exc:
            self.error.setText(str(exc))
            return None
        return target

    def _test(self) -> None:
        target = self._build()
        if not target:
            return
        self._clear_results()
        self.test_btn.setEnabled(False)
        self.test_btn.setText("Testing…")
        job = self.ctl.make_storage_test_job(target)
        self.ctl.run_job(job, on_success=self._tested, on_fail=lambda m: self._tested((False, [("fail", m)])))

    def _tested(self, result) -> None:
        ok, lines = result
        self.test_btn.setEnabled(True)
        self.test_btn.setText("Test connection")
        self._clear_results()
        icon = {"ok": ("check", "success"), "fail": ("x", "danger"), "warn": ("alert", "warning"), "info": ("info", "info")}
        for status, msg in lines:
            row = QWidget()
            hl = QHBoxLayout(row)
            hl.setContentsMargins(0, 0, 0, 0)
            ic = QLabel()
            name, col = icon[status]
            ic.setPixmap(icons.pixmap(name, theme.color(col), 16))
            hl.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
            hl.addWidget(label(msg, "body"), 1)
            self.results.addWidget(row)
        self._tested_ok = ok

    def _save(self) -> None:
        target = self._build()
        if not target:
            return
        if not self._tested_ok:
            if not confirm(self, "Save without a successful test?",
                           "The connection has not been tested (or the test failed). Uploads will fail until it works.",
                           ok="Save anyway"):
                return
        self._result = target
        self.accept()

    def target(self) -> Optional[dict]:
        return self._result
