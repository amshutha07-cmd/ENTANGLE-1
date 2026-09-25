"""Behaviour people notice: auto-lock never cuts off a transfer, quitting mid-transfer asks, the Sent list names files,
offline states explain themselves, long lists can be searched, and the window reopens where it was."""
import os
import time

import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtWidgets import QApplication, QLabel  # noqa: E402

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
    assert hp.card_inbox.text.text() == "1 file from alex, waiting for you to accept."


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


def _batch_setup(ctl, win, monkeypatch, fail=None):
    from PyQt6.QtCore import Qt
    from ui.controller import Job
    key = SecurityCore.load_identity_for_user("ux_user")["public_key"]
    for n in ("amy_b", "ben_b"):
        SecurityCore.pin_discovered_contact(n, key, "relay")
    e1 = VaultLedger.add_entry("one.pdf", "/x/1.png", "2026-09-25 09:00:00", size=100, storage={}, owner="ux_user")
    e2 = VaultLedger.add_entry("two.pdf", "/x/2.png", "2026-09-25 09:00:00", size=200, storage={}, owner="ux_user")
    sends = []

    def make_send_job(entry, person):
        def work(progress, cancelled):
            if fail and (entry["original_filename"], person) == fail:
                raise __import__("vault_service").VaultError("That person is not registered on this relay yet.")
            sends.append((entry["original_filename"], person))
            return {"id": f"t{len(sends)}", "to": person}
        return Job(work, "send")
    monkeypatch.setattr(ctl, "make_send_job", make_send_job)
    sp = win.pages["send"]
    win.go("send")
    sp._fill_files()
    for i in range(sp.files.count()):
        if sp.files.item(i).data(Qt.ItemDataRole.UserRole) in (e1["id"], e2["id"]):
            sp.files.item(i).setSelected(True)
    assert sp.next_btn.text() == "Continue with 2 files"
    sp._refresh_people = lambda: None
    sp._next()
    sp._fill_people()
    for i in range(sp.people.count()):
        if sp.people.item(i).data(Qt.ItemDataRole.UserRole) in ("amy_b", "ben_b"):
            sp.people.item(i).setSelected(True)
    assert sp.next_btn.text() == "Continue with 2 people"
    sp._next()
    return sp, sends


def test_several_files_to_several_people(signed_in, monkeypatch):
    ctl, win = signed_in
    sp, sends = _batch_setup(ctl, win, monkeypatch)
    assert sp.step == 2 and "one.pdf" in sp.rv_file.value.text() and "two.pdf" in sp.rv_file.value.text()
    assert sp.rv_file.key_label.text() == "Files" and sp.rv_size.value.text() == "300 B"
    assert sp.rv_pill.text() == "0 of 2 verified" and not sp.rv_fp.isVisible()       # no single fingerprint
    assert "(4 sends)" in sp.send_btn.text() and "amy_b" in sp.trust_banner._text.text()
    sp._send()
    assert _wait(lambda: sp._job is None and not sp._batch)
    assert sorted(sends) == sorted([(f, p) for f in ("one.pdf", "two.pdf") for p in ("amy_b", "ben_b")])
    assert sp.done.isVisible() and sp.done_title.text().startswith("Sent to amy_b, ben_b")
    assert ctl.sent_file_name("t1") in ("one.pdf", "two.pdf")                          # Sent list will name them


def test_one_failed_send_does_not_stop_the_batch(signed_in, monkeypatch):
    ctl, win = signed_in
    sp, sends = _batch_setup(ctl, win, monkeypatch, fail=("one.pdf", "ben_b"))
    sp._send()
    assert _wait(lambda: sp._job is None and not sp._batch)
    assert len(sends) == 3 and sp.done_title.text() == "Sent 3 of 4"
    assert "one.pdf to ben_b" in sp.done_text.text() and "not registered" in sp.done_text.text()


def test_cancel_stops_the_rest_of_the_batch(signed_in, monkeypatch):
    ctl, win = signed_in
    sp, sends = _batch_setup(ctl, win, monkeypatch)
    sp._batch = [("x", "y"), ("x", "z")]
    sp._batch_done = [("one.pdf", "amy_b")]
    sp._batch_cancelled()
    assert sp._batch == [] and sp.review.isVisible()


