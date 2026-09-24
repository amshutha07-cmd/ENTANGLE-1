"""ui/dialogs.py — confirmation and storage-setup dialogs, styled like the rest of the app."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget,
)

import cloud_dispatcher
from ui import icons, motion, theme
from ui.widgets import clear_layout, Banner, Button, IconBadge, label, add_reveal_toggle


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


class VerifyDialog(QDialog):
    """
    Compare a key fingerprint with its owner, one group of 8 characters at a time. Reading 64 characters in one go
    over the phone invites a skim and a "yes"; eight small, numbered checks do not. Nothing is shortened: every
    group is compared, so the check is exactly as strong as before.
    """

    def __init__(self, parent, name: str, fingerprint: str):
        super().__init__(parent)
        self.name, self.groups = name, fingerprint.split()
        self.index, self.mismatch = 0, False
        self.setWindowTitle(f"Verify {name}'s key")
        self.setModal(True)
        self.setMinimumWidth(500)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 24, 26, 20)
        lay.setSpacing(14)
        head = QHBoxLayout()
        head.setSpacing(14)
        self.badge = IconBadge("key", "primary", 44)
        head.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        self.title = label(f"Compare {name}'s key with them", "h2")
        self.intro = label(f"Call {name} or meet them. Ask them to open People & keys and read out THEIR OWN "
                           "fingerprint, one group at a time. Check each group against the one shown here.", "muted")
        col.addWidget(self.title)
        col.addWidget(self.intro)
        head.addLayout(col, 1)
        lay.addLayout(head)

        self.step = label("", "eyebrow", wrap=False)
        self.step.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.step)
        self.group = QLabel()
        self.group.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.group.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        f = self.group.font()
        f.setFamily(theme.mono_family())
        f.setPixelSize(34)
        f.setBold(True)
        f.setLetterSpacing(f.SpacingType.AbsoluteSpacing, 3)
        self.group.setFont(f)
        lay.addWidget(self.group)
        self.dots = QHBoxLayout()
        self.dots.setSpacing(8)
        self.dots.addStretch()
        self._dots = []
        for _ in self.groups:
            d = QLabel()
            d.setFixedSize(10, 10)
            self._dots.append(d)
            self.dots.addWidget(d)
        self.dots.addStretch()
        lay.addLayout(self.dots)
        self.warning = Banner("", "danger")
        self.warning.hide()
        lay.addWidget(self.warning)

        row = QHBoxLayout()
        self.cancel_btn = Button("Cancel", "ghost")
        self.cancel_btn.clicked.connect(self.reject)
        self.no_btn = Button("It doesn't match", "danger", "x")
        self.no_btn.clicked.connect(self._no)
        self.yes_btn = Button("It matches", "primary", "check")
        self.yes_btn.clicked.connect(self._yes)
        # Deliberately NO default button: holding down Enter must not "confirm" eight groups nobody compared.
        for b in (self.cancel_btn, self.no_btn, self.yes_btn):
            b.setAutoDefault(False)
            b.setDefault(False)
        row.addWidget(self.cancel_btn)
        row.addStretch()
        row.addWidget(self.no_btn)
        row.addWidget(self.yes_btn)
        lay.addLayout(row)
        self._show()

    def _show(self) -> None:
        n = len(self.groups)
        self.step.setText(f"GROUP {self.index + 1} OF {n}")
        self.group.setText(self.groups[self.index] if self.groups else "")
        for i, d in enumerate(self._dots):
            col = theme.color("success") if i < self.index else theme.color("primary") if i == self.index \
                else theme.color("border_strong")
            d.setStyleSheet(f"background: {col}; border-radius: 5px;")

    def _yes(self) -> None:
        if self.index + 1 < len(self.groups):
            self.index += 1
            self._show()
            motion.fade_in(self.group, motion.FAST)       # a visible change, so nobody misses that it moved on
        else:
            self.accept()                                  # every group matched

    def _no(self) -> None:
        self.mismatch = True
        self.badge.set("alert", "danger")
        self.title.setText("Stop: the keys don't match")
        self.intro.setText("")
        self.intro.hide()
        self.step.hide()
        self.group.hide()
        for d in self._dots:
            d.hide()
        self.warning.set(f"The key you have for {self.name} is not the key {self.name} has. Someone may be pretending "
                         f"to be them. Don't send them anything sensitive or open files from them until you have "
                         f"sorted this out in person.", "danger")
        self.warning.show()
        self.no_btn.hide()
        self.yes_btn.hide()
        self.cancel_btn.setText("Close")
        self.cancel_btn.set_variant("secondary")
        self.adjustSize()


def verify_fingerprint(parent, name: str, fingerprint: str) -> bool:
    """True only if the person confirmed every group. A mismatch shows a warning and returns False."""
    if len(fingerprint.split()) < 2:                      # not in groups: fall back to a single comparison
        return confirm(parent, "Confirm fingerprint",
                       f"Did {name} read you exactly this fingerprint, over a channel you trust?\n\n{fingerprint}",
                       ok="Yes, it matches")
    return VerifyDialog(parent, name, fingerprint).exec() == QDialog.DialogCode.Accepted


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
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)   # level with each box
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
        add_reveal_toggle(self.secret)
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
