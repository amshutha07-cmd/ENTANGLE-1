"""relay_config.py — which relay the app talks to, editable at runtime from Settings."""
from __future__ import annotations

import json
import logging
import os
import sys
from urllib.parse import urlparse

import paths

logger = logging.getLogger(__name__)
DEFAULT_RELAY = "http://127.0.0.1:8000"
_override: str | None = None


def load_config() -> dict:
    try:
        with open(paths.config_file()) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(**updates) -> None:
    cfg = load_config()
    cfg.update(updates)
    os.makedirs(paths.vault_home(), mode=0o700, exist_ok=True)
    tmp = paths.config_file() + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, paths.config_file())


def normalize(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Enter a full address such as https://relay.example.com")
    return url


def bundled_default() -> str:
    """
    The relay address shipped INSIDE the app (a `default_config.json` next to the program, or bundled by PyInstaller).
    Lets you hand people one installer that already knows your relay, so they never paste an address.
    """
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    try:
        with open(os.path.join(base, "default_config.json")) as f:
            data = json.load(f)
        return normalize(str(data.get("relay_url", ""))) if isinstance(data, dict) and data.get("relay_url") else ""
    except (OSError, ValueError):
        return ""


def get_relay_url() -> str:
    """Priority: changed in Settings this session > $ANSX_RELAY_URL > saved by the user > shipped with the app > local dev."""
    if _override:
        return _override
    env = os.environ.get("ANSX_RELAY_URL", "").strip()
    if env:
        return env.rstrip("/")
    return (load_config().get("relay_url") or bundled_default() or DEFAULT_RELAY).rstrip("/")


def set_relay_url(url: str) -> str:
    """Validate, persist and activate a new relay address."""
    global _override
    url = normalize(url)
    save_config(relay_url=url)
    _override = url
    return url


def is_secure(url: str | None = None) -> bool:
    url = url or get_relay_url()
    p = urlparse(url)
    return p.scheme == "https" or p.hostname in ("127.0.0.1", "localhost", "::1")
