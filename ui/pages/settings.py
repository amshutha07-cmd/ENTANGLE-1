"""ui/pages/settings.py — connection, cloud storage, appearance and security details."""
from __future__ import annotations


from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

import engine
import paths
import platform_secret
import relay_config
from ui import motion, theme
from ui.controller import pref, set_pref
from ui.dialogs import StorageDialog, confirm
from ui.pages.base import Page
from ui.pages.vault import open_folder
from ui.widgets import clear_layout, Banner, Button, Card, EmptyState, IconBadge, KeyValue, label

AUTO_LOCK_CHOICES = [("Never", 0), ("After 5 minutes", 5), ("After 10 minutes", 10), ("After 30 minutes", 30), ("After 1 hour", 60)]


class StorageRow(QWidget):
    def __init__(self, target: dict, on_edit, on_remove):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(12)
        lay.addWidget(IconBadge("cloud", "neutral", 38))
        col = QVBoxLayout()
        col.setSpacing(1)
        name = label(target["name"], "body", wrap=False)
        name.setStyleSheet("font-weight: 650;")
        host = (target.get("endpoint_url") or "Amazon S3").replace("https://", "")
        col.addWidget(name)
        col.addWidget(label(f"{target['bucket']} · {host}", "muted", wrap=False))
        lay.addLayout(col, 1)
        edit = Button("Edit", "secondary", size="sm")
        rm = Button("", "ghost", "trash", "sm")
        rm.setToolTip("Remove this storage account")
        edit.clicked.connect(lambda: on_edit(target))
        rm.clicked.connect(lambda: on_remove(target))
        lay.addWidget(edit)
        lay.addWidget(rm)


