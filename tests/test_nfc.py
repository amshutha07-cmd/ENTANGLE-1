"""Card-reader robustness: no false readers, and a vanished USB device is a clear message, not a crash."""
from types import SimpleNamespace as P

import pytest

serial = pytest.importorskip("serial")
import serial.tools.list_ports as lp  # noqa: E402

import nfc_serial  # noqa: E402


def _ports(monkeypatch, ports):
    monkeypatch.setattr(lp, "comports", lambda: ports)


def test_only_real_usb_readers_are_detected(monkeypatch):
    bt = P(device="/dev/cu.Bluetooth-Incoming-Port", description="n/a", manufacturer=None, vid=None)
    dbg = P(device="/dev/cu.debug-console", description="n/a", manufacturer=None, vid=None)
    buds = P(device="/dev/cu.SomeBuds", description="n/a", manufacturer=None, vid=None)
    uno = P(device="/dev/cu.usbmodem1101", description="Arduino Uno", manufacturer="Arduino", vid=0x2341)
    ch340 = P(device="COM3", description="USB-SERIAL CH340 (COM3)", manufacturer="wch.cn", vid=0x1A86)
    _ports(monkeypatch, [bt, dbg, buds])
    assert nfc_serial.find_reader_port() is None                    # the false positive that once said "Reader connected"
    _ports(monkeypatch, [bt, dbg, uno])
    assert nfc_serial.find_reader_port() == "/dev/cu.usbmodem1101"
    _ports(monkeypatch, [ch340])
    assert nfc_serial.find_reader_port() == "COM3"


class DeadPort:
    """A serial port whose USB device was unplugged: every call raises errno 6, exactly like macOS does."""
    is_open = True

    @property
    def in_waiting(self):
        raise OSError(6, "Device not configured")

    def reset_input_buffer(self):
        raise OSError(6, "Device not configured")

    def write(self, _):
        raise OSError(6, "Device not configured")

    def close(self):
        self.is_open = False


def _bridge_with(ser):
    b = nfc_serial.HardwareNFCBridge.__new__(nfc_serial.HardwareNFCBridge)
    b.ser, b.port, b.baud_rate = ser, "/dev/cu.usbmodem1101", 115200
    return b


def test_unplugged_reader_is_reported_as_disconnected():
    b = _bridge_with(DeadPort())
    assert b.is_connected() is False                                # not "open" just because the object exists
    assert b.ser is None                                            # and it was forgotten so the next try reconnects


def test_write_and_read_on_a_vanished_reader_raise_a_friendly_error():
    class HalfDead(DeadPort):
        in_waiting = 0                                              # looks alive until we actually send something

    for op in (lambda b: b.write_payload_to_tag("ABCDEFGHIJKLMNOP"), lambda b: b.read_payload_from_tag()):
        b = _bridge_with(HalfDead())
        with pytest.raises(nfc_serial.ReaderError) as e:
            op(b)
        assert "lost its connection" in str(e.value) and "Device not configured" not in str(e.value)
        assert b.ser is None and b.is_connected() is False


def test_controller_turns_a_lost_reader_into_a_screen_message(monkeypatch):
    pytest.importorskip("PyQt6")
    from ui.controller import AppController

    class Boom:
        def is_connected(self):
            return True

        def write_payload_to_tag(self, *a, **k):
            raise nfc_serial.ReaderError(nfc_serial.READER_LOST)

        def read_payload_from_tag(self, *a, **k):
            raise nfc_serial.ReaderError(nfc_serial.READER_LOST)

    monkeypatch.setattr(nfc_serial, "get_shared_bridge", lambda: Boom())
    ctl = AppController()
    seen = []
    job = ctl.make_register_job("card_user", "nfc")
    job.failed.connect(seen.append)
    job.run()                                                       # synchronous
    assert seen and "lost its connection" in seen[0] and "Something went wrong" not in seen[0]


class FakeReader:
    """A reader with one card on it. `card` is what READ returns ("" = blank card, None = unreadable)."""

    def __init__(self, card):
        self.card, self.writes = card, []

    def is_connected(self):
        return True

    def read_payload_from_tag(self, *a, **k):
        return self.card

    def write_payload_to_tag(self, payload, *a, **k):
        self.writes.append(payload)
        self.card = payload
        return True


def _register(monkeypatch, name, card):
    pytest.importorskip("PyQt6")
    from security_core import SecurityCore
    from ui.controller import AppController
    monkeypatch.setattr(SecurityCore, "_publish_identity", classmethod(lambda cls, *a, **k: None))
    reader = FakeReader(card)
    monkeypatch.setattr(nfc_serial, "get_shared_bridge", lambda: reader)
    job, out = AppController().make_register_job(name, "nfc"), {}
    job.failed.connect(lambda m: out.setdefault("failed", m))
    job.succeeded.connect(lambda n: out.setdefault("ok", n))
    job.run()                                                       # synchronous
    return reader, out


