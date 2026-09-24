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
