"""The support report and the log file: useful facts in, secrets never."""
import logging
import os
import zipfile

import pytest

import support

PEM = ("-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7\nabcdefghijklmnop\n"
       "-----END PRIVATE KEY-----")
SECRETS = [PEM, "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "hunter2-correct-horse",
           "a" * 64, "Sig=abc123def456"]


def test_redact_removes_every_kind_of_secret_and_keeps_the_rest():
    text = (f"loaded key {PEM} ok; access AKIAIOSFODNN7EXAMPLE; secret_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY; "
            f"passphrase: hunter2-correct-horse; eth {'a' * 64}; "
            f"url https://bucket.example.com/abc/s01.bin?X-Amz-Signature=Sig=abc123def456&X-Amz-Expires=600; "
            f"blob {'QUJD' * 30}; relay http://127.0.0.1:8000 answered 200 for sam")
    out = support.redact(text)
    for s in SECRETS:
        assert s not in out, s
    assert "QUJD" * 30 not in out
    assert "https://bucket.example.com/abc/s01.bin?[signed link removed]" in out          # where, but not the grant
    assert "relay http://127.0.0.1:8000 answered 200 for sam" in out                       # ordinary lines survive


def test_the_log_file_never_receives_a_secret(tmp_path, monkeypatch):
    monkeypatch.setattr(support, "log_dir", lambda: str(tmp_path / "logs"))
    handler = support.enable_file_logging()
    try:
        log = logging.getLogger("ansx.test")
        log.warning("relay said no; key was %s and token=%s", PEM, "hunter2-correct-horse")
        handler.flush()
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()
    written = open(tmp_path / "logs" / "app.log").read()
    assert "relay said no" in written and "PRIVATE KEY" not in written and "hunter2" not in written
    assert oct(os.stat(tmp_path / "logs" / "app.log").st_mode & 0o777) == "0o600"          # readable only by you


def test_report_has_facts_logs_and_activity_but_nothing_secret(tmp_path, monkeypatch):
    import activity
    monkeypatch.setattr(support, "log_dir", lambda: str(tmp_path / "logs"))
    os.makedirs(tmp_path / "logs")
    (tmp_path / "logs" / "app.log").write_text(f"old line before redaction existed: {PEM}\nprotect ok\n")
    activity.add("protected", "Protected salary-2026.xlsx", operator="someone")
    path = support.build_report(str(tmp_path / "report.zip"), "1.0", "online")
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        everything = "\n".join(z.read(n).decode() for n in names)
    assert {"report.txt", "app.log", "activity.txt"} <= names
    assert "App version  " in everything and "Relay connection now" in everything and "protect ok" in everything
    assert "protected" in everything                                                         # what happened…
    assert "salary-2026.xlsx" not in everything                                              # …but not file names
    for s in SECRETS:
        assert s not in everything


def test_settings_button_saves_the_report(tmp_path):
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QApplication
    global _app                                              # keep it alive: a collected QApplication aborts Qt
    _app = QApplication.instance() or QApplication([])
    from ui.controller import AppController
    from ui.pages.settings import SettingsPage
    page = SettingsPage(AppController())
    out = page.save_support_report(str(tmp_path / "r.zip"))
    assert out and zipfile.is_zipfile(out) and page.report_msg.property("kind") == "success"