def test_a_card_that_already_unlocks_an_identity_is_never_overwritten(monkeypatch):
    from security_core import SecurityCore
    monkeypatch.setattr(SecurityCore, "_publish_identity", classmethod(lambda cls, *a, **k: None))
    SecurityCore.establish_identity("guard_owner", "CardSecretABCDEF", auth="nfc")
    reader, out = _register(monkeypatch, "guard_new", "CardSecretABCDEF")
    assert "already unlocks 'guard_owner'" in out["failed"]
    assert reader.writes == [] and reader.card == "CardSecretABCDEF"     # the owner's key is still on the card
    assert "guard_new" not in SecurityCore.list_registered_users()
    assert SecurityCore.verify_login("guard_owner", "CardSecretABCDEF")


def test_a_passphrase_that_happens_to_match_does_not_block_a_card():
    from security_core import SecurityCore
    SecurityCore._publish_identity = classmethod(lambda cls, *a, **k: None)
    SecurityCore.establish_identity("guard_pass", "SameTextAsACard1", auth="passphrase")
    assert "guard_pass" not in SecurityCore.identities_unlocked_by("SameTextAsACard1")


def test_blank_and_unknown_cards_can_be_written_but_unreadable_ones_are_not(monkeypatch):
    reader, out = _register(monkeypatch, "guard_blank", "")
    assert out.get("ok") == "guard_blank" and len(reader.writes) == 1
    reader, out = _register(monkeypatch, "guard_foreign", "from-another-app")
    assert out.get("ok") == "guard_foreign" and len(reader.writes) == 1
    reader, out = _register(monkeypatch, "guard_none", None)
    assert "Could not read the card" in out["failed"] and reader.writes == []


# ── firmware protocol 2: per-card keys, same-card writes, time-limited waits ─────────────────────
FACTORY = "FFFFFFFFFFFF"


class Card:
    def __init__(self, uid, data=b"", key=FACTORY):
        self.uid, self.key, self.data = uid, key, data.ljust(16, b"\0")


class Firmware2:
    """Serial port of an Arduino running arduino_nfc.ino v2, with `card` on the reader (None = no card)."""
    is_open = True

    def __init__(self, card=None, after_read=None):
        self.card, self.after_read, self.out, self.sent = card, after_read, [], []

    @property
    def in_waiting(self):
        return len(self.out)

    def reset_input_buffer(self):
        self.out.clear()

    def readline(self):
        return (self.out.pop(0) + "\n").encode()

    def close(self):
        self.is_open = False

    def _open(self, uid, key):                       # openSector() in the sketch
        c = self.card
        if c is None:
            return None, "ERROR: No card"
        if c.uid != uid:
            return None, "ERROR: Wrong card"
        if key == c.key:
            return "C", None
        if c.key == FACTORY:
            return "D", None
        return None, "ERROR: Auth Failed"

    def write(self, raw):
        cmd = raw.decode().strip()
        self.sent.append(cmd)
        if cmd == "VERSION":
            self.out.append("ANSX_READER 2")
        elif cmd.startswith("UID:"):
            self.out.append(f"UID:{self.card.uid}" if self.card else "ERROR: No card")
        elif cmd.startswith("READ:"):
            _, uid, key = cmd.split(":")
            which, err = self._open(uid, key)
            self.out.append(err or f"DATA:{which}:{self.card.data.hex().upper()}")
            if self.after_read:
                self.after_read(self)
        elif cmd.startswith("WRITE:"):
            _, uid, key, data = cmd.split(":")
            which, err = self._open(uid, key)
            if err:
                self.out.append(err)
            else:
                self.card.data, self.card.key = bytes.fromhex(data), key
                self.out.append("SUCCESS: Written")


def _v2(monkeypatch, fw):
    b = _bridge_with(fw)
    b.proto = 2
    monkeypatch.setattr(nfc_serial, "get_shared_bridge", lambda: b)
    return b


def _job(monkeypatch, job):
    from security_core import SecurityCore
    monkeypatch.setattr(SecurityCore, "_publish_identity", classmethod(lambda cls, *a, **k: None))
    out = {}
    job.failed.connect(lambda m: out.setdefault("failed", m))
    job.succeeded.connect(lambda n: out.setdefault("ok", n))
    job.run()
    return out


