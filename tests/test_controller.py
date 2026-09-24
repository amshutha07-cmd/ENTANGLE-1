"""The whole product flow through AppController (no widgets): register, protect, send, receive, restore."""
import os
import time

import pytest

pytest.importorskip("PyQt6")
import engine  # noqa: E402

pytestmark = pytest.mark.skipif(not engine.available(), reason="native engine not built")

from PyQt6.QtCore import QCoreApplication  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import relay_config  # noqa: E402
from security_core import SecurityCore  # noqa: E402
from ui.controller import AppController, Job  # noqa: E402


def pump(cond, timeout=40.0):
    end = time.time() + timeout
    while time.time() < end:
        QCoreApplication.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def run_job(job: Job, timeout=60.0):
    """Run a job on its real thread and wait for it; returns ("ok"|"fail"|"cancel", value)."""
    box = {}
    job.succeeded.connect(lambda v: box.setdefault("r", ("ok", v)))
    job.failed.connect(lambda m: box.setdefault("r", ("fail", m)))
    job.cancelled.connect(lambda: box.setdefault("r", ("cancel", None)))
    job.start()
    assert pump(lambda: "r" in box, timeout), "job never finished"
    job.wait()
    return box["r"]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def env(live_relay, app):
    relay_config._override = None
    relay_config.set_relay_url(live_relay)
    SecurityCore._publish_identity = classmethod(lambda cls, *a, **k: None)
    return live_relay


def test_full_journey_through_the_controller(env, tmp_path):
    ctl_a, ctl_b = AppController(), AppController()
    phrases = {"ctl_alice": "alice pass phrase 2026", "ctl_bob": "bob pass phrase 2026x"}

    for ctl, name in ((ctl_a, "ctl_alice"), (ctl_b, "ctl_bob")):
        assert ctl.auth_mode(name) == "nfc"                                  # unknown -> default
        kind, val = run_job(ctl.make_register_job(name, "passphrase", phrases[name]))
        assert (kind, val) == ("ok", name)
        assert ctl.auth_mode(name) == "passphrase"
        SecurityCore.lock(name)                                              # prove login really unlocks
        assert not SecurityCore.is_unlocked(name)
        kind, _ = run_job(ctl.make_login_job(name, "wrong pass phrase!!"))
        assert kind == "fail" and not SecurityCore.is_unlocked(name)
        assert run_job(ctl.make_login_job(name, phrases[name]))[0] == "ok"
    ctl_a.begin_session("ctl_alice")
    assert pump(lambda: ctl_a.relay_state == "online", 20), ctl_a.relay_message

    # alice protects a file
    src = tmp_path / "plans.bin"
    src.write_bytes(os.urandom(90_000))
    job = ctl_a.make_protect_job(str(src))
    kind, result = run_job(job)
    assert kind == "ok"
    ctl_a.after_protect(result)
    assert [e["original_filename"] for e in ctl_a.vault_entries()][0] == "plans.bin"

    # bob must exist on the relay + alice must know him
    SecurityCore.verify_login("ctl_bob", phrases["ctl_bob"])
    ctl_b.begin_session("ctl_bob")
    assert pump(lambda: ctl_b.relay_state == "online", 20)
    assert run_job(ctl_a.make_directory_job())[0] == "ok"
    assert "ctl_bob" in [c["operator"] for c in ctl_a.contacts()]
    assert ctl_a.trust("ctl_bob") == "unverified"
    assert ctl_a.verify_contact("ctl_bob") and ctl_a.trust("ctl_bob") == "verified"

    # alice sends; progress is monotonic and ends done
    seen = []
    entry = ctl_a.vault_entries()[0]
    sj = ctl_a.make_send_job(entry, "ctl_bob")
    sj.progress.connect(lambda label, pct: seen.append(pct))
    kind, sent = run_job(sj)
    assert kind == "ok" and sent["to"] == "ctl_bob" and seen == sorted(seen) and seen[-1] >= 95
    ctl_a.after_send(entry, "ctl_bob")
    assert pump(lambda: any(o["state"] == "ready" for o in ctl_a.outbox), 25)

    # bob is notified, accepts, and gets the identical file
    assert pump(lambda: len(ctl_b.inbox) == 1, 25)
    item = ctl_b.inbox[0]
    assert item["from"] == "ctl_alice" and ctl_b.trust("ctl_alice", item["sender_fingerprint"]) in ("unverified", "unknown")
    out = tmp_path / "received.bin"
    kind, info = run_job(ctl_b.make_accept_job(item, str(out)))
    assert kind == "ok" and info["from"] == "ctl_alice" and out.read_bytes() == src.read_bytes()
    ctl_b.after_receive(info)
    assert pump(lambda: len(ctl_b.inbox) == 0, 25)
    assert pump(lambda: any(o["state"] == "delivered" for o in ctl_a.outbox), 25)

    # alice restores her own copy
    mine = tmp_path / "mine.bin"
    assert run_job(ctl_a.make_restore_job(entry, str(mine)))[0] == "ok" and mine.read_bytes() == src.read_bytes()

    # decline path
    src2 = tmp_path / "second.bin"; src2.write_bytes(os.urandom(3000))
    kind, res2 = run_job(ctl_a.make_protect_job(str(src2)))
    run_job(ctl_a.make_send_job(res2.entry, "ctl_bob"))
    assert pump(lambda: len(ctl_b.inbox) == 1, 25)
    ctl_b.decline(ctl_b.inbox[0])
    assert pump(lambda: any(o["state"] == "rejected" for o in ctl_a.outbox), 25)

    ctl_a.logout(); ctl_b.logout()
    assert not SecurityCore.is_unlocked("ctl_alice") and ctl_a.relay is None


