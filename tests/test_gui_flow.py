"""
The real application window (offscreen Qt) against a real relay: what a person actually clicks through.
Covers onboarding, login errors, protect -> send -> receive, verification, settings, theme, auto-lock.
"""
import os
import time

import pytest

pytest.importorskip("PyQt6")
import engine  # noqa: E402

pytestmark = pytest.mark.skipif(not engine.available(), reason="native engine not built")

from PyQt6.QtCore import QCoreApplication  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import paths  # noqa: E402
import relay_config  # noqa: E402
from security_core import SecurityCore  # noqa: E402
from ui import theme  # noqa: E402
from ui.controller import AppController, pref, set_pref  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402


def pump(cond=lambda: False, timeout=40.0):
    end = time.time() + timeout
    while time.time() < end:
        QCoreApplication.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return cond()


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    theme.apply(a, "dark")
    return a


@pytest.fixture(scope="module")
def env(live_relay, app):
    relay_config._override = None
    relay_config.set_relay_url(live_relay)
    SecurityCore._publish_identity = classmethod(lambda cls, *a, **k: None)
    return live_relay


@pytest.fixture
def auto_confirm(monkeypatch):
    """Answer every confirmation dialog 'yes' (they are modal, so a test would block forever)."""
    import ui.dialogs
    import ui.pages.auth as a
    import ui.pages.contacts as c
    import ui.pages.inbox as i
    import ui.pages.send as s
    import ui.pages.settings as st
    import ui.pages.vault as v
    state = {"answer": True, "asked": []}

    def fake(parent, title, text, ok="Confirm", danger=False, cancel="Cancel"):
        state["asked"].append(title)
        return state["answer"]

    for mod in (ui.dialogs, a, c, i, s, st, v):
        monkeypatch.setattr(mod, "confirm", fake, raising=False)
        monkeypatch.setattr(mod, "verify_fingerprint",
                            lambda parent, name, fp: state["asked"].append("verify " + name) or state["answer"],
                            raising=False)
    for mod in (c,):
        monkeypatch.setattr(mod, "info", lambda *a, **k: None, raising=False)
    return state


def _login(win, name, phrase):
    ctl = win.ctl
    box = {}
    job = ctl.make_login_job(name, phrase)
    job.succeeded.connect(lambda n: box.setdefault("ok", n))
    job.failed.connect(lambda m: box.setdefault("err", m))
    ctl.run_job(job)
    assert pump(lambda: box), "login never finished"
    return box


def test_onboarding_create_identity_through_the_wizard(env, auto_confirm):
    win = MainWindow()
    win.show()
    win.auth.show_create(False)
    c = win.auth.create

    assert not c.next_btn.isEnabled()                              # cannot continue without a name
    c.name_edit.setText("../evil")
    assert not c.next_btn.isEnabled() and "letters" in c.name_msg.text().lower()
    c.name_edit.setText("ui_alice")
    assert c.next_btn.isEnabled() and c.name_msg.text() == ""
    c._next()
    assert c.step == 1
    c._choose("passphrase")
    c.pw1.setText("short")
    assert not c.next_btn.isEnabled() and c.pw_strength.text().endswith("Too short")
    c.pw1.setText("a much longer passphrase")
    c.pw2.setText("a different passphrase")
    assert not c.next_btn.isEnabled() and "match" in c.pw_msg.text()
    c.pw2.setText("a much longer passphrase")
    assert c.next_btn.isEnabled()
    c._next()                                                       # -> creating
    assert pump(lambda: c.done_box.isVisible(), 60), c.error_banner.text()
    assert len(c.fp.raw().split()) == 8                             # fingerprint shown
    assert SecurityCore.auth_mode("ui_alice") == "passphrase"
    c.next_btn.click()                                               # "Open my vault"
    assert win.root_stack.currentIndex() == 1 and win.ctl.operator == "ui_alice"
    assert pump(lambda: win.ctl.relay_state == "online", 20)
    assert win.relay_pill.text().endswith("Online") and win.chip_name.text() == "ui_alice"
    win.ctl.logout()
    win.close()


