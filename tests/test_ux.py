"""Behaviour people notice: auto-lock never cuts off a transfer, quitting mid-transfer asks, the Sent list names files,
offline states explain themselves, long lists can be searched, and the window reopens where it was."""
import time

import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtWidgets import QApplication  # noqa: E402

from security_core import SecurityCore, VaultLedger  # noqa: E402
from ui.controller import AppController, set_pref  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402

app = QApplication.instance() or QApplication([])


def pump(s=0.2):
    end = time.time() + s
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


@pytest.fixture
def signed_in(monkeypatch):
    monkeypatch.setenv("ANSX_REDUCE_MOTION", "1")
    SecurityCore._publish_identity = classmethod(lambda cls, *a, **k: None)
    if "ux_user" not in SecurityCore.list_registered_users():
        SecurityCore.establish_identity("ux_user", "a long passphrase for ux", auth="passphrase")
    SecurityCore.verify_login("ux_user", "a long passphrase for ux")
    ctl = AppController()
    ctl.operator = "ux_user"
    win = MainWindow(ctl)
    win.resize(1200, 800)
    win.show()
    win._session_started("ux_user")
    pump()
    yield ctl, win
    ctl.operator = None


class Running:
    """Stands in for a job that is still working."""
    def __init__(self, name):
        self.name = name

    def isRunning(self):
        return True

    def cancel(self):
        pass

    def wait(self, ms):
        pass


def test_auto_lock_waits_for_a_transfer_and_warns_first(signed_in):
    ctl, win = signed_in
    ctl._jobs.add(Running("send"))
    win._idle.timeout.emit()                                 # the idle time is up, but a file is still being sent
    assert ctl.operator == "ux_user" and win._idle.isActive()      # not locked; checks again later
    ctl._jobs.clear()
    ctl._jobs.add(Running("directory"))                      # a background lookup is not work anyone loses
    assert not ctl.busy()
    ctl._jobs.clear()
    set_pref("auto_lock_minutes", 10)
    win._activity()
    assert win._idle_warn.isActive() and 0 < win._idle_warn.remainingTime() <= (600 - 30) * 1000
    before = len(win.toasts._toasts())
    win._idle_warn.timeout.emit()
    assert len(win.toasts._toasts()) == before + 1          # "Locking in 30 seconds…"
    set_pref("auto_lock_minutes", 0)


def test_quitting_during_a_transfer_asks_first(signed_in, monkeypatch):
    ctl, win = signed_in
    import ui.dialogs
    answers = []
    monkeypatch.setattr(ui.dialogs, "confirm", lambda *a, **k: answers.append(a[1]) or False)
    ctl._jobs.add(Running("accept"))
    win.close()
    assert answers and ctl.operator == "ux_user" and win.isVisible()   # "Keep working" keeps everything running
    ctl._jobs.clear()


def test_window_reopens_where_it_was(signed_in, monkeypatch):
    from PyQt6.QtCore import QRect
    from ui.controller import pref
    ctl, win = signed_in
    win.setGeometry(QRect(40, 30, 1000, 700))
    pump(0.1)
    win.close()
    saved = pref("window_place")
    assert (saved["w"], saved["h"], saved["max"]) == (1000, 700, False)
    screen = QApplication.primaryScreen().availableGeometry()
    fits = saved["w"] <= screen.width() and saved["h"] <= screen.height()
    again = MainWindow(AppController())
    if fits:
        assert (again.width(), again.height()) == (1000, 700)      # back as it was
    else:
        assert (again.width(), again.height()) == (1200, 800)      # too big for this screen: the default instead
    set_pref("window_place", None)


def test_sent_list_names_the_file(signed_in):
    ctl, win = signed_in
    ctl.remember_sent("t-42", "Q3 board deck.pdf")
    assert ctl.sent_file_name("t-42") == "Q3 board deck.pdf" and ctl.sent_file_name("nope") == ""
    now = int(time.time())
    ctl.outbox = [{"id": "t-42", "to": "sam", "size": 1000, "created": now, "state": "ready"},
                  {"id": "t-43", "to": "alex", "size": 1000, "created": now, "state": "ready"}]
    ib = win.pages["inbox"]
    ib._fill_sent(ctl.outbox)
    rows = [ib.sent_list.itemWidget(ib.sent_list.item(i)) for i in range(2)]
    assert rows[0].title.text() == "Q3 board deck.pdf" and rows[0].subtitle.text().startswith("To sam")
    assert rows[1].title.text() == "To alex"                 # sent before names were remembered


def test_offline_is_explained_where_it_matters(signed_in):
    ctl, win = signed_in
    ctl._set_relay_state("offline", "Relay unreachable")
    for key in ("send", "inbox"):
        win.go(key)
        pump(0.05)
        assert win.pages[key].conn_banner.isVisible() and "offline" in win.pages[key].conn_banner._text.text()
    ctl._set_relay_state("online", "Connected")
    assert not win.pages["inbox"].conn_banner.isVisible()


def test_long_lists_get_a_search_box(signed_in):
    ctl, win = signed_in
    for i in range(8):
        VaultLedger.add_entry(f"file-{i}.pdf" if i % 2 else f"photo-{i}.jpg", f"/nowhere/{i}.png",
                              "2026-09-20 10:00:00", size=1000, storage={"inline": 12}, owner="ux_user")
    vp = win.pages["vault"]
    win.go("vault")
    vp.refresh()
    assert vp.filter.isVisible()
    vp.filter.setText("photo")
    rows = [vp.list_card.body.itemAt(i).widget() for i in range(vp.list_card.body.count())]
    assert len(rows) == 4
    vp.filter.setText("zzz")
    assert vp.list_card.body.count() == 1                    # "No match", not an empty card
    vp.filter.setText("")
    sp = win.pages["send"]
    win.go("send")
    assert sp.file_search.isVisible()
    sp.file_search.setText("file-")
    assert sp.files.count() == 4 and sp.files.maximumHeight() < 6 * 62 + 20


def test_icon_only_buttons_have_names():
    from ui.widgets import Button
    b = Button("", "ghost", "trash", "sm")
    b.setToolTip("Remove from my vault")
    assert b.accessibleName() == "Remove from my vault"
    named = Button("Send", "primary")
    named.setToolTip("Send this file")
    assert named.accessibleName() == ""                      # a button with text is already named by its text