def test_error_messages_are_human(env, tmp_path):
    ctl = AppController()
    kind, msg = run_job(ctl.make_protect_job(str(tmp_path / "missing.bin")))
    assert kind == "fail" and "locked" in msg or "no longer exists" in msg
    assert run_job(ctl.make_register_job("../bad", "passphrase", "x" * 20))[0] == "fail"
    assert run_job(ctl.make_register_job("shorty", "passphrase", "short"))[0] == "fail"
    assert run_job(ctl.make_relay_test_job("not a url"))[0] == "fail"
    assert run_job(ctl.make_relay_test_job("http://127.0.0.1:9"))[0] == "fail"
    kind, val = run_job(ctl.make_relay_test_job(relay_config.get_relay_url()))
    assert kind == "ok" and val["limits"]["max_transfer_bytes"] > 0


def test_cancel_send_leaves_nothing_behind(env, tmp_path):
    ctl = AppController()
    name, phrase = "ctl_cancel", "cancel pass phrase 2026"
    assert run_job(ctl.make_register_job(name, "passphrase", phrase))[0] == "ok"
    ctl.begin_session(name)
    assert pump(lambda: ctl.relay_state == "online", 20)
    src = tmp_path / "big.bin"; src.write_bytes(os.urandom(400_000))
    _, res = run_job(ctl.make_protect_job(str(src)))
    peer, peer_ctl = "ctl_peer", AppController()
    assert run_job(peer_ctl.make_register_job(peer, "passphrase", "peer pass phrase 2026x"))[0] == "ok"
    peer_ctl.begin_session(peer)                                   # registers the peer on the relay
    assert pump(lambda: peer_ctl.relay_state == "online", 20)
    assert run_job(ctl.make_directory_job())[0] == "ok"
    outbox = os.path.join(os.environ["ANSX_VAULT_HOME"], "outbox")
    before = {f for f in os.listdir(outbox) if f.startswith("send_")}
    job = ctl.make_send_job(res.entry, peer)
    job.progress.connect(lambda label, pct: job.cancel() if pct > 12 else None)
    kind, _ = run_job(job)
    assert kind == "cancel"
    assert {f for f in os.listdir(outbox) if f.startswith("send_")} == before      # nothing new left behind
    assert ctl.relay.outbox()[0]["state"] == "cancelled"
    ctl.logout()
    peer_ctl.logout()
