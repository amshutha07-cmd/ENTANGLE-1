"""Desktop behaviour: one running copy per vault, a tray menu, and each shortcut bound exactly once."""
import time

import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtWidgets import QApplication  # noqa: E402

import single_instance  # noqa: E402
from ui.controller import AppController  # noqa: E402

app = QApplication.instance() or QApplication([])


def pump(s=0.3, until=lambda: False):
    end = time.time() + s
    while time.time() < end and not until():
        app.processEvents()
        time.sleep(0.01)


def test_a_second_launch_brings_the_first_window_forward(tmp_path):
    name = single_instance.server_name(str(tmp_path))
    assert single_instance.server_name(str(tmp_path)) == name                      # stable per vault folder
    assert single_instance.server_name(str(tmp_path / "other")) != name            # separate vaults don't collide
    shown = []
    server = single_instance.listen(name, lambda: shown.append(1))
    assert server is not None
    assert single_instance.notify_running(name)                                    # "already running" → show it
    pump(2, until=lambda: bool(shown))
    assert shown == [1]
    server.close()


def test_no_running_copy_means_start_normally(tmp_path):
    t = time.time()
    assert not single_instance.notify_running(single_instance.server_name(str(tmp_path / "nobody")))
    assert time.time() - t < 2                                                     # no noticeable delay at startup


def test_tray_menu_opens_locks_and_quits(monkeypatch):
    from PyQt6.QtWidgets import QSystemTrayIcon
    from ui.main_window import MainWindow
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True))
    win = MainWindow(AppController())
    assert win._tray is not None
    labels = [a.text() for a in win._tray_menu.actions()]
    assert labels == ["Open A.N.Sx Vault", "Lock now", "", "Quit"]
    win._tray_menu_opening()
    assert not win._tray_lock.isEnabled()                                          # signed out: nothing to lock
    win.root_stack.setCurrentIndex(1)
    win._tray_menu_opening()
    assert win._tray_lock.isEnabled()
    win.showMinimized()
    win.bring_to_front()
    assert not win.isMinimized() and win.isVisible()
    win._tray.hide()


def test_each_shortcut_is_bound_exactly_once():
    from PyQt6.QtGui import QShortcut
    from ui.main_window import MainWindow
    win = MainWindow(AppController())
    from PyQt6.QtCore import Qt
    window_wide = [s for s in win.findChildren(QShortcut) if s.context() == Qt.ShortcutContext.WindowShortcut]
    keys = [s.key().toString() for s in window_wide]              # (pages also have their own Esc, scoped to them)
    assert sorted(keys) == sorted(["Ctrl+O", "Ctrl+L"] + [f"Ctrl+{i}" for i in range(1, 7)])


def test_mac_menu_bar_holds_the_commands(monkeypatch):
    from PyQt6.QtGui import QShortcut
    from ui.main_window import MainWindow
    monkeypatch.setattr(QApplication, "platformName", staticmethod(lambda: "cocoa"))
    win = MainWindow(AppController())
    assert list(win._menus) == ["File", "Go", "Help"]
    shortcuts = [a.shortcut().toString() for m in win._menus.values() for a in m.actions() if not a.shortcut().isEmpty()]
    assert sorted(shortcuts) == sorted(["Ctrl+O", "Ctrl+L"] + [f"Ctrl+{i}" for i in range(1, 7)])
    from PyQt6.QtCore import Qt
    assert not [s for s in win.findChildren(QShortcut)
                if s.context() == Qt.ShortcutContext.WindowShortcut]                   # not also bound on the window
    about = [a for a in win._menus["Help"].actions() if a.text().startswith("About")][0]
    assert about.menuRole() == about.MenuRole.AboutRole                            # lives in the app menu on macOS
