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
    assert sorted(keys) == sorted(["Ctrl+O", "Ctrl+L", "Ctrl+Q"] + [f"Ctrl+{i}" for i in range(1, 7)])


def test_mac_menu_bar_holds_the_commands(monkeypatch):
    from PyQt6.QtGui import QShortcut
    from ui.main_window import MainWindow
    monkeypatch.setattr(QApplication, "platformName", staticmethod(lambda: "cocoa"))
    win = MainWindow(AppController())
    assert list(win._menus) == ["File", "Go", "Help"]
    shortcuts = [a.shortcut().toString() for m in win._menus.values() for a in m.actions() if not a.shortcut().isEmpty()]
    assert sorted(shortcuts) == sorted(["Ctrl+O", "Ctrl+L", "Ctrl+Q"] + [f"Ctrl+{i}" for i in range(1, 7)])
    from PyQt6.QtCore import Qt
    assert not [s for s in win.findChildren(QShortcut)
                if s.context() == Qt.ShortcutContext.WindowShortcut]                   # not also bound on the window
    about = [a for a in win._menus["Help"].actions() if a.text().startswith("About")][0]
    assert about.menuRole() == about.MenuRole.AboutRole                            # lives in the app menu on macOS


def test_a_closed_window_leaves_nothing_listening():
    """App-wide event filters belong to their window: once it is gone, a click anywhere must not reach them."""
    from PyQt6.QtCore import QCoreApplication, QEvent, Qt
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QPushButton
    from ui.main_window import MainWindow
    win = MainWindow(AppController())
    assert win._watcher.parent() is win and win._focus_visible.parent() is win
    win.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    other = QPushButton("elsewhere")
    other.show()
    QTest.mouseClick(other, Qt.MouseButton.LeftButton)               # used to abort: a dead window's filter ran
    QTest.keyClick(other, Qt.Key.Key_A)
    other.close()



class _CloseButton:
    """The close event the window manager sends when the close button is clicked (spontaneous)."""
    def __init__(self):
        self.ignored = False

    def spontaneous(self):
        return True

    def ignore(self):
        self.ignored = True


class _Tray:
    def __init__(self):
        self.messages = []

    def showMessage(self, title, text, *a):
        self.messages.append(title)

    def hide(self):
        pass


def test_closing_the_window_keeps_it_running_in_the_tray(monkeypatch):
    from ui.controller import set_pref
    from ui.main_window import MainWindow
    set_pref("close_to_tray", True)
    set_pref("tray_hint_shown", False)
    ctl = AppController()
    win = MainWindow(ctl)
    win._tray = _Tray()
    win.show()
    ctl.operator = "someone"                                   # signed in
    ev = _CloseButton()
    win.closeEvent(ev)
    assert ev.ignored and win.isHidden() and ctl.operator == "someone"      # still running, still receiving
    assert win._tray.messages == ["A.N.Sx Vault is still running"]          # told once where it went
    win.show()
    win.closeEvent(_CloseButton())
    assert len(win._tray.messages) == 1                                      # …only once
    win.bring_to_front()
    assert win.isVisible()


def test_quit_really_quits(monkeypatch):
    from ui.main_window import MainWindow
    ctl = AppController()
    win = MainWindow(ctl)
    win._tray = _Tray()
    logged_out = []
    monkeypatch.setattr(ctl, "logout", lambda: logged_out.append(1))
    win.quit_app()
    assert logged_out and not win.isVisible()


def test_quit_setting_closes_for_real(monkeypatch):
    from ui.controller import set_pref
    from ui.main_window import MainWindow
    set_pref("close_to_tray", False)
    ctl = AppController()
    win = MainWindow(ctl)
    win._tray = _Tray()
    assert not win._keep_running(_CloseButton())               # "Quit the app" chosen in Settings
    set_pref("close_to_tray", True)
    assert win._keep_running(_CloseButton())
    win._tray = None
    assert not win._keep_running(_CloseButton())               # no tray on this desktop: closing quits


def test_settings_offer_the_choice():
    from ui.controller import pref
    from ui.pages.settings import SettingsPage
    page = SettingsPage(AppController())
    page._loading = False
    page.close_combo.setCurrentIndex(1)
    assert pref("close_to_tray") is False
    page.close_combo.setCurrentIndex(0)
    assert pref("close_to_tray") is True
