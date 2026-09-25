"""Small UI helpers: friendly dates, file-type icons, the sidebar count badge, and the contacts empty state."""
import datetime

import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtWidgets import QApplication  # noqa: E402

from ui import icons  # noqa: E402
from ui.widgets import EmptyState, NavButton, file_icon, friendly_date  # noqa: E402

app = QApplication.instance() or QApplication([])


def test_friendly_dates():
    now = datetime.datetime.now()
    y = now - datetime.timedelta(days=1)
    assert friendly_date(now.strftime("%Y-%m-%d 09:05:00")) == "Today, 09:05"
    assert friendly_date(y.strftime("%Y-%m-%d 23:59:59")) == "Yesterday, 23:59"
    assert friendly_date(f"{now.year - 1}-03-07 10:00:00") == f"Mar 7, {now.year - 1}"
    assert friendly_date("not a date") == "not a date"


def test_file_types_get_their_own_icon_and_every_icon_exists():
    assert file_icon("Q3 board deck.PDF") == ("doc", "danger")
    assert file_icon("passport-scan.jpg") == ("image", "info")
    assert file_icon("budget.xlsx") == ("sheet", "success")
    assert file_icon("backup.tar.gz") == ("archive", "warning")
    assert file_icon("README") == ("file", "neutral")
    from ui.widgets import _FILE_KINDS
    assert {icon for icon, _k in _FILE_KINDS.values()} <= set(icons.available())


def test_sidebar_badge_is_a_count_not_text():
    b = NavButton("Inbox", "inbox")
    b.set_badge(3)
    assert b.text() == "Inbox" and b.badge() == 3 and b.accessibleName() == "Inbox, 3 waiting"
    b.set_badge(0)
    assert b.accessibleName() == "Inbox"
    b.resize(200, 44)
    b.set_badge(120)
    b.grab()                                                        # paints "99+" without error


def test_empty_state_text_can_change():
    e = EmptyState("users", "No one yet", "Import someone.")
    e.set_text("No match", "Nobody called “zed”.")
    assert (e._title.text(), e._text.text()) == ("No match", "Nobody called “zed”.")


def test_fit_stack_is_as_tall_as_the_page_on_screen():
    from PyQt6.QtWidgets import QVBoxLayout, QWidget
    from ui.widgets import FitStack, label

    def page(lines):
        w = QWidget()
        lay = QVBoxLayout(w)
        for _ in range(lines):
            lay.addWidget(label("A line of explanation that wraps when the column is narrow enough to need it.", "body"))
        return w

    st = FitStack()
    st.addWidget(page(1))
    st.addWidget(page(12))
    assert st.sizeHint().height() < st.widget(1).sizeHint().height()           # short page: short stack
    assert st.layout().heightForWidth(300) < st.widget(1).heightForWidth(300)  # also for wrapped text
    seen = []
    st.currentChanged.connect(seen.append)
    st.setCurrentIndex(1)
    assert seen == [1] and st.currentIndex() == 1 and st.sizeHint().height() == st.widget(1).sizeHint().height()


def test_long_labels_shorten_but_keep_their_text():
    from ui.widgets import ElidedLabel
    name = "an extremely long file name that would never fit in a narrow window.pdf"
    lb = ElidedLabel(name)
    lb.resize(120, 20)
    lb.show()
    app.processEvents()
    assert lb.text() == name and lb.toolTip() == name
    from PyQt6.QtWidgets import QLabel
    assert QLabel.text(lb).endswith("…") and len(QLabel.text(lb)) < len(name)
    assert lb.minimumSizeHint().width() < 120                                # never forces its row wider


def test_banner_puts_its_button_under_the_text_when_narrow():
    from PyQt6.QtWidgets import QBoxLayout
    from ui.widgets import Banner
    b = Banner("You haven't verified sam's key. Compare the fingerprint with them first.", "warning", "I verified it")
    b.show()                                                                  # resize events reach shown widgets only
    b.resize(900, 60)
    assert b._inner.direction() == QBoxLayout.Direction.LeftToRight
    b.resize(360, 120)
    assert b._inner.direction() == QBoxLayout.Direction.TopToBottom
    b._btn.hide()
    b.resize(361, 120)
    assert b._inner.direction() == QBoxLayout.Direction.LeftToRight            # no button: nothing to stack


