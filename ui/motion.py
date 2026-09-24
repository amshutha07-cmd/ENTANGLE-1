"""
ui/motion.py — small, quick animations that help people see what just changed: a page fading in, a notification
sliding into place, a progress bar moving smoothly, a wrong passphrase shaking the card.

Rules: short (120-320 ms), never block input, always end in exactly the state the code asked for, and turn off
completely when the person prefers less motion (Settings → Reduce motion, or the operating system's own setting).
"""
from __future__ import annotations

import os
import platform
import subprocess
from typing import Callable, Optional

from PyQt6.QtCore import QAbstractAnimation, QEasingCurve, QObject, QPoint, QPropertyAnimation, QTimer, QVariantAnimation
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QProgressBar, QWidget

FAST, NORMAL, SLOW = 140, 220, 320

_reduced_pref: Optional[bool] = None       # the app setting, cached (set_reduced keeps it current)
_system: Optional[bool] = None             # the operating system's "reduce motion", read once


def _system_prefers_less_motion() -> bool:
    global _system
    if _system is None:
        _system = False
        try:
            if platform.system() == "Darwin":
                out = subprocess.run(["defaults", "read", "com.apple.universalaccess", "reduceMotion"],
                                     capture_output=True, text=True, timeout=2).stdout.strip()
                _system = out == "1"
            elif platform.system() == "Windows":
                import ctypes
                on = ctypes.c_bool(True)
                ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(on), 0)   # SPI_GETCLIENTAREAANIMATION
                _system = not on.value
        except Exception:
            _system = False
    return _system


def reduced() -> bool:
    """True when animations should be skipped."""
    if os.environ.get("ANSX_REDUCE_MOTION", "").strip() not in ("", "0"):
        return True
    global _reduced_pref
    if _reduced_pref is None:
        try:
            from ui.controller import pref
            _reduced_pref = bool(pref("reduce_motion", False))
        except Exception:
            _reduced_pref = False
    return _reduced_pref or _system_prefers_less_motion()


def set_reduced(value: bool) -> None:
    global _reduced_pref
    _reduced_pref = bool(value)


def _keep(owner: QObject, anim: QAbstractAnimation, key: str) -> None:
    """Hold a reference while the animation runs (Python would otherwise collect it), replacing any earlier one."""
    old = getattr(owner, key, None)
    if old is not None:
        try:
            old.stop()
        except RuntimeError:
            pass
    setattr(owner, key, anim)
    anim.finished.connect(lambda: getattr(owner, key, None) is anim and setattr(owner, key, None))


# ── fades ───────────────────────────────────────────────────────────────────────────────────────
def fade_in(w: QWidget, ms: int = NORMAL, start: float = 0.0) -> None:
    """Fade a widget in. The opacity effect is removed at the end, so nothing keeps costing render time."""
    if reduced() or w is None:
        return
    current = w.graphicsEffect()
    if current is not None and not getattr(current, "_ansx_fade", False):
        return                                            # the widget manages its own effect (e.g. a toast)
    eff = QGraphicsOpacityEffect(w)
    eff._ansx_fade = True
    eff.setOpacity(start)
    w.setGraphicsEffect(eff)
    anim = QPropertyAnimation(eff, b"opacity", w)
    anim.setDuration(ms)
    anim.setStartValue(start)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def done() -> None:
        try:
            if w.graphicsEffect() is eff:
                w.setGraphicsEffect(None)
        except RuntimeError:                              # the widget went away mid-animation
            pass
    anim.finished.connect(done)
    _keep(w, anim, "_ansx_fade_anim")
    anim.start()


def flash(w: QWidget, color: str, ms: int = 1400) -> None:
    """Briefly tint a widget's background, then fade back: "this is the new one"."""
    if reduced() or w is None:
        return
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QColor
    w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    base = QColor(color)
    name = w.objectName() or "ansxFlash"
    w.setObjectName(name)
    anim = QVariantAnimation(w)
    anim.setDuration(ms)
    anim.setStartValue(0.0)
    anim.setKeyValueAt(0.15, 1.0)
    anim.setEndValue(0.0)
    anim.setEasingCurve(QEasingCurve.Type.InOutQuad)

    def paint(v) -> None:
        c = QColor(base)
        c.setAlphaF(max(0.0, min(1.0, float(v))) * base.alphaF())
        w.setStyleSheet(f"#{name} {{ background: rgba({c.red()},{c.green()},{c.blue()},{c.alpha()}); "
                        f"border-radius: 10px; }}")
    anim.valueChanged.connect(paint)
    anim.finished.connect(lambda: w.setStyleSheet(""))
    _keep(w, anim, "_ansx_flash_anim")
    anim.start()


def reveal(w: QWidget, ms: int = NORMAL) -> None:
    """show() plus a quick fade, for messages and result panels that appear in place."""
    w.show()
    fade_in(w, ms)


