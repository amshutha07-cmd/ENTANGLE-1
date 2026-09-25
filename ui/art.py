"""
ui/art.py — small animated illustrations (the SVG files in ui/art/).

Each SVG is flat art whose moving parts are top-level groups with ids ("eyes", "spark1", "z2", …). Qt's own SVG
animation cannot fade or scale, so the motion lives here instead: every frame each layer is drawn with its own
offset, rotation, scale and opacity. Motion is gentle, loops seamlessly, runs only while the picture is on screen,
and stops completely when the person prefers reduced motion (the picture stays, frozen at its resting pose).

The background circle and ground shadow follow the theme ({{blob}}, {{shadow}} in the SVG), so the art sits well
on both dark and light.
"""
from __future__ import annotations

import math
import os
import re
import time
import xml.etree.ElementTree as ET
from typing import Optional

from PyQt6.QtCore import QByteArray, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import QPainter
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QSizePolicy, QWidget

from ui import motion, theme

ART_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "art")

# Per scene: layer id -> effects. Amplitudes are in SVG units (viewBox 200 x 160), periods in seconds, phases 0-1.
#   ("bob", amp, period, phase)          float up and down
#   ("sway", amp, period, phase)         drift left and right
#   ("tilt", degrees, period, phase[, (ox, oy)])   rock around the layer centre (or the given point)
#   ("pulse", amount, period, phase[, (ox, oy)])   grow and shrink (e.g. a ground shadow as the body rises)
#   ("twinkle", period, phase)           fade and scale like a star
#   ("blink", every, phase)              a quick squash of the eyes now and then
#   ("rise", distance, period, phase)    float up while fading in and out (zZz, file pieces)
#   ("breathe", amount, period, phase[, (ox, oy)])  stretch taller from the bottom edge (or the given point)
#   ("fade", period, phase)              fade in and out without changing size
FLOAT = ("bob", 5, 3.2, 0.0)
SCENES: dict[str, dict[str, list]] = {
    "shield": {
        "blob": [("pulse", 0.015, 6.0, 0.0)],
        "shadow": [("pulse", -0.14, 3.2, 0.0)],
        "body": [FLOAT], "face": [FLOAT],
        "eyes": [FLOAT, ("blink", 4.2, 0.35)],
        "spark1": [("twinkle", 2.4, 0.0)], "spark2": [("twinkle", 2.4, 0.33)], "spark3": [("twinkle", 2.4, 0.66)],
    },
    "inbox": {                                           # asleep: breathing slowly, zZz drifting up
        "blob": [("pulse", 0.015, 6.0, 0.0)],
        "shadow": [("pulse", 0.04, 3.6, 0.0)],
        "tray": [("breathe", 0.035, 3.6, 0.0, (100, 130))],
        "face": [("breathe", 0.035, 3.6, 0.0, (100, 130))],
        "z1": [("rise", 16, 3.0, 0.0)], "z2": [("rise", 16, 3.0, 0.33)], "z3": [("rise", 16, 3.0, 0.66)],
        "spark1": [("twinkle", 2.8, 0.2)],
    },
    "plane": {                                           # gliding: gentle rise and fall, nose following the path
        "blob": [("pulse", 0.015, 6.0, 0.0)],
        "cloud1": [("sway", 7, 7.0, 0.0)], "cloud2": [("sway", -6, 9.0, 0.3)],
        "plane": [("bob", 6, 2.8, 0.0), ("tilt", 4, 2.8, 0.25)],
        "spark1": [("twinkle", 2.2, 0.0)], "spark2": [("twinkle", 2.2, 0.4)], "spark3": [("twinkle", 2.2, 0.7)],
    },
    "friends": {                                         # two friends bouncing out of step; one waves
        "blob": [("pulse", 0.015, 6.0, 0.0)],
        "wave": [("bob", 3, 2.6, 0.0), ("tilt", 22, 1.1, 0.0, (57, 97))],
        "left": [("bob", 3, 2.6, 0.0)], "left_face": [("bob", 3, 2.6, 0.0)],
        "right": [("bob", 3, 2.6, 0.5)], "right_face": [("bob", 3, 2.6, 0.5)],
        "heart": [("bob", 4, 2.0, 0.0), ("pulse", 0.08, 1.0, 0.0)],
        "spark1": [("twinkle", 2.4, 0.0)], "spark2": [("twinkle", 2.4, 0.5)],
    },
    "cloud": {                                           # a happy cloud; pieces float up into it
        "blob": [("pulse", 0.015, 6.0, 0.0)],
        "cloud": [("bob", 4, 3.4, 0.0)], "face": [("bob", 4, 3.4, 0.0)],
        "eyes": [("bob", 4, 3.4, 0.0), ("blink", 5.0, 0.6)],
        "p1": [("rise", 40, 3.6, 0.0)], "p2": [("rise", 40, 3.6, 0.33)], "p3": [("rise", 40, 3.6, 0.66)],
        "spark1": [("twinkle", 2.6, 0.0)],
    },
    "search": {                                          # looking around, thinking dots
        "blob": [("pulse", 0.015, 6.0, 0.0)],
        "shadow": [("pulse", 0.05, 2.6, 0.0)],
        "glass": [("sway", 5, 2.6, 0.0), ("tilt", 7, 2.6, 0.25, (146, 126))],
        "dot1": [("sway", 5, 2.6, 0.0), ("tilt", 7, 2.6, 0.25, (146, 126)), ("fade", 1.2, 0.0)],
        "dot2": [("sway", 5, 2.6, 0.0), ("tilt", 7, 2.6, 0.25, (146, 126)), ("fade", 1.2, 0.15)],
        "dot3": [("sway", 5, 2.6, 0.0), ("tilt", 7, 2.6, 0.25, (146, 126)), ("fade", 1.2, 0.3)],
        "spark1": [("twinkle", 2.4, 0.0)], "spark2": [("twinkle", 2.4, 0.5)],
    },
}


