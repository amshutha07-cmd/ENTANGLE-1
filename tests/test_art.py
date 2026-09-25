"""The animated illustrations: every scene loads and moves, rests when motion is reduced, and costs nothing hidden."""
import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtCore import QRectF  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from ui import art, motion, theme  # noqa: E402

app = QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def motion_on(monkeypatch):
    monkeypatch.delenv("ANSX_REDUCE_MOTION", raising=False)
    monkeypatch.setattr(motion, "_system", False)
    motion.set_reduced(False)
    yield
    motion.set_reduced(False)


def frame(scene: str, t: float, theme_name: str = "dark"):
    theme.apply(app, theme_name)
    w = art.Illustration(scene, 160)
    w.resize(220, 160)
    w.elapsed = lambda: t
    return w.grab().toImage()


def test_every_scene_loads_and_every_animated_layer_exists():
    names = art.scenes()
    assert {"shield", "inbox", "plane", "friends", "cloud", "search"} <= set(names)
    for name in names:
        layers = art.Illustration(name)._layers
        assert layers, name
        missing = [lid for lid in art.SCENES.get(name, {}) if lid not in layers]
        assert not missing, f"{name}: animation names layers the SVG lacks: {missing}"
        assert art.SCENES.get(name), f"{name} has no animation"


def test_each_scene_draws_and_moves():
    for name in art.scenes():
        still, later = frame(name, 0.0), frame(name, 1.3)
        painted = sum(1 for x in range(0, 220, 7) for y in range(0, 160, 7) if still.pixelColor(x, y).alpha() > 0)
        assert painted > 40, f"{name} drew almost nothing"
        assert still != later, f"{name} does not move"


def test_reduced_motion_rests_and_starts_no_timer(monkeypatch):
    monkeypatch.setenv("ANSX_REDUCE_MOTION", "1")
    w = art.Illustration("plane", 140)
    w.show()
    assert w.elapsed() == 0.0 and not w._timer.isActive()


def test_animates_only_while_visible():
    w = art.Illustration("inbox", 140)
    assert not w._timer.isActive()
    w.show()
    assert w._timer.isActive()
    w.hide()
    assert not w._timer.isActive()                          # hidden pages cost no CPU


def test_background_follows_the_theme():
    dark, light = frame("shield", 0.0, "dark"), frame("shield", 0.0, "light")
    x, y = 55, 84                     # inside the background circle, left of the mascot (viewBox 45,84 + 10px margin)
    assert dark.pixelColor(x, y).alpha() > 0 and light.pixelColor(x, y).alpha() > 0
    assert dark.pixelColor(x, y) != light.pixelColor(x, y)
    theme.apply(app, "dark")


def test_motion_math_rests_and_loops():
    b = QRectF(0, 0, 10, 10)
    _dx, dy, _r, _sx, _sy, op, _o = art.layer_state([("rise", 16, 3.0, 0.4)], 0.0, b)
    assert dy == -8 and op == pytest.approx(1.0)            # at rest: halfway up, fully visible
    blinks = [art.layer_state([("blink", 4.0, 0.0)], t / 100, b)[4] for t in range(0, 400)]
    assert min(blinks) < 0.2 and sum(1 for s in blinks if s < 1) <= 17  # a quick squash (~0.16 s), then open
    a = art.layer_state([("bob", 5, 2.0, 0.0), ("twinkle", 2.0, 0.0)], 0.3, b)
    c = art.layer_state([("bob", 5, 2.0, 0.0), ("twinkle", 2.0, 0.0)], 2.3, b)
    assert a[1] == pytest.approx(c[1]) and a[5] == pytest.approx(c[5])   # seamless loop


def test_empty_states_use_illustrations_and_can_switch():
    from ui.widgets import EmptyState
    e = EmptyState("inbox", "Inbox is empty", "…")
    assert isinstance(e.badge, art.Illustration) and e.badge.scene == "inbox"
    e.set_art("search")
    assert e.badge.scene == "search"
    plain = EmptyState("activity", "Nothing yet", "…")       # no drawing for this one: the icon stays
    assert not isinstance(plain.badge, art.Illustration)


def test_installer_bundles_the_art():
    spec = open("ANSxVault.spec").read()
    assert "ui/art/*.svg" in spec
