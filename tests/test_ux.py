"""Behaviour people notice: auto-lock never cuts off a transfer, quitting mid-transfer asks, the Sent list names files,
offline states explain themselves, long lists can be searched, and the window reopens where it was."""
import os
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
    assert win._idle_warn.isActive() and 560_000 < win._idle_warn.remainingTime() <= 571_000   # timers round a little
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


def test_work_in_progress_shows_from_any_other_page(signed_in):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    ctl, win = signed_in
    win.go("home")
    ctl.work_progress.emit("k1", "send", "Uploading to sam…", 42)
    assert win.work_pill.isVisible() and win.work_pill.text() == "Uploading to sam 42%"
    win.go("send")                                           # the Send page shows its own progress bar
    assert not win.work_pill.isVisible()
    win.go("home")
    assert win.work_pill.isVisible()
    QTest.mouseClick(win.work_pill, Qt.MouseButton.LeftButton)
    assert win.content.currentWidget() is win.pages["send"]
    win.go("home")
    ctl.work_done.emit("k1")
    assert not win.work_pill.isVisible()


def test_controller_reports_real_work_but_not_background_lookups(signed_in):
    from ui.controller import Job
    ctl, win = signed_in
    seen, done = [], []
    ctl.work_progress.connect(lambda k, page, label, pct: seen.append((page, label, pct)))
    ctl.work_done.connect(done.append)

    def work(progress, cancelled):
        progress("Storing pieces…", 50)
        return "ok"
    for name in ("protect", "directory"):
        job = Job(work, name)
        ctl.run_job(job)
        end = time.time() + 5
        while job.isRunning() or time.time() < end and not job.isFinished():
            app.processEvents()
            time.sleep(0.01)
        pump(0.2)
    assert ("vault", "Protecting…", 0) in seen and ("vault", "Storing pieces…", 50) in seen
    assert all(page == "vault" for page, _l, _p in seen) and len(done) == 1   # the directory job stayed quiet


def test_send_can_protect_a_new_file_and_carry_on(signed_in, monkeypatch, tmp_path):
    from types import SimpleNamespace
    from ui.controller import Job
    ctl, win = signed_in
    src = tmp_path / "minutes.docx"
    src.write_bytes(b"x" * 500)
    made = []

    def fake_protect(path):
        def work(progress, cancelled):
            progress("Encrypting and splitting your file…", 30)
            entry = VaultLedger.add_entry(os.path.basename(path), "/nowhere/new.png", "2026-09-25 09:00:00",
                                          size=500, storage={"inline": 12}, owner="ux_user")
            return SimpleNamespace(entry=entry, cloud=0, inline=12, upload_errors={}, storage_configured=False)
        job = Job(work, "protect")
        made.append(job)
        return job
    monkeypatch.setattr(ctl, "make_protect_job", fake_protect)
    sp = win.pages["send"]
    monkeypatch.setattr(sp, "_refresh_people", lambda: None)
    win.go("send")
    sp.protect_and_continue(str(src))
    assert made and made[0].page == "send" and not sp.next_btn.isEnabled()   # busy: can't continue yet
    end = time.time() + 5
    while sp._job is not None and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    entry = VaultLedger.get(sp.entry_id)
    assert sp.step == 1 and entry["original_filename"] == "minutes.docx"      # straight on to "Choose a person"


def test_dropping_a_file_on_the_send_page_protects_it_for_sending(signed_in, tmp_path):
    from PyQt6.QtCore import QMimeData, QUrl
    ctl, win = signed_in
    f = tmp_path / "plan.pdf"
    f.write_bytes(b"x")
    sp = win.pages["send"]
    routed = []
    sp.protect_and_continue = routed.append
    win.pages["vault"].protect_files = lambda ps: routed.append(("vault", ps[0]))

    class Drop:
        def __init__(self):
            self.m = QMimeData()
            self.m.setUrls([QUrl.fromLocalFile(str(f))])

        def mimeData(self):
            return self.m

        def acceptProposedAction(self):
            pass
    win.go("send")
    win.dropEvent(Drop())
    assert routed == [str(f)]                                # stays on Send, keeps the sending flow
    win.go("home")
    win.dropEvent(Drop())
    assert routed[-1] == ("vault", str(f))                   # anywhere else: Protect, as before


FP = "A2F901DE 2F4219ED 8D5187EE E11BD291 88E1224E 166B9D78 4A3BBF79 237B6083"