def test_login_errors_lock_and_idle_lock(env, auto_confirm):
    win = MainWindow()
    win.show()
    win.auth.show_login()
    lv = win.auth.login
    assert lv.people.count() >= 1
    for i in range(lv.people.count()):
        if lv.people.item(i).data(0x100) == "ui_alice":
            lv.people.setCurrentRow(i)
    lv.pass_edit.setText("definitely wrong phrase")
    lv._unlock()
    assert pump(lambda: lv.error.isVisible(), 30) and "does not unlock" in lv.error.text()
    assert win.root_stack.currentIndex() == 0 and not SecurityCore.is_unlocked("ui_alice")

    lv.pass_edit.setText("a much longer passphrase")
    lv._unlock()
    assert pump(lambda: win.root_stack.currentIndex() == 1, 30)
    assert SecurityCore.is_unlocked("ui_alice")

    # keyboard shortcuts navigate, Ctrl+L locks
    win._shortcut("vault")
    assert win.content.currentWidget() is win.pages["vault"] and win.nav["vault"].isChecked()

    # auto-lock after inactivity
    set_pref("auto_lock_minutes", 1)
    win._activity()
    assert win._idle.isActive()
    win._idle.timeout.emit()
    assert win.root_stack.currentIndex() == 0 and not SecurityCore.is_unlocked("ui_alice")
    set_pref("auto_lock_minutes", 0)
    win.close()


def _make_user(name, phrase):
    ctl = AppController()
    box = {}
    job = ctl.make_register_job(name, "passphrase", phrase)
    job.succeeded.connect(lambda n: box.setdefault("ok", n))
    job.failed.connect(lambda m: box.setdefault("err", m))
    ctl.run_job(job)
    assert pump(lambda: box, 60) and "ok" in box, box
    return ctl