def test_dragging_files_over_the_window_says_what_dropping_them_does(tmp_path):
    from PyQt6.QtCore import QMimeData, QPoint, Qt, QUrl
    from PyQt6.QtGui import QDragEnterEvent, QDragLeaveEvent
    win = MainWindow(AppController())
    win.resize(1200, 800)
    win.show()
    files = []
    for name in ("Q3 board deck.pdf", "notes.txt"):
        f = tmp_path / name
        f.write_bytes(b"x")
        files.append(str(f))

    def drag(paths):
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
        win._mime = mime                                          # the event keeps only a pointer to it
        win.dragEnterEvent(QDragEnterEvent(QPoint(300, 300), Qt.DropAction.CopyAction, mime,
                                           Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        return win._drop_overlay

    win.root_stack.setCurrentIndex(0)                             # signed out: files are not taken, nothing shows
    assert not drag(files[:1]).isVisible()
    win.root_stack.setCurrentIndex(1)
    win.go("home")
    o = drag(files[:1])
    assert o.isVisible() and o._title == "Drop to protect" and "Q3 board deck.pdf" in o._detail
    assert o.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)     # can never take a click
    assert drag(files)._title == "Drop to protect 2 files"
    win.go("send")
    assert drag(files[:1])._title == "Drop to protect and send"
    win.dragLeaveEvent(QDragLeaveEvent())                         # dragged away again
    assert not o.isVisible()
    win.close()


def test_your_badge_in_the_sidebar_shows_the_connection_and_its_tooltip_says_where():
    from urllib.parse import urlparse
    import relay_config
    win = MainWindow(AppController())
    win.show()
    win._relay_state("online", "Connected")
    assert win.chip_avatar._presence == "success"
    host = urlparse(relay_config.get_relay_url()).hostname
    assert f"Connected to {host} since " in win.relay_pill.toolTip()
    since = win._relay_since
    win._relay_state("online", "Connected")                          # still online: "since" does not move
    assert win._relay_since == since
    win._relay_state("connecting", "Connecting to the relay…")
    assert win.chip_avatar._presence == "info" and "Connecting to" in win.chip_avatar.toolTip()
    assert not win.chip_avatar.grab().isNull()                        # the dot paints
    win.close()


def test_find_goes_to_the_search_box_on_this_page_or_to_your_files(signed_in):
    _ctl, win = signed_in
    SecurityCore.pin_discovered_contact("find_me", SecurityCore.load_identity_for_user("ux_user")["public_key"], "relay")
    win.go("contacts")
    pump()
    win._find()
    assert win.focusWidget() is win.pages["contacts"].search
    win.go("home")                                                 # no search here: your files' search instead
    win._find()
    assert win.content.currentWidget() is win.pages["vault"]


def test_home_shows_your_key_like_an_address_and_a_click_copies_all_of_it(signed_in):
    ctl, win = signed_in
    win.go("home")
    pump()
    chip, fp = win.pages["home"].key_chip, ctl.my_fingerprint()
    assert chip.isVisible() and chip.text() == f"{fp.split()[0]} … {fp.split()[-1]}"
    chip.click()
    assert QApplication.clipboard().text() == fp and chip.text() == "Copied"


def test_a_notice_stays_while_the_pointer_is_on_it():
    from PyQt6.QtCore import QEvent, QPointF
    from PyQt6.QtGui import QEnterEvent
    from PyQt6.QtWidgets import QWidget
    from ui.widgets import ToastHost
    root = QWidget()
    root.resize(900, 700)
    host = ToastHost(root)
    root.show()
    host.show_toast("Read me slowly", "info", ms=200)
    t = host._toasts()[0]
    QApplication.sendEvent(t, QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
    assert not t._clock.isActive()
    time.sleep(0.3)
    QApplication.processEvents()
    assert host._toasts() == [t]                                     # still there, well past its 200 ms
    QApplication.sendEvent(t, QEvent(QEvent.Type.Leave))
    assert t._clock.isActive() and t._clock.remainingTime() >= 1400   # then time to read the end of it
    root.close()


def test_recent_activity_leads_to_where_it_happened(signed_in):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    import activity
    from ui.pages.home import _ActivityRow
    _ctl, win = signed_in
    activity.add("sent", "Sent deck.pdf to sam", operator="ux_user")
    win.go("home")
    pump()
    rows = [r for r in win.pages["home"].findChildren(_ActivityRow) if r.isVisible()]
    assert rows and rows[0].toolTip() == "See what you sent"
    QTest.mouseClick(rows[0], Qt.MouseButton.LeftButton)
    assert win.content.currentWidget() is win.pages["inbox"]          # the Sent tab of the inbox


def test_a_status_pill_opens_settings_at_its_own_section(signed_in):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    _ctl, win = signed_in
    win.resize(1000, 700)                                            # small enough that the section is further down
    win.go("home")
    QTest.mouseClick(win.storage_pill, Qt.MouseButton.LeftButton)
    page = win.pages["settings"]
    assert win.content.currentWidget() is page
    top = page.storage_card.mapTo(page.viewport(), page.storage_card.rect().topLeft()).y()
    assert 0 <= top < page.viewport().height()                       # scrolled into view


def test_the_inbox_says_it_is_checking_until_the_relay_has_answered(signed_in):
    ctl, win = signed_in
    win.go("inbox")
    page = win.pages["inbox"]
    ctl._set_relay_state("connecting", "Connecting to the relay…")
    assert page.inbox_empty._title.text() == "Checking for files…"
    ctl._set_relay_state("online", "Connected")
    assert page.inbox_empty._title.text() == "Inbox is empty"


def test_sign_in_preselects_whoever_unlocked_last(signed_in):
    from ui.controller import pref
    _ctl, win = signed_in
    assert pref("last_operator") == "ux_user"                        # remembered when the session started
    if "ux_second" not in SecurityCore.list_registered_users():
        SecurityCore.establish_identity("ux_second", "another long passphrase for ux", auth="passphrase")
    login = win.auth.login
    for name in ("ux_second", "ux_user"):
        set_pref("last_operator", name)
        login.refresh()
        assert login._selected == name


def test_the_menu_bar_menu_says_what_is_going_on(signed_in):
    ctl, win = signed_in
    win._build_tray_menu()
    ctl.inbox = [{"id": "t1", "from": "sam"}, {"id": "t2", "from": "alex"}]
    ctl._set_relay_state("online", "Connected")
    win._tray_menu_opening()
    assert win._tray_status.text() == "ux_user · ● Online" and not win._tray_status.isEnabled()
    assert win._tray_inbox.isVisible() and win._tray_inbox.text() == "Open Inbox — 2 files waiting"
    win.root_stack.setCurrentIndex(0)                                # locked: nothing about the inbox shows
    win._tray_menu_opening()
    assert win._tray_status.text() == "Locked" and not win._tray_inbox.isVisible()
    ctl.inbox = []


def test_protected_files_can_be_ordered_and_the_choice_is_remembered(signed_in):
    from ui.controller import pref
    from ui.pages.vault import VaultRow
    _ctl, win = signed_in
    mine = [("srt-b.txt", 50, "2026-09-20"), ("srt-a.txt", 5, "2026-09-21"), ("srt-c.txt", 1, "2026-09-22")]
    for name, size, day in mine + [(f"srt-x{n}.bin", 2, "2026-09-19") for n in range(5)]:
        VaultLedger.add_entry(name, f"/x/{name}.png", f"{day} 10:00:00", size=size, storage={}, owner="ux_user")
    page = win.pages["vault"]
    win.go("vault")
    pump()

    def shown():                                                     # the order of this test's own files
        pump()
        names = {e["id"]: e["original_filename"] for e in VaultLedger.load(owner="ux_user")}
        return [n for n in (names[r.entry_id] for r in page.findChildren(VaultRow) if r.isVisible())
                if n in ("srt-a.txt", "srt-b.txt", "srt-c.txt")]
    assert page.sort.isVisible()
    page.sort.setCurrentIndex(page.sort.findData("name"))
    assert shown() == ["srt-a.txt", "srt-b.txt", "srt-c.txt"] and pref("vault_sort") == "name"
    page.sort.setCurrentIndex(page.sort.findData("size"))
    assert shown() == ["srt-b.txt", "srt-a.txt", "srt-c.txt"]
    page.sort.setCurrentIndex(page.sort.findData("newest"))
    assert shown() == ["srt-c.txt", "srt-a.txt", "srt-b.txt"]


def test_the_inbox_tile_says_who_the_files_are_from():
    from ui.pages.home import _names
    assert _names(["sam"]) == "sam" and _names(["alex", "sam"]) == "alex and sam"
    assert _names(["a", "b", "c"]) == "a, b and 1 other" and _names(["a", "b", "c", "d"]) == "a, b and 2 others"


def test_a_burst_of_notices_keeps_the_stack_short():
    from PyQt6.QtWidgets import QWidget
    from ui.widgets import ToastHost
    root = QWidget()
    root.resize(900, 700)
    host = ToastHost(root)
    root.show()
    for i in range(7):
        host.show_toast(f"Notice {i}", "info")
    pump()
    assert len(host._toasts()) == ToastHost.MAX                     # the newest four
    root.close()


def test_switching_the_theme_is_one_smooth_step(monkeypatch):
    from PyQt6.QtCore import QCoreApplication, QEvent, Qt
    from PyQt6.QtWidgets import QLabel
    from ui import motion, theme
    monkeypatch.delenv("ANSX_REDUCE_MOTION", raising=False)
    monkeypatch.setattr(motion, "_system", False)
    motion.set_reduced(False)
    win = MainWindow(AppController())
    win.show()
    root = win.centralWidget()

    def veils():
        return [c for c in root.children() if isinstance(c, QLabel) and c.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)]
    win.set_theme("light")
    assert theme.current() == "light" and len(veils()) == 1           # the old look, fading over the new one
    pump(0.6)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not veils()                                                # and then gone
    win.set_theme("dark")
    win.close()


def test_esc_empties_a_search_box(signed_in):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    _ctl, win = signed_in
    box = win.pages["contacts"].search
    box.setText("sam")
    QTest.keyClick(box, Qt.Key.Key_Escape)
    assert box.text() == ""


def test_the_automatic_theme_follows_the_computer(monkeypatch):
    from ui import theme
    win = MainWindow(AppController())
    win.show()
    monkeypatch.setattr(theme, "_system_scheme", lambda: "light")
    win.set_theme("auto")
    assert theme.choice() == "auto" and theme.current() == "light"
    monkeypatch.setattr(theme, "_system_scheme", lambda: "dark")
    win._system_scheme_changed()                                     # the computer switched, e.g. at sunset
    assert theme.choice() == "auto" and theme.current() == "dark"
    page = win.pages["settings"]
    page.on_show()
    assert page.theme_combo.currentData() == "auto"
    win.set_theme("dark")
    win.close()


def test_progress_shows_a_percentage_when_there_is_one():
    from ui.widgets import ProgressPanel
    p = ProgressPanel()
    p.start("Protecting deck.pdf")
    p.update_progress("Storing pieces…", 42)
    assert p.pct.text() == "42%" and not p.pct.isHidden()
    p.indeterminate("Checking…")
    assert p.pct.isHidden()                                          # no number to show
    p.update_progress("Storing pieces…", 60)
    assert p.pct.text() == "60%" and p.bar.maximum() == 100


def test_dropping_an_id_file_adds_the_person_instead_of_protecting_it(signed_in, tmp_path, monkeypatch):
    from PyQt6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
    from PyQt6.QtGui import QDragEnterEvent, QDropEvent
    import ui.pages.contacts as cp
    monkeypatch.setattr(cp, "info", lambda *a, **k: None)             # no modal "Contact added" in a test
    ctl, win = signed_in
    if "ux_friend" not in SecurityCore.list_registered_users():
        SecurityCore.establish_identity("ux_friend", "a long passphrase for a friend", auth="passphrase")
    path = SecurityCore.export_public_identity("ux_friend", str(tmp_path))
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(path)])
    win.dragEnterEvent(QDragEnterEvent(QPoint(200, 200), Qt.DropAction.CopyAction, mime,
                                       Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    assert win._drop_overlay._title == "Drop to add a person"
    win.dropEvent(QDropEvent(QPointF(200, 200), Qt.DropAction.CopyAction, mime,
                             Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    assert win.content.currentWidget() is win.pages["contacts"]
    assert "ux_friend" in [c["operator"] for c in ctl.contacts()]


def test_a_persons_details_say_when_you_last_sent_them_a_file(signed_in):
    ctl, win = signed_in
    SecurityCore.pin_discovered_contact("sam_contact", SecurityCore.load_identity_for_user("ux_user")["public_key"], "relay")
    ctl.outbox = [{"id": "s1", "to": "sam_contact", "size": 1, "created": int(time.time()) - 7200, "state": "delivered"}]
    win.go("contacts")
    page = win.pages["contacts"]
    page.select("sam_contact")
    assert page.d_src.text() == "Found on the relay · you last sent them a file 2 h ago"
    ctl.outbox = []


def test_a_notice_can_be_dismissed_without_doing_what_it_offers():
    from PyQt6.QtCore import QCoreApplication, QEvent, Qt
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QWidget
    from ui.widgets import ToastHost
    root = QWidget()
    root.resize(900, 700)
    host = ToastHost(root)
    root.show()
    opened = []
    host.show_toast("New file from sam. Click here to open your inbox.", "info", on_click=lambda: opened.append(True))
    t = host._toasts()[0]
    QTest.mouseClick(t.close_btn, Qt.MouseButton.LeftButton)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not opened and not host._toasts()
    root.close()


def test_arrow_keys_move_along_the_sidebar(signed_in):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    _ctl, win = signed_in
    win.nav["home"].setFocus()
    QTest.keyClick(win.nav["home"], Qt.Key.Key_Down)
    assert win.focusWidget() is win.nav["vault"]
    QTest.keyClick(win.nav["vault"], Qt.Key.Key_Return)                  # Enter opens it
    assert win.content.currentWidget() is win.pages["vault"]
    QTest.keyClick(win.nav["vault"], Qt.Key.Key_Up)
    assert win.focusWidget() is win.nav["home"]


def test_the_inbox_list_says_how_long_each_file_stays(signed_in):
    from ui.widgets import ListRow
    ctl, win = signed_in
    now = int(time.time())
    ctl.inbox = [{"id": "i9", "from": "alex", "size": 2048, "created": now, "expires": now + 6 * 86400 + 60}]
    win.go("inbox")
    page = win.pages["inbox"]
    page._fill_inbox()
    texts = [w.text() for r in page.inbox_list.findChildren(ListRow) for w in r.findChildren(QLabel)]
    assert any(t.endswith("· 6 days left") for t in texts)
    ctl.inbox = []


def test_progress_bars_move_only_while_there_is_work(monkeypatch):
    from ui import motion
    from ui.widgets import NeonBar
    monkeypatch.delenv("ANSX_REDUCE_MOTION", raising=False)
    monkeypatch.setattr(motion, "_system", False)
    motion.set_reduced(False)
    bar = NeonBar()
    bar.setRange(0, 100)
    bar.show()
    bar.setValue(40)
    running = bar._anim.State.Running
    assert bar._anim.state() == running                              # a glint runs along it
    bar.setValue(100)
    assert bar._anim.state() != running                              # done: still
    bar.setRange(0, 0)
    assert bar._anim.state() == running                              # no percentage yet: a segment slides
    assert not bar.grab().isNull()
    bar.hide()
    assert bar._anim.state() != running                              # hidden bars cost nothing


def test_a_busy_button_spins_and_comes_back_as_it_was():
    from ui.widgets import Button
    b = Button("Test", "secondary", "wifi")
    b.set_busy(True, "Testing…")
    assert b.busy() and not b.isEnabled() and b.text() == "Testing…"
    b.set_busy(False)
    assert not b.busy() and b.isEnabled() and b.text() == "Test"