def test_a_finished_job_is_announced_only_when_you_are_elsewhere():
    from PyQt6.QtCore import QObject, pyqtSignal
    from ui.pages.base import Page

    class Ctl(QObject):
        toast = pyqtSignal(str, str)

    ctl, seen = Ctl(), []
    ctl.toast.connect(lambda t, k: seen.append(t))
    page = Page(ctl, "Protect a file")
    page.notify_if_away("report.pdf is protected.", "success")         # page not on screen: tell them
    page.show()
    app.processEvents()
    page.notify_if_away("notes.txt is protected.", "success")          # on screen: the page already says so
    assert seen == ["report.pdf is protected."]


def test_keyboard_users_can_reach_and_use_everything():
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from ui.widgets import Button, ClickableCard
    b = Button("Send", "primary")
    assert b.focusPolicy() == Qt.FocusPolicy.TabFocus       # a ring when tabbing, none after a mouse click
    tile = ClickableCard()
    tile.show()
    hits = []
    tile.clicked.connect(lambda: hits.append(1))
    for key in (Qt.Key.Key_Return, Qt.Key.Key_Space, Qt.Key.Key_A):
        QTest.keyClick(tile, key)
    assert hits == [1, 1] and tile.focusPolicy() == Qt.FocusPolicy.TabFocus


def test_window_title_shows_what_is_waiting():
    from ui.controller import AppController
    from ui.main_window import MainWindow
    win = MainWindow(AppController())
    win._set_waiting(2)
    assert win.windowTitle() == "A.N.Sx Vault (2 waiting)" and win.nav["inbox"].badge() == 2
    win._set_waiting(0)
    assert win.windowTitle() == "A.N.Sx Vault"


def test_status_pills_and_new_file_toast_lead_somewhere():
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from ui.controller import AppController
    from ui.main_window import MainWindow
    win = MainWindow(AppController())
    win.resize(1200, 800)
    win.show()
    win.root_stack.setCurrentIndex(1)                        # signed-in shell
    QTest.mouseClick(win.relay_pill, Qt.MouseButton.LeftButton)
    assert win.content.currentWidget() is win.pages["settings"]
    win.go("home")
    win._new_transfers([{"from": "sam", "id": "t1"}])
    toast = win.toasts._toasts()[-1]
    QTest.mouseClick(toast, Qt.MouseButton.LeftButton)
    assert win.content.currentWidget() is win.pages["inbox"]


def test_passphrase_fields_can_be_revealed():
    from PyQt6.QtWidgets import QLineEdit
    from ui.widgets import add_reveal_toggle
    e = QLineEdit()
    e.setEchoMode(QLineEdit.EchoMode.Password)
    add_reveal_toggle(e)
    e._reveal_action.trigger()
    assert e.echoMode() == QLineEdit.EchoMode.Normal and e._reveal_action.toolTip() == "Hide passphrase"
    e._reveal_action.trigger()
    assert e.echoMode() == QLineEdit.EchoMode.Password and e._reveal_action.toolTip() == "Show passphrase"


def test_dropping_a_file_anywhere_protects_it(tmp_path):
    from PyQt6.QtCore import QMimeData, QUrl
    from ui.controller import AppController
    from ui.main_window import MainWindow
    f = tmp_path / "scan.pdf"
    f.write_bytes(b"x" * 100)
    win = MainWindow(AppController())
    win.root_stack.setCurrentIndex(1)
    started = []
    win.pages["vault"].protect_files = started.append

    class Drop:
        def __init__(self, paths):
            self.m = QMimeData()
            self.m.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
            self.accepted = False

        def mimeData(self):
            return self.m

        def acceptProposedAction(self):
            self.accepted = True

    d = Drop([f])
    win.dragEnterEvent(d)
    assert d.accepted
    win.dropEvent(d)
    assert started == [[str(f)]] and win.content.currentWidget() is win.pages["vault"]
    g = tmp_path / "notes.txt"
    g.write_bytes(b"y")
    two = Drop([f, g])                                        # several files: all of them, one after another
    win.dragEnterEvent(two)
    win.dropEvent(two)
    assert two.accepted and started[-1] == [str(f), str(g)]
    folder = Drop([tmp_path])                                 # a folder is not a file
    win.dragEnterEvent(folder)
    assert not folder.accepted
    win.root_stack.setCurrentIndex(0)                         # signed out: nothing to protect with
    locked = Drop([f])
    win.dragEnterEvent(locked)
    assert not locked.accepted