# The scenes are drawn in one pastel palette; the vault's neon look maps it to mint, violet and coin gold. Pinks,
# white and the ink of the faces stay as drawn. (Keys are the colors in the SVG files, upper case.)
NEON = {
    # periwinkle → mint and teal
    "#EEF2FF": "#E6FFF8", "#EAF0FF": "#E2FFF7", "#DCE5FF": "#CFFBEF", "#C9D5FF": "#B5F9E8", "#B9C8FF": "#9BF7E0",
    "#9DB2FF": "#63F2D2", "#98AEFF": "#5CEFCF", "#8EA6FF": "#3BE8C4", "#7C86F5": "#12C7A5",
    # the shield mascot: mint into violet
    "#9BB1FF": "#5FFBD8", "#5B7CFA": "#8A5CFF",
    # lavender → violet
    "#C3B8FF": "#D9B8FF", "#9E93FA": "#B98AFF", "#8F84F5": "#A66BFF", "#7C70EC": "#8F4DFF",
    # sparkles, blocks and friends: coin gold and neon mint
    "#FFD66B": "#FFC83D", "#8CE0C4": "#3DFFC8", "#6FD3B1": "#14DDAA",
}
NEON_SCENE = {"cloud": {"#8EA6FF": "#A66BFF"}}         # the cloud's three blocks: violet, gold, mint


def _neon(name: str, text: str) -> str:
    swap = {**NEON, **NEON_SCENE.get(name, {})}
    return re.sub(r"#[0-9A-Fa-f]{6}\b", lambda m: swap.get(m.group(0).upper(), m.group(0)), text)


def _svg_text(name: str) -> str:
    with open(os.path.join(ART_DIR, f"{name}.svg"), encoding="utf-8") as f:
        text = _neon(name, f.read())
    colors = {"blob": theme.color("primary_soft"),
              "shadow": "#000000" if theme.current() == "dark" else "#1B2540"}
    text = text.replace("{{blob}}", colors["blob"]).replace("{{shadow}}", colors["shadow"])
    if theme.current() != "dark":                        # a softer ground shadow on light backgrounds
        text = text.replace('fill="#1B2540"', 'fill="#1B2540" fill-opacity="0.10"')
    else:
        text = text.replace('fill="#000000"', 'fill="#000000" fill-opacity="0.30"')
    return text


def _layer_ids(svg: str) -> list[str]:
    """Top-level <g id=…> elements, in drawing order."""
    root = ET.fromstring(re.sub(r"\{\{[a-z_]+\}\}", "#000000", svg))
    return [el.get("id") for el in root if el.tag.endswith("}g") and el.get("id")]


def _wave(t: float, period: float, phase: float) -> float:
    return math.sin(2 * math.pi * (t / period + phase))