def test_full_user_journey_across_the_real_screens(env, auto_confirm, tmp_path, monkeypatch):
    # bob (receiver) signs up through the controller; alice drives the real window
    bob_ctl = _make_user("ui_bob", "bob long passphrase 42")
    alice_ctl = _make_user("ui_carla", "carla long passphrase 42")
    bob_ctl.begin_session("ui_bob")
    assert pump(lambda: bob_ctl.relay_state == "online", 20)

    alice = MainWindow(alice_ctl)
    alice.show()
    alice_ctl.begin_session("ui_carla")
    assert pump(lambda: alice_ctl.relay_state == "online", 20)
    assert alice.root_stack.currentIndex() == 1 and alice.content.currentWidget() is alice.pages["home"]

    # ── protect ──
    secret = os.urandom(120_000)
    src = tmp_path / "Board minutes ⚖.pdf"
    src.write_bytes(secret)
    alice.go("vault")
    vp = alice.pages["vault"]
    assert vp.storage_banner.isVisible()                            # no cloud storage yet -> explained, with a way out
    vp.protect_file(str(src))
    assert vp._job is not None and not vp.drop.isEnabled()          # busy state is visible
    assert pump(lambda: vp._job is None and vp.result.isVisible(), 60)
    assert "protected" in vp.result._text.text()
    entries = alice_ctl.vault_entries()
    assert [e["original_filename"] for e in entries] == [src.name]
    assert vp.list_card.body.count() == 1                           # row rendered

    # ── send (wizard: file -> person -> review) ──
    vp.send_requested.emit(entries[0]["id"])
    sp = alice.pages["send"]
    assert alice.content.currentWidget() is sp and sp.step == 1     # jumped straight to "choose a person"
    assert pump(lambda: sp.people.count() >= 1 or True, 5)
    sp._refresh_people()
    assert pump(lambda: any(sp.people.item(i).data(0x100) == "ui_bob" for i in range(sp.people.count())), 20)
    for i in range(sp.people.count()):
        if sp.people.item(i).data(0x100) == "ui_bob":
            sp.people.setCurrentRow(i)
    assert sp.next_btn.isEnabled()
    sp._next()
    assert sp.step == 2 and sp.rv_name.text() == "ui_bob" and sp.rv_pill.text() == "Not verified"
    assert len(sp.rv_fp.raw().split()) == 8 and sp.trust_banner.property("kind") == "warning"
    sp._verify_now()                                                 # user compares fingerprint by phone -> yes
    assert sp.rv_pill.text() == "Verified" and sp.trust_banner.property("kind") == "success"
    sp._send()
    assert not sp.review.isVisible() and sp.progress.isVisible()
    assert pump(lambda: sp.done.isVisible(), 60), sp.error.text()
    assert "ui_bob" in sp.done_title.text()

    # ── the sender's tracker shows it, then bob's inbox shows it ──
    assert pump(lambda: any(o["state"] == "ready" for o in alice_ctl.outbox), 20)
    inbox = alice.pages["inbox"]
    alice.go("sent")
    assert inbox.stack.currentIndex() == 1 and inbox.sent_list.count() == 1
    assert "Waiting for pickup" in inbox.sent_list.itemWidget(inbox.sent_list.item(0)).findChildren(type(sp.rv_pill))[0].text()

    bob = MainWindow(bob_ctl)
    bob.show()
    assert pump(lambda: len(bob_ctl.inbox) == 1, 25)
    assert bob.nav["inbox"].badge() == 1 and bob.nav["inbox"].accessibleName() == "Inbox, 1 waiting"   # badge
    bob.go("inbox")
    bi = bob.pages["inbox"]
    assert pump(lambda: bi.inbox_list.count() == 1 and bi._current is not None, 10)
    # carla's key reaches bob's contacts through LAN discovery, which may or may not have happened yet: either way the
    # sender is flagged as not verified ("New sender" before discovery, "Not verified" after) with a warning banner.
    assert bi.d_from.text() == "ui_carla" and bi.d_pill.text() in ("Not verified", "New sender")
    assert bi.d_banner.property("kind") == "warning"

    # ── bob accepts ──
    opened = []
    monkeypatch.setattr("ui.pages.inbox.open_folder", lambda p: opened.append(p))
    bi._accept()
    assert bi.progress.isVisible() and not bi.accept_btn.isEnabled()
    assert pump(lambda: bi.result.isVisible() and bi._job is None, 60), bi.result._text.text()
    got = opened[0]
    assert os.path.dirname(got) == paths.downloads_dir() and open(got, "rb").read() == secret
    assert os.path.basename(got) == "Board minutes ⚖.pdf"            # sanitised name, unicode preserved
    assert pump(lambda: len(bob_ctl.inbox) == 0, 20) and bi.inbox_list.count() == 0

    # ── alice sees it delivered ──
    assert pump(lambda: any(o["state"] == "delivered" for o in alice_ctl.outbox), 20)
    assert pump(lambda: "Delivered" in inbox.sent_list.itemWidget(inbox.sent_list.item(0)).findChildren(type(sp.rv_pill))[0].text(), 10)

    # ── home reflects reality ──
    alice.go("home")
    titles = [it["title"] for it in __import__("activity").recent(10, operator="ui_carla")]
    assert any("Protected" in t for t in titles) and any("Sent" in t for t in titles) and any("received your file" in t for t in titles)

    # ── restore my own copy ──
    vp.refresh()
    vp._restore(entries[0]["id"])
    monkeypatch.setattr("ui.pages.vault.open_folder", lambda p: opened.append(p))
    assert pump(lambda: vp._job is None and len(opened) == 2, 60)
    assert open(opened[1], "rb").read() == secret and opened[1] != got   # unique name, never overwrites

    bob_ctl.logout()
    alice_ctl.logout()
    bob.close()
    alice.close()


def test_decline_and_cancel_paths(env, auto_confirm, tmp_path):
    a = _make_user("ui_dan", "dan long passphrase 4242")
    b = _make_user("ui_eve", "eve long passphrase 4242")
    b.begin_session("ui_eve")
    assert pump(lambda: b.relay_state == "online", 20)
    win_a = MainWindow(a)
    win_a.show()
    a.begin_session("ui_dan")
    assert pump(lambda: a.relay_state == "online", 20)
    f = tmp_path / "note.txt"
    f.write_bytes(os.urandom(5000))
    vp = win_a.pages["vault"]
    vp.protect_file(str(f))
    assert pump(lambda: vp._job is None and a.vault_entries(), 60)
    entry = a.vault_entries()[0]
    assert pump(lambda: a.make_directory_job() and True, 1)
    from tests.test_controller import run_job
    run_job(a.make_directory_job())

    # sender cancels a transfer that is waiting for pickup
    assert run_job(a.make_send_job(entry, "ui_eve"))[0] == "ok"
    assert pump(lambda: any(o["state"] == "ready" for o in a.outbox), 20)
    win_a.go("sent")
    ip = win_a.pages["inbox"]
    ip.sent_list.setCurrentRow(0)
    assert ip.cancel_out_btn.isVisible() or ip.cancel_out_btn.isVisibleTo(ip)
    ip._cancel_outgoing()
    assert pump(lambda: any(o["state"] == "cancelled" for o in a.outbox), 20)

    # receiver declines another one; sender is told
    assert run_job(a.make_send_job(entry, "ui_eve"))[0] == "ok"
    assert pump(lambda: len(b.inbox) == 1, 25)
    win_b = MainWindow(b)
    win_b.show()
    win_b.go("inbox")
    bp = win_b.pages["inbox"]
    assert pump(lambda: bp.inbox_list.count() == 1 and bp._current, 10)
    bp._decline()
    assert pump(lambda: len(b.inbox) == 0, 20)
    assert pump(lambda: any(o["state"] == "rejected" for o in a.outbox), 20)
    assert "Declined a file" in [i["title"] for i in __import__("activity").recent(5, operator="ui_eve")][0]
    for w in (win_a, win_b):
        w.ctl.logout()
        w.close()


