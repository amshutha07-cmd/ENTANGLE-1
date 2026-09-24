"""
platform_secret.py — A.N.Sx Vault | per-machine secret (second factor for local key wrapping)

Returns a 32-byte secret that never leaves this machine's protected storage.
Backends, tried in order; `backend_name()` reports which one is REALLY in use
(never claim more than that in UI or docs):

  "tpm2"     Linux TPM 2.0 via tpm2-tools: 32 random bytes sealed to the TPM.
             UNTESTED on real hardware in this repo — treat as experimental.
  "keyring"  OS keystore: macOS Keychain, Windows Credential Locker, Secret Service.
  "file"     0600 file in ~/.ansx_vault. Last resort; protects nothing against
             anyone who can read your home directory. A warning is logged.

Override for tests / CI: ANSX_PLATFORM_SECRET_BACKEND = tpm2 | keyring | file
"""
from __future__ import annotations

import base64
import logging
import os
import secrets
import shutil
import stat
import subprocess
import tempfile
import threading
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DIR = os.environ.get("ANSX_VAULT_HOME", os.path.expanduser("~/.ansx_vault"))
_SERVICE = "ANSxVault"
_ACCOUNT = "platform-secret-v1"
_lock = threading.Lock()
_cached: Optional[tuple[str, bytes]] = None


def _ensure_dir(path: str) -> None:
    os.makedirs(path, mode=0o700, exist_ok=True)


# ── keyring ──────────────────────────────────────────────────────────────────
def _from_keyring() -> Optional[bytes]:
    try:
        import keyring
        from keyring.backends.fail import Keyring as FailKeyring
        if isinstance(keyring.get_keyring(), FailKeyring):
            return None
        val = keyring.get_password(_SERVICE, _ACCOUNT)
        if not val:
            val = base64.b64encode(secrets.token_bytes(32)).decode()
            keyring.set_password(_SERVICE, _ACCOUNT, val)
        return base64.b64decode(val)
    except Exception as exc:
        logger.info("[PlatformSecret] keyring unavailable: %s", exc)
        return None


# ── TPM 2.0 (experimental) ───────────────────────────────────────────────────
def _tpm_available() -> bool:
    return (os.path.exists("/dev/tpmrm0") and all(
        shutil.which(t) for t in ("tpm2_createprimary", "tpm2_create", "tpm2_load", "tpm2_unseal")))


def _from_tpm2() -> Optional[bytes]:
    if not _tpm_available():
        return None
    tdir = os.path.join(BASE_DIR, "tpm")
    pub, priv = os.path.join(tdir, "seal.pub"), os.path.join(tdir, "seal.priv")
    try:
        _ensure_dir(tdir)
        with tempfile.TemporaryDirectory() as tmp:
            prim = os.path.join(tmp, "primary.ctx")
            run = lambda *a, **k: subprocess.run(a, check=True, capture_output=True, timeout=30, **k)
            run("tpm2_createprimary", "-C", "o", "-c", prim)
            if not (os.path.exists(pub) and os.path.exists(priv)):
                data = secrets.token_bytes(32)
                run("tpm2_create", "-C", prim, "-i", "-", "-u", pub, "-r", priv, input=data)
            loaded = os.path.join(tmp, "seal.ctx")
            run("tpm2_load", "-C", prim, "-u", pub, "-r", priv, "-c", loaded)
            return run("tpm2_unseal", "-c", loaded).stdout
    except Exception as exc:
        logger.warning("[PlatformSecret] TPM2 backend failed: %s", exc)
        return None


# ── file fallback ────────────────────────────────────────────────────────────
def _from_file() -> bytes:
    _ensure_dir(BASE_DIR)
    path = os.path.join(BASE_DIR, "platform.secret")
    if os.path.exists(path):
        with open(path, "rb") as f:
            return base64.b64decode(f.read())
    data = secrets.token_bytes(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "wb") as f:
        f.write(base64.b64encode(data))
    return data


def _resolve() -> tuple[str, bytes]:
    forced = os.environ.get("ANSX_PLATFORM_SECRET_BACKEND", "").lower()
    order = [forced] if forced else ["tpm2", "keyring", "file"]
    for name in order:
        val = {"tpm2": _from_tpm2, "keyring": _from_keyring}.get(name, lambda: None)() if name != "file" else _from_file()
        if val:
            if name == "file":
                logger.warning("[PlatformSecret] No TPM/OS keystore available; using a 0600 file. "
                               "This is NOT hardware-backed.")
            return name, val
    raise RuntimeError(f"No platform secret backend available (tried {order})")


def get_platform_secret() -> bytes:
    global _cached
    with _lock:
        if _cached is None:
            _cached = _resolve()
        return _cached[1]


def backend_name() -> str:
    get_platform_secret()
    return _cached[0]  # type: ignore[index]