def layer_state(effects: list, t: float, bounds: QRectF) -> tuple:
    """(dx, dy, degrees, sx, sy, opacity, origin) for one layer at time t (t=0 and reduced motion: at rest)."""
    dx = dy = rot = 0.0
    sx = sy = op = 1.0
    origin = bounds.center()
    for eff in effects:
        kind = eff[0]
        if kind == "bob":
            dy -= eff[1] * _wave(t, eff[2], eff[3])
        elif kind == "sway":
            dx += eff[1] * _wave(t, eff[2], eff[3])
        elif kind == "tilt":
            rot += eff[1] * _wave(t, eff[2], eff[3])
            if len(eff) > 4:
                origin = type(origin)(*eff[4])
        elif kind == "pulse":
            k = 1 + eff[1] * _wave(t, eff[2], eff[3])
            sx *= k
            sy *= k
            if len(eff) > 4:
                origin = type(origin)(*eff[4])
        elif kind == "breathe":
            sy *= 1 + eff[1] * (0.5 + 0.5 * _wave(t, eff[2], eff[3]))
            origin = type(origin)(*eff[4]) if len(eff) > 4 else type(origin)(bounds.center().x(), bounds.bottom())
        elif kind == "fade":
            op *= 0.3 + 0.7 * (0.5 + 0.5 * _wave(t, eff[1], eff[2]))
        elif kind == "twinkle":
            w = 0.5 + 0.5 * _wave(t, eff[1], eff[2])
            op *= 0.35 + 0.65 * w
            sx *= 0.75 + 0.35 * w
            sy *= 0.75 + 0.35 * w
        elif kind == "blink":
            u = ((t / eff[1]) + eff[2]) % 1.0
            span = 0.16 / eff[1]                          # a blink lasts about 0.16 s
            if u < span:
                sy *= max(0.08, abs(1 - 2 * u / span))
        elif kind == "rise":
            u = 0.5 if t == 0 else ((t / eff[2]) + eff[3]) % 1.0     # at rest: halfway up, fully visible
            dy -= eff[1] * u
            op *= math.sin(math.pi * u)                   # fades in, then out, as it rises
    return dx, dy, rot, sx, sy, op, origin


class Illustration(QWidget):
    """An animated scene from ui/art/, scaled to fit while keeping its proportions."""

    FPS = 30

    def __init__(self, scene: str, height: int = 130, parent=None):
        super().__init__(parent)
        self.scene = scene
        self._h = height
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)   # decoration only
        self._t0 = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setInterval(1000 // self.FPS)
        self._timer.timeout.connect(self.update)
        self.retheme()

    def retheme(self) -> None:
        svg = _svg_text(self.scene)
        self._layers = _layer_ids(svg)
        self._renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")), self)
        self._renderer.setAnimationEnabled(False)         # our own engine does the moving
        self.update()

    def sizeHint(self) -> QSize:
        vb = self._renderer.viewBoxF()
        return QSize(int(self._h * vb.width() / max(1.0, vb.height())), self._h)

    def elapsed(self) -> float:
        return 0.0 if motion.reduced() else time.monotonic() - self._t0

    # only animate while someone can see it
    def showEvent(self, e) -> None:
        super().showEvent(e)
        if not motion.reduced():
            self._timer.start()

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self._timer.stop()

    def paintEvent(self, _e) -> None:
        if motion.reduced() and self._timer.isActive():   # the setting changed while on screen
            self._timer.stop()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        vb = self._renderer.viewBoxF()
        s = min(self.width() / vb.width(), self.height() / vb.height())
        ox = (self.width() - vb.width() * s) / 2
        oy = (self.height() - vb.height() * s) / 2
        t = self.elapsed()
        effects = SCENES.get(self.scene, {})
        for lid in self._layers:
            b = self._renderer.boundsOnElement(lid)
            dx, dy, rot, sx, sy, op, origin = layer_state(effects.get(lid, []), t, b)
            if op <= 0.01:
                continue
            p.save()
            p.translate(ox, oy)
            p.scale(s, s)
            p.translate(origin.x() + dx, origin.y() + dy)
            if rot:
                p.rotate(rot)
            if sx != 1.0 or sy != 1.0:
                p.scale(sx, sy)
            p.translate(-origin.x(), -origin.y())
            p.setOpacity(op)
            self._renderer.render(p, lid, b)
            p.restore()
        p.end()


def scenes() -> list[str]:
    return sorted(n[:-4] for n in os.listdir(ART_DIR) if n.endswith(".svg"))


def make(scene: Optional[str], height: int = 130) -> Optional[Illustration]:
    return Illustration(scene, height) if scene and os.path.exists(os.path.join(ART_DIR, f"{scene}.svg")) else None