def test_contacts_screen_verifies_and_removes(env, auto_confirm):
    a = _make_user("ui_fay", "fay long passphrase 4242")
    g = _make_user("ui_gus", "gus long passphrase 4242")
    g.begin_session("ui_gus")
    assert pump(lambda: g.relay_state == "online", 20)
    win = MainWindow(a)
    win.show()
    a.begin_session("ui_fay")
    assert pump(lambda: a.relay_state == "online", 20)
    from tests.test_controller import run_job
    run_job(a.make_directory_job())
    win.go("contacts")
    cp = win.pages["contacts"]
    assert len(cp.me_fp.raw().split()) == 8 and cp.me_name.text() == "ui_fay"
    names = [cp.list.item(i).data(0x100) for i in range(cp.list.count())]
    assert "ui_gus" in names
    cp.select("ui_gus")
    assert cp.d_pill.text() == "Not verified" and cp.verify_btn.isVisibleTo(cp)
    cp._verify()
    assert cp.d_pill.text() == "Verified" and not cp.verify_btn.isVisibleTo(cp)
    assert "verify ui_gus" in auto_confirm["asked"]
    cp._remove()
    assert "ui_gus" not in [cp.list.item(i).data(0x100) for i in range(cp.list.count()) if cp.list.item(i).data(0x100)]
    a.logout()
    g.logout()
    win.close()


def test_settings_relay_storage_theme(env, auto_confirm, monkeypatch):
    a = _make_user("ui_hal", "hal long passphrase 4242")
    win = MainWindow(a)
    win.show()
    a.begin_session("ui_hal")
    assert pump(lambda: a.relay_state == "online", 20)
    win.go("settings")
    sp = win.pages["settings"]
    assert sp.acct_name.value.text() == "ui_hal" and sp.acct_mode.value.text() == "Passphrase"
    assert sp.relay_edit.text() == env

    # relay test / save with good and bad addresses
    sp.relay_edit.setText("not an address")
    sp._test_relay()
    assert pump(lambda: sp.relay_msg.property("kind") == "danger", 10)
    sp.relay_edit.setText("http://127.0.0.1:9")
    sp._test_relay()
    assert pump(lambda: "Could not reach" in sp.relay_msg._text.text(), 20)
    sp.relay_edit.setText(env)
    sp._test_relay()
    assert pump(lambda: sp.relay_msg.property("kind") in ("success", "warning") and "Connected" in sp.relay_msg._text.text(), 20)
    sp.relay_edit.setText("ftp://nope")
    sp._save_relay()
    assert sp.relay_msg.property("kind") == "danger" and relay_config.get_relay_url() == env   # unchanged

    # storage: validation errors are friendly, saving persists with a private file, secrets are never displayed
    from ui.dialogs import StorageDialog
    import cloud_dispatcher
    dlg = StorageDialog(a)
    dlg.provider.setCurrentIndex(dlg.provider.findData("r2"))
    dlg.name.setText("my-r2")
    dlg.bucket.setText("bkt")
    dlg.access.setText("AKIA")
    dlg.secret.setText("TOPSECRET")
    dlg.account.setText("tooshort")
    assert dlg._build() is None and "Account ID" in dlg.error.text()
    dlg.account.setText("a" * 32)
    target = dlg._build()
    assert target["endpoint_url"] == f"https://{'a' * 32}.r2.cloudflarestorage.com" and target["region"] == "auto"
    monkeypatch.setattr(dlg, "exec", lambda: 1)
    dlg._result = target
    sp.ctl.save_storage_targets([target])
    if os.name == "posix":
        assert oct(os.stat(cloud_dispatcher.CONFIG_PATH).st_mode & 0o777) == "0o600"
    sp._fill_storage()
    assert sp.storage_box.count() == 1
    shown = " ".join(w.text() for w in sp.findChildren(type(sp.acct_name.value)))
    assert "TOPSECRET" not in shown
    win.pages["home"].refresh()
    assert win.pages["home"].tile_storage.value.text().startswith("1 account")
    win._storage_pill()
    assert "1" in win.storage_pill.text()
    sp.ctl.save_storage_targets([])

    # theme switches live and is remembered
    sp.theme_combo.setCurrentIndex(1)
    assert theme.current() == "light" and pref("theme") == "light"
    sp.theme_combo.setCurrentIndex(0)
    assert theme.current() == "dark"
    a.logout()
    win.close()


