"""
main.py — A.N.Sx Vault desktop application entry point.

    python main.py

Environment: ANSX_RELAY_URL (relay address; can also be set in Settings), ANSX_VAULT_HOME (data folder).
"""
from __future__ import annotations

import logging
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtCore import QCoreApplication, Qt
from PyQt6.QtWidgets import QApplication, QMessageBox

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%H:%M:%S")


def self_test() -> int:
    """`main.py --self-test`: check everything the app depends on, without opening a window. Exit code 0 = healthy."""
    import tempfile
    problems = 0

    def check(name: str, fn) -> None:
        nonlocal problems
        try:
            detail = fn()
            print(f"  [ok]   {name}" + (f": {detail}" if detail else ""))
        except Exception as exc:
            problems += 1
            print(f"  [FAIL] {name}: {exc}")

    print("A.N.Sx Vault self-test")
    print(f"  python {sys.version.split()[0]} on {sys.platform}{' (packaged app)' if getattr(sys, 'frozen', False) else ''}")

    def engine_roundtrip():
        import engine
        if not engine.available():
            raise RuntimeError("native engine not found - run: python build_engine.py")
        with tempfile.TemporaryDirectory() as d:
            src, out, shards = os.path.join(d, "a.bin"), os.path.join(d, "b.bin"), os.path.join(d, "s")
            payload = os.urandom(50_000)
            with open(src, "wb") as f:
                f.write(payload)
            engine.shatter(src, "self-test-key", out_dir=shards)
            engine.unshatter(shards, out, "self-test-key")
            with open(out, "rb") as f:
                if f.read() != payload:
                    raise RuntimeError("round trip produced different bytes")
        return f"engine v{engine.EXPECTED_VERSION}: encrypt, split into 12, rebuild"

    def crypto():
        from cryptography.hazmat.primitives.asymmetric import rsa
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        import ghost_map  # noqa: F401
        return "RSA + AES-GCM available"

    def qt():
        from PyQt6.QtCore import PYQT_VERSION_STR
        from PyQt6 import QtSvg  # noqa: F401  (icons)
        return f"PyQt6 {PYQT_VERSION_STR} with SVG support"

    def keys():
        import platform_secret
        return f"keys protected by: {platform_secret.backend_name()}"

    def relay():
        import relay_config
        url = relay_config.get_relay_url()
        return f"{url}{'' if relay_config.is_secure(url) else '  (not https)'}"

    def storage():
        import cloud_dispatcher
        n = len(cloud_dispatcher.load_targets())
        return f"{n} cloud storage account(s) configured" if n else "none configured (files up to 10 MB only)"

    for name, fn in (("encryption engine", engine_roundtrip), ("cryptography", crypto), ("desktop UI toolkit", qt),
                     ("key protection", keys), ("relay address", relay), ("cloud storage", storage)):
        check(name, fn)
    print("Result:", "all good" if not problems else f"{problems} problem(s) found")
    return 1 if problems else 0


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    QCoreApplication.setApplicationName("A.N.Sx Vault")
    QCoreApplication.setOrganizationName("ANSX")
    app = QApplication(sys.argv)

    from ui import theme
    from ui.controller import pref
    theme.apply(app, pref("theme", "dark") if pref("theme", "dark") in ("dark", "light") else "dark")

    import engine
    if not engine.available():
        QMessageBox.warning(
            None, "Encryption engine not built",
            "The native encryption engine is missing, so files cannot be protected yet.\n\n"
            "Build it once with:\n    python build_engine.py\n\nThen restart the app. You can still look around.")

    from ui.main_window import MainWindow
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
