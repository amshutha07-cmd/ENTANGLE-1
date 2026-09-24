"""Animations: each one ends in exactly the state the code asked for, and all of them switch off with Reduce motion."""
import time

import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtWidgets import QApplication, QProgressBar, QVBoxLayout, QWidget  # noqa: E402

from ui import motion  # noqa: E402
from ui.widgets import Fingerprint, NavButton, Stepper  # noqa: E402

app = QApplication.instance() or QApplication([])


def watch(read, until, seconds=1.5):
    """Values of read() while an animation runs, until `until()` holds (robust on a busy machine)."""
    seen = []
    t0 = time.time()
    while time.time() - t0 < seconds:
        app.processEvents()
        seen.append(read())
        if until():
            break
        time.sleep(0.003)
    return seen


def pump(ms):
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()
        time.sleep(0.004)


@pytest.fixture(autouse=True)
def motion_on(monkeypatch):
    monkeypatch.delenv("ANSX_REDUCE_MOTION", raising=False)
    monkeypatch.setattr(motion, "_system", False)            # ignore this computer's accessibility setting
    motion.set_reduced(False)
    yield
    motion.set_reduced(False)


def test_fade_in_runs_and_then_removes_its_effect():
    w = QWidget()
    w.show()
    motion.fade_in(w, 150)
    seen = set()
    t0 = time.time()
    while time.time() - t0 < 1.0 and w.graphicsEffect() is not None:      # watch it happen, however busy the machine
        seen.add(round(w.graphicsEffect().opacity(), 2))
        app.processEvents()
        time.sleep(0.003)
    assert any(0 < o < 1 for o in seen)                      # it really faded, rather than popping in
    assert w.graphicsEffect() is None                        # and left no lasting render cost


def test_progress_moves_smoothly_up_and_jumps_back_down():
    bar = QProgressBar()
    bar.setRange(0, 100)
    bar.show()
    motion.progress_to(bar, 80, 150)
    seen = watch(bar.value, lambda: bar.value() == 80)
    assert any(0 < v < 80 for v in seen) and bar.value() == 80
    motion.progress_to(bar, 0)                               # a new job starts: no animated rewind
    assert bar.value() == 0


def test_stepper_fills_forward_and_snaps_back():
    s = Stepper(["Choose a file", "Choose a person", "Review and send"])
    s.resize(700, 40)
    s.show()
    s.set_current(2)
    seen = watch(lambda: s.shown, lambda: s.shown == 2.0)
    assert any(0 < v < 2 for v in seen) and s.shown == 2.0
    s.set_current(0)
    assert s.shown == 0.0


def test_badge_pops_only_when_the_count_grows():
    b = NavButton("Inbox", "inbox")
    b.resize(200, 44)
    b.show()
    b.set_badge(1)
    seen = watch(lambda: b.pop, lambda: getattr(b, "_ansx_pulse_anim", None) is None)
    assert any(v > 1.05 for v in seen) and b.pop == pytest.approx(1.0)
    b.set_badge(0)
    assert getattr(b, "_ansx_pulse_anim", None) is None


def test_shake_ends_in_the_layout_slot():
    outer = QWidget()
    lay = QVBoxLayout(outer)
    card = QWidget()
    card.setMinimumSize(200, 80)
    lay.addWidget(card)
    outer.resize(400, 200)
    outer.show()
    pump(50)
    slot = lay.itemAt(0).geometry()
    xs = set()
    motion.shake(card, ms=200)
    t0 = time.time()
    while time.time() - t0 < 0.35:
        app.processEvents()
        xs.add(card.pos().x())
        time.sleep(0.004)
    assert len(xs) > 3                                       # it really moved
    assert card.geometry() == slot                           # and came back exactly


def test_copy_button_confirms_then_resets(monkeypatch):
    fp = Fingerprint()
    fp.set("A" * 64)
    fp.show()
    fp._copy()
    assert QApplication.clipboard().text() == fp.text.text()
    assert fp.copy_btn._icon_name == "check" and fp.copy_btn.toolTip() == "Copied"
    pump(1600)
    assert fp.copy_btn._icon_name == "copy" and fp.copy_btn.toolTip() == "Copy"


def test_reduce_motion_turns_everything_off(monkeypatch):
    monkeypatch.setenv("ANSX_REDUCE_MOTION", "1")
    w = QWidget()
    w.show()
    motion.fade_in(w)
    assert w.graphicsEffect() is None
    bar = QProgressBar()
    bar.setRange(0, 100)
    bar.show()
    motion.progress_to(bar, 70)
    assert bar.value() == 70                                 # immediately
    s = Stepper(["a", "b"])
    s.show()
    s.set_current(1)
    assert s.shown == 1.0
    monkeypatch.delenv("ANSX_REDUCE_MOTION")
    motion.set_reduced(True)                                 # the Settings switch
    assert motion.reduced()


def test_settings_switch_saves_and_applies_reduced_motion():
    from ui.controller import AppController, pref
    from ui.pages.settings import SettingsPage
    page = SettingsPage(AppController())
    page._loading = False
    page.motion_combo.setCurrentIndex(1)                     # "Reduced"
    assert pref("reduce_motion") is True and motion.reduced()
    page.motion_combo.setCurrentIndex(0)                     # "On"
    assert pref("reduce_motion") is False and not motion.reduced()
