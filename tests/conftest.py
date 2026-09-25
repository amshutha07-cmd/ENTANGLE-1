import os
import sys
import tempfile

# Isolate every test run from the developer's real ~/.ansx_vault and OS keychain.
_REAL_HOME = os.path.expanduser("~")
os.environ.setdefault("SOLCX_BINARY_PATH", os.path.join(_REAL_HOME, ".solcx"))   # keep the cached compiler
_TMP = tempfile.mkdtemp(prefix="ansx_test_")
os.environ["ANSX_VAULT_HOME"] = _TMP
os.environ["HOME"] = _TMP            # code that uses ~/.ansx_vault must not touch the real profile
os.environ["USERPROFILE"] = _TMP
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["ANSX_PLATFORM_SECRET_BACKEND"] = "file"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── shared: a real relay on a local port for integration tests ───────────────────────────────────
import socket
import threading
import time

import pytest


@pytest.fixture(scope="session")
def live_relay(tmp_path_factory):
    requests = pytest.importorskip("requests")
    uvicorn = pytest.importorskip("uvicorn")
    from relay.server import Config, create_app
    cfg = Config(data_dir=str(tmp_path_factory.mktemp("live_relay")), register_per_ip_hour=10_000,
                 transfers_per_user_hour=10_000, chunk_size=64 * 1024)
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = uvicorn.Server(uvicorn.Config(create_app(cfg), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(100):
        try:
            requests.get(f"http://127.0.0.1:{port}/", timeout=0.2)
            break
        except requests.RequestException:
            time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True


@pytest.fixture(autouse=True)
def _no_background_jobs_left_behind():
    """Each test ends with its background jobs finished, so no thread outlives the objects it belongs to."""
    yield
    try:
        from ui.controller import wait_for_all_jobs
    except Exception:                                   # tests that never touch the UI
        return
    wait_for_all_jobs()
    _delete_windows_now()


def _delete_windows_now():
    """
    Delete the windows a test made, at a controlled moment. Left to Python's garbage collector, a window can be
    destroyed in the middle of Qt delivering an event to it, which crashes the process. (The app itself keeps one
    window for its whole life; only tests create and drop many.)
    """
    from PyQt6.QtCore import QCoreApplication, QEvent
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        return
    for w in app.topLevelWidgets():
        w.hide()
        w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