def test_registration_locks_the_new_card_with_its_own_key(monkeypatch):
    pytest.importorskip("PyQt6")
    from ui.controller import AppController
    card = Card("A1B2C3D4")
    fw = Firmware2(card)
    _v2(monkeypatch, fw)
    out = _job(monkeypatch, AppController().make_register_job("v2_owner", "nfc"))
    assert out.get("ok") == "v2_owner"
    assert card.key == nfc_serial.card_key("A1B2C3D4") != FACTORY           # a factory-key reader gets nothing now
    assert any(c.startswith("WRITE:A1B2C3D4:") for c in fw.sent)             # the write named the card it checked

    snoop = _bridge_with(Firmware2(card))                                    # someone else's reader / computer
    snoop.proto = 2
    monkeypatch.setattr(nfc_serial, "card_key", lambda uid: "0123456789AB")
    with pytest.raises(nfc_serial.CardLocked):
        snoop.read_payload_from_tag(timeout=1)


def test_a_card_swapped_between_the_check_and_the_write_is_refused(monkeypatch):
    pytest.importorskip("PyQt6")
    from security_core import SecurityCore
    from ui.controller import AppController
    monkeypatch.setattr(SecurityCore, "_publish_identity", classmethod(lambda cls, *a, **k: None))
    SecurityCore.establish_identity("v2_victim", "VictimCardSecret", auth="nfc")
    victim = Card("0BADCAFE", b"VictimCardSecret")
    fw = Firmware2(Card("B1A2B3B4"), after_read=lambda f: setattr(f, "card", victim))   # blank card checked, then swapped
    _v2(monkeypatch, fw)
    out = _job(monkeypatch, AppController().make_register_job("v2_thief", "nfc"))
    assert "different card" in out["failed"]
    assert victim.data.rstrip(b"\0") == b"VictimCardSecret" and victim.key == FACTORY
    assert "v2_thief" not in SecurityCore.list_registered_users()


def test_an_old_factory_key_card_is_locked_at_login(monkeypatch):
    pytest.importorskip("PyQt6")
    from security_core import SecurityCore
    from ui.controller import AppController
    monkeypatch.setattr(SecurityCore, "_publish_identity", classmethod(lambda cls, *a, **k: None))
    SecurityCore.establish_identity("v2_legacy", "LegacyCardSecret", auth="nfc")
    card = Card("C0FFEE00", b"LegacyCardSecret")
    fw = Firmware2(card)
    _v2(monkeypatch, fw)
    assert _job(monkeypatch, AppController().make_login_job("v2_legacy")).get("ok") == "v2_legacy"
    assert card.key == nfc_serial.card_key("C0FFEE00") and card.data.rstrip(b"\0") == b"LegacyCardSecret"
    writes = len([c for c in fw.sent if c.startswith("WRITE")])
    assert _job(monkeypatch, AppController().make_login_job("v2_legacy")).get("ok") == "v2_legacy"
    assert len([c for c in fw.sent if c.startswith("WRITE")]) == writes          # already locked: no more writes


def test_a_wrong_card_is_not_locked(monkeypatch):
    pytest.importorskip("PyQt6")
    from security_core import SecurityCore
    from ui.controller import AppController
    monkeypatch.setattr(SecurityCore, "_publish_identity", classmethod(lambda cls, *a, **k: None))
    SecurityCore.establish_identity("v2_other", "OtherCardSecret1", auth="nfc")
    stranger = Card("5EED5EED", b"SomeoneElsesCard")
    _v2(monkeypatch, Firmware2(stranger))
    assert "does not unlock" in _job(monkeypatch, AppController().make_login_job("v2_other"))["failed"]
    assert stranger.key == FACTORY                                               # never touched


def test_a_card_from_another_computer_gets_a_clear_message(monkeypatch):
    pytest.importorskip("PyQt6")
    from security_core import SecurityCore
    from ui.controller import AppController
    monkeypatch.setattr(SecurityCore, "_publish_identity", classmethod(lambda cls, *a, **k: None))
    SecurityCore.establish_identity("v2_here", "HereCardSecret12", auth="nfc")
    _v2(monkeypatch, Firmware2(Card("FA11FA11", b"x", key="112233445566")))
    assert "another computer" in _job(monkeypatch, AppController().make_login_job("v2_here"))["failed"]


def test_waiting_for_a_card_ends_on_time_and_on_cancel(monkeypatch):
    b = _bridge_with(Firmware2(None))
    b.proto = 2
    t = __import__("time").time()
    assert b.read_payload_from_tag(timeout=0.6) is None
    assert __import__("time").time() - t < 3
    calls = []
    assert b.wait_for_card(30, cancelled=lambda: calls.append(1) or len(calls) > 2) is None   # Cancel stops the wait
    assert len(calls) == 3


def test_the_card_secret_never_reaches_the_log(monkeypatch, caplog):
    b = _bridge_with(Firmware2(Card("DEADBEEF", b"TopSecretCard123")))
    b.proto = 2
    with caplog.at_level("DEBUG", logger="nfc_serial"):
        assert b.read_payload_from_tag(timeout=1) == "TopSecretCard123"
    assert "TopSecretCard123" not in caplog.text and b"TopSecretCard123".hex().upper() not in caplog.text
    assert "DATA:<hidden>" in caplog.text