class SettingsPage(Page):
    theme_changed = pyqtSignal(str)
    lock_requested = pyqtSignal()
    autolock_changed = pyqtSignal(int)
    navigate = pyqtSignal(str)

    def __init__(self, ctl):
        super().__init__(ctl, "Settings", "Connection, cloud storage and security.")

        # ── account ──
        acct = Card(padding=20, spacing=10)
        acct.body.addWidget(label("Account", "h2", wrap=False))
        self.acct_name = KeyValue("Name")
        self.acct_mode = KeyValue("Sign-in")
        acct.body.addWidget(self.acct_name)
        acct.body.addWidget(self.acct_mode)
        row = QHBoxLayout()
        lock = Button("Lock now", "secondary", "lock")
        lock.clicked.connect(self.lock_requested)
        self.remove_btn = Button("Remove this identity…", "danger", "trash")
        self.remove_btn.clicked.connect(self._remove_identity)
        row.addWidget(lock)
        row.addStretch()
        row.addWidget(self.remove_btn)
        acct.body.addLayout(row)
        self.root.addWidget(acct)

        # ── relay ──
        rel = Card(padding=20, spacing=10)
        rel.body.addWidget(label("Connection", "h2", wrap=False))
        rel.body.addWidget(label("The relay is the mailbox that carries your encrypted files between people. "
                                 "It can never read them. Use the address your administrator gave you.", "muted"))
        r = QHBoxLayout()
        self.relay_edit = QLineEdit()
        self.relay_edit.setPlaceholderText("https://relay.example.com")
        self.relay_test = Button("Test", "secondary", "wifi")
        self.relay_save = Button("Save", "primary", "check")
        self.relay_test.clicked.connect(self._test_relay)
        self.relay_save.clicked.connect(self._save_relay)
        self.relay_edit.textChanged.connect(lambda _t: self.relay_msg.hide())
        r.addWidget(self.relay_edit, 1)
        r.addWidget(self.relay_test)
        r.addWidget(self.relay_save)
        rel.body.addLayout(r)
        self.relay_msg = Banner("", "info")
        self.relay_msg.hide()
        rel.body.addWidget(self.relay_msg)
        self.root.addWidget(rel)

        # ── storage ──
        sto = Card(padding=20, spacing=10)
        head = QHBoxLayout()
        head.addWidget(label("Cloud storage", "h2", wrap=False), 1)
        add = Button("Add an account", "primary", "plus", "sm")
        add.clicked.connect(self._add_storage)
        head.addWidget(add)
        sto.body.addLayout(head)
        sto.body.addWidget(label("When you protect a file, its encrypted pieces are spread across these accounts, so no single "
                                 "company holds everything. Use two different providers if you can.", "muted"))
        self.storage_box = QVBoxLayout()
        self.storage_box.setSpacing(0)
        sto.body.addLayout(self.storage_box)
        self.storage_tip = Banner("Tip: add a second provider so no single company holds all the pieces.", "info")
        sto.body.addWidget(self.storage_tip)
        self.root.addWidget(sto)

        # ── appearance / lock ──
        app = Card(padding=20, spacing=10)
        app.body.addWidget(label("Appearance and privacy", "h2", wrap=False))
        r = QHBoxLayout()
        r.addWidget(label("Theme", "body", wrap=False), 1)
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Dark", "dark")
        self.theme_combo.addItem("Light", "light")
        self.theme_combo.currentIndexChanged.connect(self._theme_picked)
        r.addWidget(self.theme_combo)
        app.body.addLayout(r)
        r = QHBoxLayout()
        r.addWidget(label("Lock automatically when idle", "body", wrap=False), 1)
        self.lock_combo = QComboBox()
        for text, mins in AUTO_LOCK_CHOICES:
            self.lock_combo.addItem(text, mins)
        self.lock_combo.currentIndexChanged.connect(self._lock_picked)
        r.addWidget(self.lock_combo)
        app.body.addLayout(r)
        r = QHBoxLayout()
        r.addWidget(label("When you close the window", "body", wrap=False), 1)
        self.close_combo = QComboBox()
        self.close_combo.addItem("Keep running in the background", True)
        self.close_combo.addItem("Quit the app", False)
        self.close_combo.currentIndexChanged.connect(self._close_picked)
        r.addWidget(self.close_combo)
        app.body.addLayout(r)
        self.close_note = label("In the background it keeps receiving files and tells you when one arrives, until it "
                                "locks (see “Lock automatically”). Quit from the tray / menu bar icon.", "muted")
        app.body.addWidget(self.close_note)
        r = QHBoxLayout()
        motion_label = label("Animations", "body", wrap=False)
        motion_label.setToolTip("Short fades and slides that show what changed. Choose “Reduced” if motion bothers you.")
        r.addWidget(motion_label, 1)
        self.motion_combo = QComboBox()
        self.motion_combo.addItem("On", False)
        self.motion_combo.addItem("Reduced", True)
        self.motion_combo.currentIndexChanged.connect(self._motion_picked)
        r.addWidget(self.motion_combo)
        app.body.addLayout(r)
        self.root.addWidget(app)

        # ── keyboard shortcuts ──
        keys = self.shortcuts_card = Card(padding=20, spacing=6)
        keys.body.addWidget(label("Keyboard shortcuts", "h2", wrap=False))

        def native(seq: str) -> str:
            from PyQt6.QtGui import QKeySequence
            return QKeySequence(seq).toString(QKeySequence.SequenceFormat.NativeText)
        for what, seq in (("Home, Protect, Send", f"{native('Ctrl+1')}  {native('Ctrl+2')}  {native('Ctrl+3')}"),
                          ("Inbox, People, Settings", f"{native('Ctrl+4')}  {native('Ctrl+5')}  {native('Ctrl+6')}"),
                          ("Protect a file", native("Ctrl+O")),
                          ("Lock now", native("Ctrl+L")),
                          ("Move between buttons", "Tab  /  Shift+Tab")):
            kv = KeyValue(what, seq, key_width=190)
            keys.body.addWidget(kv)
        keys.body.addWidget(label("You can also drop a file anywhere on the window to protect it.", "muted"))
        self.root.addWidget(keys)

        # ── security details ──
        sec = Card(padding=20, spacing=6)
        sec.body.addWidget(label("Security details", "h2", wrap=False))
        self.sec_keys = KeyValue("Key protection")
        self.sec_engine = KeyValue("Encryption engine")
        self.sec_data = KeyValue("Data folder", mono=True)
        sec.body.addWidget(self.sec_keys)
        sec.body.addWidget(self.sec_engine)
        sec.body.addWidget(self.sec_data)
        opn = Button("Open data folder", "ghost", "folder", "sm")
        opn.clicked.connect(lambda: open_folder(paths.vault_home()))
        report = Button("Save a support report…", "ghost", "download", "sm")
        report.setToolTip("A zip with app and system facts and the recent log, for someone helping you. "
                          "No keys, passphrases, file contents or storage secrets.")
        report.clicked.connect(self.save_support_report)
        tools = QHBoxLayout()
        tools.addWidget(opn)
        tools.addWidget(report)
        tools.addStretch()
        sec.body.addLayout(tools)
        self.report_msg = Banner("", "success", "Show in folder", lambda: open_folder(self._report_path))
        self.report_msg.hide()
        self._report_path = ""
        sec.body.addWidget(self.report_msg)
        self.root.addWidget(sec)
        self.root.addStretch(1)

        self._loading = True
        ctl.session_started.connect(self._session_started)

    def _session_started(self, _name: str) -> None:
        self.on_show()

    def on_show(self) -> None:
        self._loading = True
        ctl = self.ctl
        name = ctl.operator or ""
        self.acct_name.set(name or "—")
        self.acct_mode.set("NFC card" if ctl.auth_mode(name) == "nfc" else "Passphrase")
        self.relay_edit.setText(relay_config.get_relay_url())
        self.theme_combo.setCurrentIndex(0 if theme.current() == "dark" else 1)
        mins = int(pref("auto_lock_minutes", 10) or 0)
        idx = max(0, self.lock_combo.findData(mins))
        self.lock_combo.setCurrentIndex(idx)
        self.motion_combo.setCurrentIndex(1 if pref("reduce_motion", False) else 0)
        self.close_combo.setCurrentIndex(0 if pref("close_to_tray", True) else 1)
        self.close_note.setVisible(bool(pref("close_to_tray", True)))
        try:
            backend = platform_secret.backend_name()
        except Exception:
            backend = "unknown"
        self.sec_keys.set({"tpm2": "Your computer's TPM chip", "keyring": "Your system's secure storage",
                           "file": "A protected file on this computer (weaker; no secure storage was available)"}.get(backend, backend))
        self.sec_engine.set(f"Version {engine.EXPECTED_VERSION} · AES-256-GCM, 12 pieces, any 8 rebuild" if engine.available()
                            else "NOT AVAILABLE — run python build_engine.py")
        self.sec_data.set(paths.vault_home())
        self._fill_storage()
        self._loading = False

    # ── relay ────────────────────────────────────────────────────────────────
    def _test_relay(self) -> None:
        self.relay_msg.set("Testing…", "info")
        self.relay_msg.show()
        self.relay_test.setEnabled(False)
        job = self.ctl.make_relay_test_job(self.relay_edit.text())
        self.ctl.run_job(job, on_success=self._relay_ok, on_fail=self._relay_bad)

    def _relay_ok(self, res: dict) -> None:
        self.relay_test.setEnabled(True)
        mb = res["limits"]["max_transfer_bytes"] // (1024 * 1024)
        if res["secure"]:
            self.relay_msg.set(f"Connected. Files up to {mb} MB, kept up to {res['limits']['ttl_seconds'] // 86400} days.", "success")
        else:
            self.relay_msg.set("Connected, but this address is not encrypted (http). Your files stay end-to-end encrypted, "
                               "but who you talk to and when is visible on the network. Use https:// for real use.", "warning")

    def _relay_bad(self, message: str) -> None:
        self.relay_test.setEnabled(True)
        self.relay_msg.set(message, "danger")
        self.relay_msg.show()

    def _save_relay(self) -> None:
        try:
            url = self.ctl.apply_relay_url(self.relay_edit.text())
        except ValueError as exc:
            self.relay_msg.set(str(exc), "danger")
            self.relay_msg.show()
            return
        self.relay_edit.setText(url)
        self.relay_msg.set("Saved. Reconnecting…", "success")
        self.relay_msg.show()
        self.ctl.toast.emit("Relay address saved.", "success")

    # ── storage ──────────────────────────────────────────────────────────────
    def _fill_storage(self) -> None:
        clear_layout(self.storage_box)
        targets = self.ctl.storage_targets()
        if not targets:
            self.storage_box.addWidget(EmptyState("cloud", "No storage accounts",
                                                  "Without one, pieces are kept inside the protected package. That works for small files."))
        for t in targets:
            self.storage_box.addWidget(StorageRow(t, self._edit_storage, self._remove_storage))
        self.storage_tip.setVisible(len(targets) == 1)

    def _add_storage(self) -> None:
        dlg = StorageDialog(self.ctl, self)
        if dlg.exec() and dlg.target():
            targets = [t for t in self.ctl.storage_targets() if t["name"] != dlg.target()["name"]]
            self.ctl.save_storage_targets(targets + [dlg.target()])
            self._fill_storage()
            self.ctl.toast.emit("Storage account saved.", "success")
            self.ctl.activity_changed.emit()

    def _edit_storage(self, target: dict) -> None:
        dlg = StorageDialog(self.ctl, self, existing=target)
        if dlg.exec() and dlg.target():
            targets = [t for t in self.ctl.storage_targets() if t["name"] != target["name"]]
            self.ctl.save_storage_targets(targets + [dlg.target()])
            self._fill_storage()
            self.ctl.toast.emit("Storage account updated.", "success")

    def _remove_storage(self, target: dict) -> None:
        if confirm(self, "Remove this storage account?",
                   f"“{target['name']}” is removed from this app. Files already stored there stay there, but this computer "
                   "can no longer refresh their download links.", ok="Remove", danger=True):
            self.ctl.save_storage_targets([t for t in self.ctl.storage_targets() if t["name"] != target["name"]])
            self._fill_storage()
            self.ctl.activity_changed.emit()

    # ── appearance ───────────────────────────────────────────────────────────
    def _theme_picked(self) -> None:
        if self._loading:
            return
        self.theme_changed.emit(self.theme_combo.currentData())

    def save_support_report(self, path: str = "") -> str:
        """Ask where, then write the support report there. Returns the path ("" if cancelled or failed)."""
        import datetime
        import os
        import support
        from PyQt6.QtWidgets import QFileDialog
        if not path:
            default = os.path.join(paths.downloads_dir(),
                                   f"ansx-support-{datetime.datetime.now():%Y%m%d-%H%M}.zip")
            path, _ = QFileDialog.getSaveFileName(self, "Save a support report", default, "Zip files (*.zip)")
            if not path:
                return ""
        try:
            from ui.main_window import APP_VERSION
            support.build_report(path, APP_VERSION, self.ctl.relay_state)
        except OSError as exc:
            self.report_msg.set(f"Could not save the report: {exc}", "danger")
            self.report_msg._btn.hide()
            motion.reveal(self.report_msg)
            return ""
        self._report_path = path
        self.report_msg.set("Saved. It has no keys, passphrases, file contents or storage secrets. Its log can mention "
                            "the names of people you exchanged files with: look through it before you share it.",
                            "success")
        self.report_msg._btn.show()
        motion.reveal(self.report_msg)
        return path

    def _close_picked(self) -> None:
        if self._loading:
            return
        keep = bool(self.close_combo.currentData())
        set_pref("close_to_tray", keep)
        self.close_note.setVisible(keep)

    def _motion_picked(self) -> None:
        if self._loading:
            return
        value = bool(self.motion_combo.currentData())
        set_pref("reduce_motion", value)
        motion.set_reduced(value)

    def _lock_picked(self) -> None:
        if self._loading:
            return
        mins = int(self.lock_combo.currentData())
        set_pref("auto_lock_minutes", mins)
        self.autolock_changed.emit(mins)

    def _remove_identity(self) -> None:
        name = self.ctl.operator
        if name and confirm(self, "Remove this identity?",
                            f"'{name}' and its keys will be deleted from this computer. Files protected with it can never be "
                            "opened again unless you have a backup. This cannot be undone.", ok="Remove permanently", danger=True):
            self.ctl.delete_identity(name)
