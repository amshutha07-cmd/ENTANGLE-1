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

# No test may use the real local network. The app's peer discovery would announce every test identity to each
# A.N.Sx Vault on this Wi-Fi (a copy running on this very computer included, which saves them as contacts), and
# while a test held the port, the real app could not listen on it.
import web3_bridge  # noqa: E402

web3_bridge._LAN.start_listener = lambda: None
web3_bridge._LAN.announce = lambda *_args, **_kwargs: None


# ── shared: a real relay on a local port for integration tests ───────────────────────────────────
import socket
import threading
import time
import traceback

import pytest

# ── a Python error inside a Qt callback fails that test instead of killing the whole run ─────────────
_QT_ERRORS: list = []


def _record_qt_error(kind, value, tb) -> None:
    """
    PyQt hands an exception raised inside a Qt callback (a slot, a timer, paintEvent…) to sys.excepthook. With the
    default hook it calls abort() instead: every later test is lost, and so is the report of which one failed.
    """
    _QT_ERRORS.append("".join(traceback.format_exception(kind, value, tb)))
    sys.__stderr__.write(_QT_ERRORS[-1])


sys.excepthook = _record_qt_error


@pytest.fixture(scope="session")
def live_relay(tmp_path_factory):
    requests = pytest.importorskip("requests")
    uvicorn = pytest.importorskip("uvicorn")
    from relay.server import Config, create_app
    cfg = Config(data_dir=str(tmp_path_factory.mktemp("live_relay")), register_per_ip_hour=10_000,
                 transfers_per_user_hour=10_000, chunk_size=64 * 1024)
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = uvicorn.Server(uvicorn.Config(create_app(cfg), host="127.0.0.1", port=port, log_level="warning", ws="none"))
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
        wait_for_all_jobs = None
    if wait_for_all_jobs is not None:
        _end_sessions_left_open()
        wait_for_all_jobs()
        _delete_windows_now()
    if _QT_ERRORS:
        errors = "\n".join(_QT_ERRORS)
        _QT_ERRORS.clear()
        pytest.fail(f"an exception was raised inside a Qt callback during this test:\n{errors}", pytrace=False)


def _end_sessions_left_open():
    """
    A test that stops halfway (a failed assert) never reaches its own logout, and its session keeps polling the relay
    into later tests. End it here, while its window still exists.
    """
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        return
    for w in app.topLevelWidgets():
        ctl = getattr(w, "ctl", None)
        if ctl is not None and getattr(ctl, "operator", None):
            ctl.logout()


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
