"""activity.py — small persistent activity feed shown on the Home screen (never stores file contents or keys)."""
from __future__ import annotations

import json
import os
import threading
import time

import paths

_lock = threading.Lock()
MAX_ITEMS = 200
KINDS = ("protected", "sent", "delivered", "received", "declined", "restored", "security", "error")


def _load() -> list[dict]:
    try:
        with open(paths.activity_file()) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def add(kind: str, title: str, detail: str = "", operator: str = "") -> dict:
    item = {"ts": int(time.time()), "kind": kind if kind in KINDS else "security",
            "title": title, "detail": detail, "operator": operator}
    with _lock:
        items = [item] + _load()
        os.makedirs(paths.vault_home(), mode=0o700, exist_ok=True)
        tmp = paths.activity_file() + ".tmp"
        with open(tmp, "w") as f:
            json.dump(items[:MAX_ITEMS], f)
        os.replace(tmp, paths.activity_file())
    return item


def recent(n: int = 20, operator: str = "") -> list[dict]:
    with _lock:
        items = _load()
    if operator:
        items = [i for i in items if i.get("operator") in ("", operator)]
    return items[:n]