def breathe(w: QWidget, on: bool, ms: int = 1400) -> None:
    """A slow, looping fade for something in progress (e.g. "Connecting…"); off removes it and restores full opacity."""
    anim = getattr(w, "_ansx_breathe_anim", None)
    if not on or reduced():
        if anim is not None:
            anim.stop()
            w._ansx_breathe_anim = None
            try:
                if w.graphicsEffect() is not None and getattr(w.graphicsEffect(), "_ansx_breathe", False):
                    w.setGraphicsEffect(None)
            except RuntimeError:
                pass
        return
    if anim is not None:
        return                                            # already breathing
    eff = QGraphicsOpacityEffect(w)
    eff._ansx_breathe = True
    w.setGraphicsEffect(eff)
    anim = QPropertyAnimation(eff, b"opacity", w)
    anim.setDuration(ms)
    anim.setStartValue(1.0)
    anim.setKeyValueAt(0.5, 0.45)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.InOutSine)
    anim.setLoopCount(-1)
    w._ansx_breathe_anim = anim
    anim.start()


# ── movement ────────────────────────────────────────────────────────────────────────────────────
def shake(w: QWidget, distance: int = 9, ms: int = SLOW) -> None:
    """
    A short horizontal shake ("that's not right"). It starts after any pending layout change (the same failure often
    shows an error line, which moves the widget), and at the end the layout puts the widget back where it belongs.
    """
    if reduced() or w is None:
        return

    def start() -> None:
        try:
            home = w.pos()
        except RuntimeError:
            return
        anim = QVariantAnimation(w)
        anim.setDuration(ms)
        anim.setStartValue(home)
        for t, dx in ((0.15, -distance), (0.35, distance), (0.55, -distance * 0.6),
                      (0.75, distance * 0.3), (0.9, -distance * 0.1)):
            anim.setKeyValueAt(t, home + QPoint(int(dx), 0))
        anim.setEndValue(home)
        anim.valueChanged.connect(lambda p: w.move(p))
        anim.finished.connect(lambda: _settle(w))
        _keep(w, anim, "_ansx_shake_anim")
        anim.start()
    QTimer.singleShot(0, start)


def _settle(w: QWidget) -> None:
    """Let the parent's layout place the widget again (it may have moved while we animated its position)."""
    try:
        parent = w.parentWidget()
        lay = parent.layout() if parent is not None else None
        if lay is not None:
            lay.invalidate()
            lay.activate()
    except RuntimeError:
        pass


def grow_in(w: QWidget, ms: int = NORMAL, on_done: Optional[Callable[[], None]] = None) -> None:
    """Open a widget's height from 0 to its natural height (things below slide down instead of jumping)."""
    if reduced() or w is None:
        if on_done:
            on_done()
        return
    target = max(1, w.sizeHint().height())
    anim = QPropertyAnimation(w, b"maximumHeight", w)
    anim.setDuration(ms)
    anim.setStartValue(0)
    anim.setEndValue(target)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def done() -> None:
        try:
            w.setMaximumHeight(16777215)                  # QWIDGETSIZE_MAX: free to resize again
        except RuntimeError:
            return
        if on_done:
            on_done()
    anim.finished.connect(done)
    _keep(w, anim, "_ansx_grow_anim")
    w.setMaximumHeight(0)
    anim.start()


# ── values ──────────────────────────────────────────────────────────────────────────────────────
def progress_to(bar: QProgressBar, value: int, ms: int = 260) -> None:
    """Move a progress bar smoothly to `value` (instantly when going back to 0, or for a busy/indeterminate bar)."""
    value = max(bar.minimum(), min(bar.maximum(), int(value)))
    if reduced() or bar.maximum() == bar.minimum() or value <= bar.value() or not bar.isVisible():
        prev = getattr(bar, "_ansx_value_anim", None)
        if prev is not None:
            prev.stop()
        bar.setValue(value)
        return
    anim = QPropertyAnimation(bar, b"value", bar)
    anim.setDuration(ms)
    anim.setStartValue(bar.value())
    anim.setEndValue(value)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    _keep(bar, anim, "_ansx_value_anim")
    anim.start()


def pulse(obj: QObject, prop: bytes, peak: float = 1.35, ms: int = SLOW) -> None:
    """Animate a float property from `peak` back to 1.0 with a little overshoot: a "pop" for a new badge count."""
    if reduced():
        return
    anim = QPropertyAnimation(obj, prop, obj)
    anim.setDuration(ms)
    anim.setStartValue(peak)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.OutBack)
    _keep(obj, anim, "_ansx_pulse_anim")
    anim.start()


def later(ms: int, fn: Callable[[], None]) -> None:
    """Run fn after ms; quietly does nothing if the widget it touches has been deleted by then."""
    def run() -> None:
        try:
            fn()
        except RuntimeError:
            pass
    QTimer.singleShot(ms, run)