def test_verification_checks_every_group_before_trusting():
    from PyQt6.QtWidgets import QDialog
    from ui.dialogs import VerifyDialog
    d = VerifyDialog(None, "sam", FP)
    for i in range(7):
        assert d.group.text() == FP.split()[i]
        d._yes()
        assert d.result() != QDialog.DialogCode.Accepted      # not trusted until the last group matched
    d._yes()
    assert d.result() == QDialog.DialogCode.Accepted


def test_a_mismatch_stops_and_warns():
    from PyQt6.QtWidgets import QDialog
    from ui.dialogs import VerifyDialog
    d = VerifyDialog(None, "sam", FP)
    d.show()
    d._yes()
    d._no()
    assert d.mismatch and d.warning.isVisible() and "pretending" in d.warning._text.text()
    assert not d.yes_btn.isVisible() and d.cancel_btn.text() == "Close"
    d.close()
    assert d.result() != QDialog.DialogCode.Accepted


def test_holding_enter_confirms_nothing():
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from ui.dialogs import VerifyDialog
    d = VerifyDialog(None, "sam", FP)
    d.show()
    for _ in range(10):
        QTest.keyClick(d, Qt.Key.Key_Return)
    assert d.index == 0                                       # each group needs a deliberate "It matches"


def test_finished_steps_are_a_way_back(signed_in):
    from PyQt6.QtCore import QPoint, Qt
    from PyQt6.QtTest import QTest
    ctl, win = signed_in
    sp = win.pages["send"]
    win.go("send")
    VerifyEntry = VaultLedger.add_entry("deck.pdf", "/x/d.png", "2026-09-25 09:00:00", size=10, storage={}, owner="ux_user")
    sp._fill_files()
    sp.entry_id = VerifyEntry["id"]
    sp._go(1)
    pump(0.1)
    x_first = int(sp.stepper.width() / 3 / 2)                # middle of "Choose a file"
    QTest.mouseClick(sp.stepper, Qt.MouseButton.LeftButton, pos=QPoint(x_first, sp.stepper.height() // 2))
    assert sp.step == 0                                       # clicked a finished step: back there
    x_last = int(sp.stepper.width() * 5 / 6)
    QTest.mouseClick(sp.stepper, Qt.MouseButton.LeftButton, pos=QPoint(x_last, sp.stepper.height() // 2))
    assert sp.step == 0                                       # a step not reached yet is not a shortcut
    sp._go(1)
    sp.stepper.clickable = False                              # e.g. while sending
    QTest.mouseClick(sp.stepper, Qt.MouseButton.LeftButton, pos=QPoint(x_first, sp.stepper.height() // 2))
    assert sp.step == 1


def test_enter_picks_and_escape_goes_back(signed_in):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    ctl, win = signed_in
    sp = win.pages["send"]
    win.go("send")
    e = VaultLedger.add_entry("minutes.pdf", "/x/m.png", "2026-09-25 09:00:00", size=10, storage={}, owner="ux_user")
    sp._fill_files()
    sp._go(0)
    for i in range(sp.files.count()):
        if sp.files.item(i).data(Qt.ItemDataRole.UserRole) == e["id"]:
            sp.files.setCurrentRow(i)
    sp._refresh_people = lambda: None
    sp.files.setFocus()
    QTest.keyClick(sp.files, Qt.Key.Key_Return)
    assert sp.step == 1                                       # Enter = "Continue" with the highlighted file
    SecurityCore.pin_discovered_contact("sam_contact", SecurityCore.load_identity_for_user("ux_user")["public_key"], "relay")
    sp._fill_people()                                         # with a contact, the search box is shown
    win.activateWindow()
    sp.search.setText("zz")
    sp.search.setFocus()
    pump(0.05)
    sp._escape.activated.emit()
    assert sp.search.text() == "" and sp.step == 1            # Esc first clears the search…
    sp._escape.activated.emit()
    assert sp.step == 0                                       # …then goes back a step


def _fake_protect(ctl, monkeypatch, fail=()):
    """make_protect_job that 'protects' instantly (a real vault entry, no encryption), failing for names in `fail`."""
    from types import SimpleNamespace
    from ui.controller import Job

    def make(path):
        def work(progress, cancelled):
            name = os.path.basename(path)
            if name in fail:
                raise __import__("vault_service").VaultError("The file could not be read.")
            entry = VaultLedger.add_entry(name, f"/nowhere/{name}.png", "2026-09-25 09:00:00", size=10,
                                          storage={"inline": 12}, owner=ctl.operator)
            return SimpleNamespace(entry=entry, cloud=0, inline=12, upload_errors={}, storage_configured=False)
        return Job(work, "protect")
    monkeypatch.setattr(ctl, "make_protect_job", make)


def _wait(cond, s=8):
    end = time.time() + s
    while not cond() and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    return cond()


def test_several_files_are_protected_in_one_go_and_a_bad_one_does_not_stop_the_rest(signed_in, monkeypatch, tmp_path):
    ctl, win = signed_in
    _fake_protect(ctl, monkeypatch, fail={"broken.bin"})
    files = []
    for n in ("a.pdf", "broken.bin", "c.txt"):
        (tmp_path / n).write_bytes(b"x")
        files.append(str(tmp_path / n))
    vp = win.pages["vault"]
    win.go("vault")
    titles = []
    orig = vp.progress.title.setText
    vp.progress.title.setText = lambda t: (titles.append(t), orig(t))
    vp.protect_files(files)
    assert _wait(lambda: vp._job is None and not vp._queue)
    assert any("(2 of 3)" in t for t in titles)                    # you can see where it is in the batch
    msg = vp.result._text.text()
    assert "2 of 3 files protected" in msg and "broken.bin" in msg and not vp.result._btn.isVisible()


def test_cancelling_stops_the_whole_batch(signed_in, monkeypatch, tmp_path):
    ctl, win = signed_in
    vp = win.pages["vault"]
    for n in ("x1.pdf", "x2.pdf", "x3.pdf"):
        (tmp_path / n).write_bytes(b"x")
    vp._queue = [str(tmp_path / "x2.pdf"), str(tmp_path / "x3.pdf")]
    vp._cancelled()
    assert vp._queue == []


def test_send_them_a_file_skips_choosing_the_person(signed_in, monkeypatch):
    from PyQt6.QtCore import Qt
    ctl, win = signed_in
    SecurityCore.pin_discovered_contact("sam_friend", SecurityCore.load_identity_for_user("ux_user")["public_key"], "relay")
    e = VaultLedger.add_entry("plan.pdf", "/x/p.png", "2026-09-25 09:00:00", size=10, storage={}, owner="ux_user")
    cp = win.pages["contacts"]
    win.go("contacts")
    cp.select("sam_friend")
    cp.send_btn.click()
    sp = win.pages["send"]
    assert win.content.currentWidget() is sp and sp.recipient == "sam_friend" and sp.step == 0
    assert sp.files_heading.text() == "Which file should sam_friend get?"
    for i in range(sp.files.count()):
        if sp.files.item(i).data(Qt.ItemDataRole.UserRole) == e["id"]:
            sp.files.setCurrentRow(i)
    sp._next()
    assert sp.step == 2                                           # straight to review: the person was already chosen
    sp._back()
    assert sp.step == 1 and sp.recipient == "sam_friend"          # and they can still change their mind


def test_reply_from_the_inbox_sends_back_to_the_sender(signed_in):
    ctl, win = signed_in
    now = int(time.time())
    ctl.inbox = [{"id": "r1", "from": "alex_r", "size": 10, "created": now, "expires": now + 86400,
                  "sender_fingerprint": FP}]
    ib = win.pages["inbox"]
    win.go("inbox")
    ib._fill_inbox(ctl.inbox)
    ib.reply_btn.click()
    assert win.content.currentWidget() is win.pages["send"] and win.pages["send"].recipient == "alex_r"


def test_the_people_you_send_to_come_first(signed_in):
    from PyQt6.QtCore import Qt
    ctl, win = signed_in
    key = SecurityCore.load_identity_for_user("ux_user")["public_key"]
    for n in ("aaron_new", "zoe_often"):
        SecurityCore.pin_discovered_contact(n, key, "relay")
    now = int(time.time())
    ctl.outbox = [{"id": "o1", "to": "zoe_often", "size": 1, "created": now - 60, "state": "delivered"}]
    sp = win.pages["send"]
    sp._fill_people()
    order = [sp.people.item(i).data(Qt.ItemDataRole.UserRole) for i in range(sp.people.count())]
    assert order.index("zoe_often") < order.index("aaron_new")    # recent beats alphabetical
    row = sp.people.itemWidget(sp.people.item(order.index("zoe_often")))
    assert "last sent them a file" in row.subtitle.text()


def test_home_tiles_show_live_status(signed_in):
    ctl, win = signed_in
    hp = win.pages["home"]
    now = int(time.time())
    VaultLedger.add_entry("one.pdf", "/x/1.png", "2026-09-25 09:00:00", size=1, storage={}, owner="ux_user")
    ctl.remember_sent("s9", "one.pdf")
    ctl.outbox = [{"id": "s9", "to": "sam", "size": 1, "created": now - 120, "state": "ready"}]
    ctl.inbox = [{"id": "i1", "from": "alex", "size": 1, "created": now, "expires": now + 86400}]
    hp.refresh()
    assert "protected" in hp.card_protect.text.text()
    assert hp.card_send.text.text().startswith("Last: one.pdf to sam")
    assert hp.card_inbox.text.text() == "1 file waiting for you to accept."


def _entry_with_cloud(n=7):
    return VaultLedger.add_entry("board.pdf", "/nowhere/board.png", "2026-09-25 09:00:00", size=10,
                                 storage={"cloud": n, "inline": 12 - n}, owner="ux_user")


def test_removing_everywhere_deletes_the_cloud_pieces_first(signed_in, monkeypatch):
    import vault_service
    import ui.pages.vault as vpage
    ctl, win = signed_in
    e = _entry_with_cloud()
    monkeypatch.setattr(vpage, "confirm_remove", lambda *a: True)                     # "also delete from cloud"
    monkeypatch.setattr(vault_service, "delete_cloud_pieces", lambda entry, pem, dispatcher=None:
                        {"total": 7, "deleted": 7, "problems": []})
    vp = win.pages["vault"]
    win.go("vault")
    vp._remove(e["id"])
    assert _wait(lambda: vp._job is None)
    assert VaultLedger.get(e["id"]) is None and "deleted its 7 pieces" in vp.result._text.text()


def test_if_any_piece_stays_the_file_stays_too(signed_in, monkeypatch):
    import vault_service
    import ui.pages.vault as vpage
    ctl, win = signed_in
    e = _entry_with_cloud()
    monkeypatch.setattr(vpage, "confirm_remove", lambda *a: True)
    monkeypatch.setattr(vault_service, "delete_cloud_pieces", lambda entry, pem, dispatcher=None:
                        {"total": 7, "deleted": 5, "problems": ["“backup-b2” refused (AccessDenied)"]})
    vp = win.pages["vault"]
    vp._remove(e["id"])
    assert _wait(lambda: vp._job is None)
    assert VaultLedger.get(e["id"]) is not None                                        # still listed: nothing is lost
    msg = vp.result._text.text()
    assert "2 of 7 pieces could not be deleted" in msg and "stays in your vault" in msg


def test_remove_from_this_computer_only_leaves_the_cloud_alone(signed_in, monkeypatch):
    import vault_service
    import ui.pages.vault as vpage
    ctl, win = signed_in
    e = _entry_with_cloud()
    called = []
    monkeypatch.setattr(vpage, "confirm_remove", lambda *a: False)
    monkeypatch.setattr(vault_service, "delete_cloud_pieces", lambda *a, **k: called.append(1))
    win.pages["vault"]._remove(e["id"])
    assert VaultLedger.get(e["id"]) is None and not called


def test_someone_still_waiting_is_noticed(signed_in):
    ctl, win = signed_in
    e = _entry_with_cloud()
    ctl.remember_sent("w1", "board.pdf", e["id"])
    ctl.outbox = [{"id": "w1", "to": "sam", "size": 1, "created": int(time.time()), "state": "ready"}]
    assert ctl.pending_sends(e) == ["sam"]
    ctl.outbox[0]["state"] = "delivered"
    assert ctl.pending_sends(e) == []                                                  # picked up: their copy is safe


def test_remove_dialog_defaults(monkeypatch):
    from PyQt6.QtWidgets import QCheckBox, QDialog
    from ui.dialogs import confirm_remove
    seen = []

    def fake_exec(self):
        seen.append(self)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog, "exec", fake_exec)
    assert confirm_remove(None, "a.pdf", 7, []) is True                               # nobody waiting: ticked
    assert seen[-1].findChildren(QCheckBox)[0].isChecked()
    assert confirm_remove(None, "a.pdf", 7, ["sam"]) is False                          # sam waiting: unticked
    assert "sam has not picked it up" in " ".join(l.text() for l in seen[-1].findChildren(__import__("PyQt6.QtWidgets", fromlist=["QLabel"]).QLabel))
    assert confirm_remove(None, "a.pdf", 0, []) is False and not seen[-1].findChildren(QCheckBox)   # nothing in cloud