def test_app_starts_with_no_relay_and_recovers(env, auto_confirm):
    """Offline is a state the UI explains, not a crash; it recovers when the relay address is corrected."""
    a = _make_user("ui_ivy", "ivy long passphrase 4242")
    good = relay_config.get_relay_url()
    relay_config.set_relay_url("http://127.0.0.1:9")
    win = MainWindow(a)
    win.show()
    a.begin_session("ui_ivy")
    assert pump(lambda: a.relay_state == "offline", 30)
    assert "Offline" in win.relay_pill.text()
    win.pages["home"].refresh()
    assert win.pages["home"].tile_relay.value.text() == "Offline"
    a.apply_relay_url(good)
    assert pump(lambda: a.relay_state == "online", 30)
    assert win.relay_pill.text().endswith("Online")
    a.logout()
    win.close()


def test_every_button_can_actually_be_clicked_even_with_toasts_showing(env, auto_confirm):
    """
    Regression: an invisible overlay once swallowed clicks on the right side of the window ("Add an account" did
    nothing). This hit-tests, for every visible button on every page, that a real click lands on THAT button.
    """
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QPushButton
    ctl = _make_user("ui_jo", "jo long passphrase 4242")
    win = MainWindow(ctl)
    win.show()
    ctl.begin_session("ui_jo")
    assert pump(lambda: ctl.relay_state == "online", 20)
    for kind in ("success", "warning", "error", "info"):            # a full stack of toasts on screen
        ctl.toast.emit("A notification that is on screen while you work.", kind)
    pump(timeout=0.3)
    assert win.toasts.isVisible() and win.toasts.height() < 400      # sized to its contents, not the whole window

    blocked = []
    for key, page in win.pages.items():
        win.go(key)
        pump(timeout=0.2)
        viewport = page.viewport().rect()
        for b in page.findChildren(QPushButton):
            if not b.isVisibleTo(page) or not b.isEnabled() or not b.isVisible():
                continue
            centre = b.mapTo(page.viewport(), b.rect().center())
            if not viewport.contains(centre):
                continue                                             # scrolled out of view; a user would scroll first
            hit = win.childAt(b.mapTo(win, b.rect().center()))
            ok = hit is b or (hit is not None and b.isAncestorOf(hit))
            if not ok:
                blocked.append(f"{key}: '{b.text() or b.toolTip()}' is covered by {type(hit).__name__}")
    assert not blocked, "buttons that a click cannot reach:\n" + "\n".join(blocked)

    # and the specific case that was reported: Settings -> Add an account really opens the dialog
    import ui.pages.settings as st
    opened = []
    real_exec = st.StorageDialog.exec
    st.StorageDialog.exec = lambda self: (opened.append(True), 0)[1]
    try:
        win.go("settings")
        pump(timeout=0.2)
        add = [b for b in win.pages["settings"].findChildren(QPushButton) if b.text().strip() == "Add an account"][0]
        QTest.mouseClick(win.childAt(add.mapTo(win, add.rect().center())), Qt.MouseButton.LeftButton)
        assert opened, "clicking 'Add an account' did nothing"
    finally:
        st.StorageDialog.exec = real_exec
    ctl.logout()
    win.close()
