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
