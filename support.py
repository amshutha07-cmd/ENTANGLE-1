"""
support.py — the app's log file, and a support report you can send when something goes wrong.

The log lives in <vault>/logs/app.log (rotated, a few MB at most). Every line is passed through redact() before it
is written, and again when a report is built, so keys, secrets and signed links never end up in either.

A support report is a zip with:
  report.txt    app / system / setup facts (versions, counts, which providers, whether things are reachable)
  app.log …     the recent log, redacted
  activity.txt  what kinds of things happened and when (no file names)
It never contains private keys, passphrases, file contents, storage secrets or relay credentials.
"""
from __future__ import annotations

import datetime
import logging
import logging.handlers
import os
import platform
import re
import sys
import zipfile
from typing import Optional

import paths

_REDACTIONS = [
    # whole PEM blocks (private keys above all; public keys are harmless but add nothing to a report)
    (re.compile(r"-----BEGIN [A-Z ]*-----.*?-----END [A-Z ]*-----", re.S), "[key removed]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*", re.S), "[key removed]"),     # a block cut off mid-line
    # cloud access keys and anything labelled as a secret, token or password
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "[access key removed]"),
    (re.compile(r"(?i)\b(secret[_ -]?(?:access[_ -]?)?key|application[_ -]?key|password|passphrase|token|"
                r"authorization|x-ansx-signature)\b(\s*[=:]\s*|\s+)(['\"]?)[^\s'\",;]+"), r"\1\2\3[removed]"),
    # signed links: keep where they point, drop the signature that grants access
    (re.compile(r"(https?://[^\s?\"']+)\?[^\s\"']*(?:Signature|X-Amz|sig=|token=)[^\s\"']*", re.I), r"\1?[signed link removed]"),
    # long hex and base64 runs: private-key material, wallet keys, shard data
    (re.compile(r"\b(?:0x)?[0-9a-fA-F]{40,}\b"), "[hex removed]"),
    (re.compile(r"[A-Za-z0-9+/]{60,}={0,2}"), "[data removed]"),
]


def redact(text: str) -> str:
    """Remove anything that looks like a key, secret, signed link or raw key material."""
    for rx, repl in _REDACTIONS:
        text = rx.sub(repl, text)
    return text


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def log_dir() -> str:
    return os.path.join(paths.vault_home(), "logs")


def log_file() -> str:
    return os.path.join(log_dir(), "app.log")


def enable_file_logging(level: int = logging.INFO) -> Optional[logging.Handler]:
    """Also write the log to <vault>/logs/app.log (3 x 1 MB, rotated), readable only by this user."""
    try:
        os.makedirs(log_dir(), mode=0o700, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(log_file(), maxBytes=1_000_000, backupCount=2,
                                                       encoding="utf-8")
        handler.setFormatter(RedactingFormatter("%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"))
        handler.setLevel(level)
        logging.getLogger().addHandler(handler)
        try:
            os.chmod(log_file(), 0o600)
        except OSError:
            pass
        return handler
    except OSError:
        return None


def _facts(app_version: str) -> list[tuple[str, str]]:
    """Plain facts about this installation. Counts and names of providers, never secrets or file names."""
    facts: list[tuple[str, str]] = [
        ("App version", app_version),
        ("Report made", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        ("System", f"{platform.system()} {platform.release()} ({platform.machine()})"),
        ("Python", sys.version.split()[0]),
    ]
    try:
        from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR
        facts.append(("Qt / PyQt", f"{QT_VERSION_STR} / {PYQT_VERSION_STR}"))
    except Exception:
        pass
    try:
        import engine
        facts.append(("Encryption engine", f"v{engine.EXPECTED_VERSION} available" if engine.available()
                      else "NOT available (run python build_engine.py)"))
    except Exception as exc:
        facts.append(("Encryption engine", f"error: {type(exc).__name__}"))
    try:
        import platform_secret
        facts.append(("Key protection", platform_secret.backend_name()))
    except Exception as exc:
        facts.append(("Key protection", f"error: {type(exc).__name__}"))
    try:
        from urllib.parse import urlparse
        import relay_config
        u = urlparse(relay_config.get_relay_url())
        facts.append(("Relay", f"{u.scheme}://{u.hostname}{':' + str(u.port) if u.port else ''}"))
    except Exception:
        pass
    try:
        import cloud_dispatcher
        targets = cloud_dispatcher.load_targets()
        def provider(t: dict) -> str:
            end = (t.get("endpoint_url") or "").lower()
            return ("Cloudflare R2" if "r2.cloudflarestorage.com" in end else "Backblaze B2" if "backblazeb2" in end
                    else "Amazon S3" if not end else "S3-compatible")
        providers = sorted({provider(t) for t in targets})
        facts.append(("Cloud storage", f"{len(targets)} account(s)" + (f": {', '.join(providers)}" if providers else "")))
    except Exception:
        pass
    try:
        from security_core import SecurityCore, VaultLedger
        facts.append(("Identities on this computer", str(len(SecurityCore.list_registered_users()))))
        facts.append(("Contacts", str(len(SecurityCore.list_contact_info()))))
        facts.append(("Protected files", str(len(VaultLedger.load()))))
    except Exception:
        pass
    try:
        import nfc_serial
        facts.append(("Card reader", nfc_serial.reader_status()))
    except Exception:
        facts.append(("Card reader", "support not installed"))
    try:
        from ui.controller import pref
        facts.append(("Settings", f"theme={pref('theme', 'dark')}, auto-lock={pref('auto_lock_minutes', 10)} min, "
                                  f"animations={'reduced' if pref('reduce_motion', False) else 'on'}, "
                                  f"close window={'background' if pref('close_to_tray', True) else 'quit'}"))
    except Exception:
        pass
    return facts


def build_report(dest: str, app_version: str = "?", relay_state: str = "") -> str:
    """Write the support report zip to `dest` and return its path."""
    lines = ["A.N.Sx Vault support report", "=" * 27, "",
             "This report has no private keys, passphrases, file contents or storage secrets.", ""]
    facts = _facts(app_version)
    if relay_state:
        facts.append(("Relay connection now", relay_state))
    width = max(len(k) for k, _v in facts)
    lines += [f"{k.ljust(width)}  {v}" for k, v in facts]
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("report.txt", redact("\n".join(lines) + "\n"))
        for name in sorted(os.listdir(log_dir())) if os.path.isdir(log_dir()) else []:
            if name.startswith("app.log"):
                with open(os.path.join(log_dir(), name), encoding="utf-8", errors="replace") as f:
                    z.writestr(name, redact(f.read()))                # redacted again, in case of older lines
        try:
            import activity
            recent = activity.recent(100)
            z.writestr("activity.txt", "\n".join(
                f"{datetime.datetime.fromtimestamp(i.get('ts', 0)).strftime('%Y-%m-%d %H:%M')}  {i.get('kind', '?')}"
                for i in recent) + "\n")                              # kinds and times only: no file or person names
        except Exception:
            pass
    try:
        os.chmod(dest, 0o600)
    except OSError:
        pass
    return dest