def test_a_revealed_passphrase_is_hidden_again_for_the_next_person():
    from PyQt6.QtWidgets import QLineEdit
    from ui.controller import AppController
    from ui.pages.auth import LoginView
    lv = LoginView(AppController(), lambda: None)
    lv.pass_edit._reveal_action.trigger()
    assert lv.pass_edit.echoMode() == QLineEdit.EchoMode.Normal
    lv.refresh()                                             # the sign-in screen is shown again (e.g. after locking)
    assert lv.pass_edit.echoMode() == QLineEdit.EchoMode.Password


def test_focus_rings_appear_only_for_keyboard_use():
    import time
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QPushButton
    from security_core import SecurityCore
    from ui.controller import AppController
    from ui.main_window import MainWindow
    SecurityCore._publish_identity = classmethod(lambda cls, *a, **k: None)
    SecurityCore.establish_identity("focus_user", "a long passphrase focus", auth="passphrase")
    ctl = AppController()
    ctl.operator = "focus_user"
    win = MainWindow(ctl)
    win.resize(1200, 800)
    win.show()
    win.activateWindow()
    win._session_started("focus_user")                      # the sign-in field disappears: Qt moves focus along

    def settle():
        end = time.time() + 0.3
        while time.time() < end:
            app.processEvents()
            time.sleep(0.01)
    settle()
    assert not isinstance(QApplication.focusWidget(), QPushButton)   # no ring nobody asked for
    QTest.keyClick(win, Qt.Key.Key_Tab)
    settle()
    assert isinstance(QApplication.focusWidget(), QPushButton)       # Tab: a ring shows where you are


def _contrast(a: str, b: str) -> float:
    def lum(h):
        h = h.lstrip("#")
        r, g, b_ = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
        f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b_)
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_every_text_colour_meets_wcag_aa_in_both_themes():
    """4.5:1 for normal-size text (WCAG 2.x AA). Guards against a palette tweak quietly making text unreadable."""
    from ui.theme import DARK, LIGHT
    pairs = [("text", "bg"), ("text", "surface"), ("text", "surface_alt"),
             ("text_muted", "bg"), ("text_muted", "surface"), ("text_muted", "surface_alt"),
             ("text_faint", "bg"), ("text_faint", "surface"), ("text_faint", "surface_alt"),
             ("on_primary", "primary_fill"), ("on_primary", "primary_fill_hover"), ("on_primary", "primary_fill_pressed"),
             ("primary", "surface"), ("primary_on_soft", "primary_soft"),
             ("success", "success_soft"), ("warning", "warning_soft"), ("danger", "danger_soft"), ("info", "info_soft")]
    low = [(name, fg, bg, round(_contrast(t[fg], t[bg]), 2)) for name, t in (("dark", DARK), ("light", LIGHT))
           for fg, bg in pairs if _contrast(t[fg], t[bg]) < 4.5]
    assert not low, f"text below 4.5:1: {low}"
    # and the soft highlight must still be visible against the card it sits on (not merged into it)
    assert _contrast(DARK["primary_soft"], DARK["surface"]) > 1.15 and _contrast(LIGHT["primary_soft"], LIGHT["surface"]) > 1.15


def test_app_icon_comes_in_every_size_a_desktop_asks_for():
    from ui import icons
    sizes = {s.width() for s in icons.app_icon().availableSizes()}
    assert {16, 32, 64, 128, 256, 512} <= sizes


def test_dock_badge_and_background_notifications(monkeypatch):
    from ui.controller import AppController
    from ui.main_window import MainWindow
    badges = []
    monkeypatch.setattr(QApplication, "setBadgeNumber", lambda self, n: badges.append(n), raising=False)
    win = MainWindow(AppController())
    win._set_waiting(3)
    win._set_waiting(0)
    assert badges[-2:] == [3, 0]                             # the Dock/taskbar icon counts waiting files

    class Tray:
        def __init__(self):
            self.messages = []

        def showMessage(self, title, text, icon, ms):
            self.messages.append(text)
    win._tray = Tray()
    monkeypatch.setattr(win, "isActiveWindow", lambda: False)
    win.toasts_show("sam received your file.", "success")   # in the background: the system shows it too
    win.toasts_show("Copied.", "info")                       # small confirmations stay in the app
    monkeypatch.setattr(win, "isActiveWindow", lambda: True)
    win.toasts_show("alex received your file.", "success")  # in front: the in-app notice is enough
    assert win._tray.messages == ["sam received your file."]
