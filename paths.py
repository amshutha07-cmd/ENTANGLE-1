"""paths.py — where A.N.Sx Vault keeps its data (evaluated lazily so tests can relocate it)."""
import os


def vault_home() -> str:
    return os.environ.get("ANSX_VAULT_HOME") or os.path.join(os.path.expanduser("~"), ".ansx_vault")


def _sub(*parts: str, create: bool = True) -> str:
    path = os.path.join(vault_home(), *parts)
    if create:
        os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def outbox_dir() -> str:
    return _sub("outbox")


def incoming_dir() -> str:
    return _sub("incoming")


def tmp_dir() -> str:
    return _sub("tmp")


def config_file() -> str:
    return os.path.join(vault_home(), "config.json")


def activity_file() -> str:
    return os.path.join(vault_home(), "activity.json")


def downloads_dir() -> str:
    """Where received/restored files land by default: ~/Downloads/A.N.Sx Vault (or the home folder as a fallback)."""
    home = os.path.expanduser("~")
    base = os.path.join(home, "Downloads")
    if not os.path.isdir(base):
        base = home
    path = os.path.join(base, "A.N.Sx Vault")
    os.makedirs(path, exist_ok=True)
    return path
