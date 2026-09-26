"""
ui/widgets.py — reusable components. Pages are assembled from these; none of them know about the
network, the vault or the crypto.
"""
from __future__ import annotations

import hashlib
import os
from typing import Callable, Optional

from PyQt6.QtCore import (
    QEasingCurve, QEvent, QObject, QPoint, QPropertyAnimation, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation,
    pyqtProperty, pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QFontMetrics, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRegion,
)
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QLineEdit, QProgressBar,
    QBoxLayout, QPushButton, QSizePolicy, QStackedLayout, QVBoxLayout, QWidget,
)

from ui import icons, motion, theme


def polish(w: QWidget) -> None:
    """Re-evaluate the stylesheet after changing a dynamic property."""
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


def clear_layout(layout) -> None:
    """Remove and destroy every widget in a layout IMMEDIATELY (deleteLater alone leaves stale widgets painted)."""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())


def label(text: str = "", role: str = "body", wrap: bool = True, selectable: bool = False) -> QLabel:
    lb = QLabel(text)
    lb.setProperty("role", role)
    lb.setWordWrap(wrap)
    if selectable:
        lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lb


_FILE_KINDS = {
    ("pdf",): ("doc", "danger"),
    ("doc", "docx", "odt", "rtf", "txt", "md", "pages"): ("doc", "primary"),
    ("xls", "xlsx", "csv", "ods", "numbers"): ("sheet", "success"),
    ("ppt", "pptx", "odp", "key"): ("media", "warning"),
    ("png", "jpg", "jpeg", "gif", "webp", "heic", "bmp", "tif", "tiff", "svg"): ("image", "info"),
    ("mp4", "mov", "avi", "mkv", "webm"): ("media", "info"),
    ("mp3", "wav", "m4a", "flac", "aac", "ogg"): ("audio", "info"),
    ("zip", "7z", "rar", "tar", "gz", "tgz", "bz2", "xz", "dmg", "iso"): ("archive", "warning"),
    ("py", "js", "ts", "java", "c", "cpp", "h", "rs", "go", "json", "xml", "html", "css", "sh", "sql"): ("code", "neutral"),
}


def file_icon(name: str) -> tuple[str, str]:
    """(icon, badge kind) for a file name, so a PDF, a photo and a spreadsheet are told apart at a glance."""
    ext = os.path.splitext(name or "")[1].lower().lstrip(".")
    for exts, look in _FILE_KINDS.items():
        if ext in exts:
            return look
    return "file", "neutral"


def friendly_date(stamp: str) -> str:
    """ "2026-09-24 23:14:05" -> "Today, 23:14" / "Yesterday, 09:02" / "Sep 12, 14:30" / "Sep 12, 2025"."""
    import datetime
    try:
        when = datetime.datetime.strptime((stamp or "")[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return (stamp or "")[:16]
    today = datetime.date.today()
    if when.date() == today:
        return f"Today, {when:%H:%M}"
    if when.date() == today - datetime.timedelta(days=1):
        return f"Yesterday, {when:%H:%M}"
    if when.year == today.year:
        return f"{when:%b} {when.day}, {when:%H:%M}"
    return f"{when:%b} {when.day}, {when.year}"


def add_reveal_toggle(edit) -> None:
    """An eye button inside a password field: show what you typed, hide it again."""
    from PyQt6.QtGui import QAction
    from PyQt6.QtWidgets import QLineEdit
    act = QAction(edit)

    def paint() -> None:
        hidden = edit.echoMode() == QLineEdit.EchoMode.Password
        act.setIcon(icons.icon("eye" if hidden else "eye_off", theme.color("text_muted"), 18))
        act.setToolTip("Show passphrase" if hidden else "Hide passphrase")

    def toggle() -> None:
        hidden = edit.echoMode() == QLineEdit.EchoMode.Password
        edit.setEchoMode(QLineEdit.EchoMode.Normal if hidden else QLineEdit.EchoMode.Password)
        paint()
    def hide_again() -> None:
        edit.setEchoMode(QLineEdit.EchoMode.Password)
        paint()
    act.triggered.connect(toggle)
    edit.addAction(act, QLineEdit.ActionPosition.TrailingPosition)
    edit._reveal_action = act
    edit.hide_passphrase = hide_again                   # call when the screen is reused (never leave it revealed)
    paint()


class _OnEnter(QObject):
    def __init__(self, fn, parent):
        super().__init__(parent)
        self._fn = fn

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.KeyPress and ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._fn()
            return True
        return False


def on_enter(widget, fn) -> None:
    """Enter/Return on `widget` (e.g. a list) runs fn, like a double-click does, without single-click activation."""
    f = _OnEnter(fn, widget)
    widget.installEventFilter(f)
    widget._on_enter = f


def escape_goes_back(page, back) -> None:
    """Esc clears a search field that has text; otherwise it runs `back` (only while focus is inside `page`)."""
    from PyQt6.QtGui import QKeySequence, QShortcut
    from PyQt6.QtWidgets import QLineEdit

    def esc() -> None:
        fw = QApplication.focusWidget() or page.focusWidget()   # the page remembers its focused field
        if isinstance(fw, QLineEdit) and fw.text():
            fw.clear()
            return
        back()
    sc = QShortcut(QKeySequence(Qt.Key.Key_Escape), page, context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
    sc.activated.connect(esc)
    page._escape = sc


def human_size(n: Optional[int]) -> str:
    if n is None:
        return "—"
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


def time_ago(ts: int) -> str:
    import time
    d = max(0, int(time.time()) - int(ts))
    if d < 60:
        return "just now"
    if d < 3600:
        return f"{d // 60} min ago"
    if d < 86400:
        return f"{d // 3600} h ago"
    return f"{d // 86400} d ago"


# ── layout helpers ───────────────────────────────────────────────────────────────────────────────
class _EscClears(QObject):
    def eventFilter(self, obj, ev):
        if ev.type() in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress) and ev.key() == Qt.Key.Key_Escape \
                and obj.text():
            if ev.type() == QEvent.Type.KeyPress:
                obj.clear()
            ev.accept()                                   # the box takes Esc: not also "back" or "close"
            return True
        return False


def esc_clears(edit: QLineEdit) -> None:
    """Esc empties a search box, as on a Mac. On an empty box Esc does whatever it would have done anyway."""
    edit.installEventFilter(_EscClears(edit))


class _FitLayout(QStackedLayout):
    """Reports the CURRENT page's size (QStackedLayout reports the largest page, including its height-for-width)."""

    def sizeHint(self) -> QSize:
        w = self.currentWidget()
        return w.sizeHint() if w is not None else super().sizeHint()

    def minimumSize(self) -> QSize:
        w = self.currentWidget()
        return w.minimumSizeHint() if w is not None else super().minimumSize()

    def hasHeightForWidth(self) -> bool:
        w = self.currentWidget()
        return w is not None and w.hasHeightForWidth()

    def heightForWidth(self, width: int) -> int:
        w = self.currentWidget()
        return w.heightForWidth(width) if w is not None else -1


class FitStack(QWidget):
    """
    Pages shown one at a time, like QStackedWidget, but only as tall as the page on screen. A stock stack is as tall
    as its tallest page, which left a short step (e.g. "Choose your name") floating in a mostly empty card.
    """
    currentChanged = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lay = _FitLayout(self)
        self._lay.currentChanged.connect(self._changed)

    def _changed(self, i: int) -> None:
        self._lay.invalidate()
        self.updateGeometry()
        self.currentChanged.emit(i)

    def addWidget(self, w: QWidget) -> int:
        return self._lay.addWidget(w)

    def setCurrentIndex(self, i: int) -> None:
        self._lay.setCurrentIndex(i)

    def setCurrentWidget(self, w: QWidget) -> None:
        self._lay.setCurrentWidget(w)

    def currentIndex(self) -> int:
        return self._lay.currentIndex()

    def currentWidget(self) -> Optional[QWidget]:
        return self._lay.currentWidget()

    def count(self) -> int:
        return self._lay.count()

    def widget(self, i: int) -> Optional[QWidget]:
        return self._lay.widget(i)

    def indexOf(self, w: QWidget) -> int:
        return self._lay.indexOf(w)


class ElidedLabel(QLabel):
    """
    A one-line label that shortens itself with "…" to the space it gets, instead of forcing its container wider (which
    pushed whole pages past the window's right edge in small windows). text() still returns the full text.
    """

    def __init__(self, text: str = "", role: str = "body", parent=None):
        super().__init__(parent)
        self.setProperty("role", role)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(40)
        self.setText(text)

    def text(self) -> str:                                   # noqa: D401 - Qt naming
        return self._full

    def setText(self, text: str) -> None:
        self._full = text or ""
        self._elide()
        self.updateGeometry()

    def sizeHint(self) -> QSize:
        h = super().sizeHint().height()
        return QSize(self.fontMetrics().horizontalAdvance(self._full) + 2, h)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._elide()

    def changeEvent(self, e) -> None:                        # font/style changes (theme switch, bold via stylesheet)
        super().changeEvent(e)
        if e.type() in (e.Type.FontChange, e.Type.StyleChange):
            self._elide()

    def _elide(self) -> None:
        shown = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, max(0, self.width()))
        QLabel.setText(self, shown)
        self.setToolTip(self._full if shown != self._full else "")


# ── buttons ──────────────────────────────────────────────────────────────────────────────────────
def _spinner_icon(color: str, size: int, angle: float) -> QIcon:
    """A 270° neon arc turned by `angle`: one frame of a loading spinner, the same when the button is disabled."""
    ratio = 2.0                                           # crisp on Retina, scaled down elsewhere
    pm = QPixmap(int(size * ratio), int(size * ratio))
    pm.setDevicePixelRatio(ratio)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 2.2)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawArc(QRectF(2, 2, size - 4, size - 4), int(-angle * 16), 270 * 16)
    p.end()
    ic = QIcon()
    for mode in (QIcon.Mode.Normal, QIcon.Mode.Disabled):
        ic.addPixmap(pm, mode)
    return ic


class Button(QPushButton):
    """variant: primary | secondary | ghost | danger;  size: sm | md | lg"""

    def __init__(self, text: str = "", variant: str = "secondary", icon: str = "", size: str = "md", parent=None):
        super().__init__(text, parent)
        self._icon_name = icon
        self.setProperty("variant", variant)
        self.setProperty("size", size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)     # focus ring for keyboard users, not after every click
        self.setIconSize(QSize(18, 18))
        self.refresh_icon()

    def set_icon(self, name: str) -> None:
        self._icon_name = name
        self.refresh_icon()

    def set_busy(self, busy: bool, text: str = "") -> None:
        """While its work runs: disabled, a spinning neon arc for an icon, and optionally other text ("Testing…")."""
        spin = getattr(self, "_spin", None)
        if busy:
            if spin is None:
                self._rest_text = self.text()
                spin = self._spin = QVariantAnimation(self)
                spin.setStartValue(0.0)
                spin.setEndValue(360.0)
                spin.setDuration(900)
                spin.setLoopCount(-1)
                spin.valueChanged.connect(self._spin_frame)
                if motion.reduced():
                    self._spin_frame(45.0)                # a still arc: "working", without the motion
                else:
                    spin.start()
            if text:
                self.setText(text)
            self.setEnabled(False)
            return
        if spin is not None:
            spin.stop()
            spin.deleteLater()
            self._spin = None
            self.setText(self._rest_text)
        self.setEnabled(True)
        self.refresh_icon()

    def busy(self) -> bool:
        return getattr(self, "_spin", None) is not None

    def _spin_frame(self, angle) -> None:
        self.setIcon(_spinner_icon(theme.color("primary"), 16, float(angle)))

    def setToolTip(self, tip: str) -> None:                  # noqa: N802 - Qt naming
        """An icon-only button is named by its tooltip, so screen readers say "Remove from my vault", not "button"."""
        super().setToolTip(tip)
        if not self.text():
            self.setAccessibleName(tip)

    def set_variant(self, variant: str) -> None:
        if self.property("variant") != variant:
            self.setProperty("variant", variant)
            polish(self)
            self.refresh_icon()

    def refresh_icon(self) -> None:
        if not self._icon_name:
            return
        variant = self.property("variant")
        col = {"primary": theme.color("on_primary"), "danger": theme.color("danger")}.get(variant, theme.color("text"))
        if variant == "ghost":
            col = theme.color("text_muted")
        self.setIcon(icons.icon(self._icon_name, col, 18))

    def event(self, e):                                  # keep the icon readable on hover/disabled
        if e.type() in (e.Type.EnabledChange, e.Type.StyleChange):
            self.refresh_icon()
        return super().event(e)


class NavButton(QPushButton):
    def __init__(self, text: str, icon: str, parent=None):
        super().__init__(text.replace("&", "&&"), parent)
        self._icon_name = icon
        self._badge = 0
        self._text = text.replace("&", "&&")
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setProperty("nav", "true")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIconSize(QSize(20, 20))
        self.setMinimumHeight(44)
        self.toggled.connect(lambda _c: self.refresh_icon())
        self.refresh_icon()

    def refresh_icon(self) -> None:
        col = theme.color("primary_on_soft") if self.isChecked() else theme.color("text_muted")
        self.setIcon(icons.icon(self._icon_name, col, 20))

    def keyPressEvent(self, e) -> None:
        """↑ / ↓ move along the sidebar, as in a native sidebar; Enter or Space opens the page."""
        if e.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down) and self.parentWidget() is not None:
            row = sorted((b for b in self.parentWidget().findChildren(NavButton) if b.isVisible()), key=lambda b: b.y())
            i = row.index(self) + (1 if e.key() == Qt.Key.Key_Down else -1)
            if 0 <= i < len(row):
                row[i].setFocus(Qt.FocusReason.TabFocusReason)
            return
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
            return
        super().keyPressEvent(e)

    def badge(self) -> int:
        return self._badge

    def _get_pop(self) -> float:
        return getattr(self, "_pop", 1.0)

    def _set_pop(self, v: float) -> None:
        self._pop = v
        self.update()

    pop = pyqtProperty(float, _get_pop, _set_pop)            # badge scale, animated when a new item arrives

    def set_badge(self, n: int) -> None:
        grew = n > self._badge
        self._badge = n
        if grew:
            motion.pulse(self, b"pop")
        self.setAccessibleName(f"{self._text.replace('&&', '&')}, {n} waiting" if n else self._text.replace("&&", "&"))
        self.update()

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        sliding = getattr(self.parentWidget(), "_ansx_sliding", False)     # the sidebar's glider is moving it
        if self.isChecked() and not sliding:              # the current page: a short neon bar at the left edge
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.qcolor("primary"))
            p.drawRoundedRect(QRectF(0, (self.height() - 18) / 2, 3, 18), 1.5, 1.5)
            p.end()
        if not self._badge:
            return
        text = "99+" if self._badge > 99 else str(self._badge)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        f = QFont(self.font())
        f.setPointSizeF(max(8.0, f.pointSizeF() * 0.8))
        f.setWeight(QFont.Weight.Bold)
        p.setFont(f)
        h = 20
        w = max(h, QFontMetrics(f).horizontalAdvance(text) + 12)
        x, y = self.width() - w - 12, (self.height() - h) // 2
        k = self._get_pop()
        if k != 1.0:                                      # scale around the badge's centre
            p.translate(x + w / 2, y + h / 2)
            p.scale(k, k)
            p.translate(-(x + w / 2), -(y + h / 2))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.color("primary_fill")))       # white count on it: needs the deeper blue
        p.drawRoundedRect(x, y, w, h, h / 2, h / 2)
        p.setPen(QColor(theme.color("on_primary")))
        p.drawText(x, y, w, h, Qt.AlignmentFlag.AlignCenter, text)
        p.end()


# ── small display pieces ─────────────────────────────────────────────────────────────────────────
class NavGlider(QWidget):
    """The current page's neon bar, sliding from the old sidebar item to the new one."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()

    @staticmethod
    def spot(b: QWidget) -> QRect:
        """Where a sidebar item draws its bar, in the sidebar's coordinates."""
        top_left = b.mapTo(b.parentWidget(), QPoint(0, (b.height() - 18) // 2))
        return QRect(top_left.x(), top_left.y(), 3, 18)

    def slide(self, old: QWidget, new: QWidget) -> None:
        if motion.reduced() or old is None or old is new or not self.parentWidget().isVisible():
            return
        anim = QPropertyAnimation(self, b"geometry", self)
        anim.setDuration(motion.NORMAL)
        anim.setStartValue(self.spot(old))
        anim.setEndValue(self.spot(new))
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(self._landed)
        self.parentWidget()._ansx_sliding = True          # on this sidebar only
        self.setGeometry(self.spot(old))
        self.show()
        self.raise_()
        for b in self.parentWidget().findChildren(NavButton):
            b.update()
        motion._keep(self, anim, "_ansx_slide")
        anim.start()

    def _landed(self) -> None:
        self.parentWidget()._ansx_sliding = False
        self.hide()
        for b in self.parentWidget().findChildren(NavButton):
            b.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.qcolor("primary"))
        p.drawRoundedRect(QRectF(self.rect()), 1.5, 1.5)
        p.end()


class SegmentPill(QWidget):
    """The highlight of a segmented switch: it slides to the chosen button instead of jumping."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._target: Optional[QWidget] = None
        parent.installEventFilter(self)

    def move_to(self, b: QWidget) -> None:
        self._target = b
        if b.width() <= 1 or not self.parentWidget().isVisible() or motion.reduced():
            self._snap()                                  # not laid out yet, or motion off: go straight there
            return
        anim = QPropertyAnimation(self, b"geometry", self)
        anim.setDuration(motion.NORMAL)
        anim.setStartValue(self.geometry())
        anim.setEndValue(b.geometry())
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        motion._keep(self, anim, "_ansx_slide")
        anim.start()

    def _snap(self) -> None:
        slide = getattr(self, "_ansx_slide", None)
        if slide is not None and slide.state() == QPropertyAnimation.State.Running:
            return                                        # mid-slide: let it land
        if self._target is not None:
            self.setGeometry(self._target.geometry())
            self.show()
            self.lower()

    def eventFilter(self, obj, ev):
        if obj is self.parent() and ev.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            motion.later(0, self._snap)                   # after the switch has laid its buttons out
        return False

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(theme.qcolor("primary_line"), 1))
        p.setBrush(theme.qcolor("primary_soft"))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 7, 7)
        p.end()


class Pill(QLabel):
    def __init__(self, text: str = "", kind: str = "neutral", parent=None):
        super().__init__(text, parent)
        self.set_kind(kind)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def set_kind(self, kind: str) -> None:
        self.setProperty("pill", kind)
        polish(self)

    def set(self, text: str, kind: str) -> None:
        self.setText(text)
        self.set_kind(kind)


class Avatar(QWidget):
    """Round initials badge; the color is derived from the name so a person always looks the same."""

    def __init__(self, name: str = "?", size: int = 40, parent=None):
        super().__init__(parent)
        self._name, self._size = name, size
        self.setFixedSize(size, size)

    def set_name(self, name: str) -> None:
        self._name = name
        self.update()

    def set_ring(self, on: bool) -> None:
        """A thin green ring with a gap: this person's key is verified. Visible on any badge colour."""
        self._ring = on
        self.update()

    def set_presence(self, kind: Optional[str]) -> None:
        """A small status dot on the badge, e.g. the relay connection: success / info / warning / danger / neutral."""
        self._presence = kind
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette, ink = theme.avatar_colors()
        idx = int(hashlib.md5(self._name.encode()).hexdigest(), 16) % len(palette)
        p.setBrush(QColor(palette[idx]))
        p.setPen(Qt.PenStyle.NoPen)
        if getattr(self, "_ring", False):
            p.drawEllipse(QRectF(4, 4, self._size - 8, self._size - 8))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(theme.qcolor("success"), 2.0))
            p.drawEllipse(QRectF(1, 1, self._size - 2, self._size - 2))
            p.setPen(Qt.PenStyle.NoPen)
        else:
            p.drawEllipse(0, 0, self._size, self._size)
        p.setPen(QColor(ink))
        f = QFont(self.font())
        f.setBold(True)
        f.setPixelSize(int(self._size * 0.38))
        p.setFont(f)
        letters = "".join(w[0] for w in self._name.replace("_", " ").replace(".", " ").replace("-", " ").split()[:2]) or "?"
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, letters.upper())
        kind = getattr(self, "_presence", None)
        if kind:
            d = max(8.0, self._size * 0.3)
            dot = QRectF(self._size - d, self._size - d, d, d)
            p.setPen(QPen(theme.qcolor("surface_alt"), 2.0))     # a ring in the card's colour: cut out of the badge
            p.setBrush(theme.qcolor({"neutral": "text_faint"}.get(kind, kind)))
            p.drawEllipse(dot.adjusted(1, 1, -1, -1))


class AvatarStack(QWidget):
    """Up to three overlapping initials badges (who is waiting, who is involved), with "+N" for the rest."""

    def __init__(self, size: int = 24, parent=None):
        super().__init__(parent)
        self._size = size
        self._badges: list = []
        self.setFixedHeight(size)

    def set_names(self, names: list) -> None:
        for b in self._badges:
            b.deleteLater()
        self._badges = []
        shown, step = names[:3], int(self._size * 0.66)
        for i, name in enumerate(shown):
            b = Avatar(name, self._size, self)
            b.move(i * step, 0)
            b.setToolTip(name)
            b.show()
            self._badges.append(b)
        extra = len(names) - len(shown)
        if extra > 0:
            more = label(f"+{extra}", "faint", wrap=False)
            more.setParent(self)
            more.move(len(shown) * step + self._size - step + 6, (self._size - 16) // 2)
            more.show()
            self._badges.append(more)
        width = (len(shown) - 1) * step + self._size + (34 if extra > 0 else 0) if shown else 0
        self.setFixedWidth(max(0, width))
        self.setVisible(bool(shown))


class ClickablePill(Pill):
    """A status pill that opens the place where that status can be changed."""
    clicked = pyqtSignal()

    def __init__(self, text: str = "", kind: str = "neutral", parent=None):
        super().__init__(text, kind, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.pos()):
            self.clicked.emit()


class IconBadge(QLabel):
    """Rounded square with an icon — used for tiles and empty states."""

    def __init__(self, name: str, kind: str = "primary", size: int = 44, parent=None):
        super().__init__(parent)
        self._name, self._kind, self._size = name, kind, size
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.refresh()

    def refresh(self) -> None:
        fg = theme.color({"primary": "primary", "success": "success", "warning": "warning",
                          "danger": "danger", "info": "info", "neutral": "text_muted"}[self._kind])
        bg = theme.color({"primary": "primary_soft", "success": "success_soft", "warning": "warning_soft",
                          "danger": "danger_soft", "info": "info_soft", "neutral": "surface_alt"}[self._kind])
        edge = QColor(fg)
        self.setStyleSheet(f"background: {bg}; border-radius: {self._size // 3}px; "
                           f"border: 1px solid rgba({edge.red()}, {edge.green()}, {edge.blue()}, 70);")
        self.setPixmap(icons.pixmap(self._name, fg, int(self._size * 0.5)))

    def set(self, name: str, kind: str) -> None:
        self._name, self._kind = name, kind
        self.refresh()


class Card(QFrame):
    def __init__(self, parent=None, alt: bool = False, padding: int = 20, spacing: int = 12):
        super().__init__(parent)
        self.setObjectName("CardAlt" if alt else "Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(padding, padding, padding, padding)
        self.body.setSpacing(spacing)

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        if self.objectName() != "Card" or self.hasFocus() or (self.property("clickable") == "true" and self.underMouse()):
            return                                        # hover and focus draw their own solid border
        # a neon edge: mint at the top left fading out, violet at the bottom right
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        g = QLinearGradient(r.topLeft(), r.bottomRight())
        mint, violet, clear = theme.qcolor("primary"), theme.qcolor("accent"), theme.qcolor("primary")
        mint.setAlphaF(0.34)
        violet.setAlphaF(0.30)
        clear.setAlphaF(0.0)
        g.setColorAt(0.0, mint)
        g.setColorAt(0.45, clear)
        g.setColorAt(0.6, clear)
        g.setColorAt(1.0, violet)
        p.setPen(QPen(QBrush(g), 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, theme.RADIUS - 0.5, theme.RADIUS - 0.5)
        p.end()


class ClickableCard(Card):
    clicked = pyqtSignal()

    def __init__(self, parent=None, padding: int = 20, spacing: int = 8):
        super().__init__(parent, padding=padding, spacing=spacing)
        self.setProperty("clickable", "true")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)     # reachable with Tab, activated with Enter or Space

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.pos()):
            self.clicked.emit()

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
        else:
            super().keyPressEvent(e)


class Divider(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Divider")
        self.setFixedHeight(1)


class Banner(QFrame):
    """Inline message with an optional action button (kind: info | success | warning | danger)."""

    def __init__(self, text: str = "", kind: str = "info", action: str = "", on_action: Optional[Callable] = None, parent=None):
        super().__init__(parent)
        self.setObjectName("Banner")
        self._icon = QLabel()
        self._text = label(text, "body")
        self._btn = Button(action, "secondary", size="sm") if action else None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(12)
        lay.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        self._inner = QBoxLayout(QBoxLayout.Direction.LeftToRight)   # text beside the button, or above it when narrow
        self._inner.setSpacing(10)
        self._inner.addWidget(self._text, 1)
        lay.addLayout(self._inner, 1)
        if self._btn:
            self._inner.addWidget(self._btn, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            if on_action:
                self._btn.clicked.connect(on_action)
        self.set(text, kind)

    NARROW = 520

    def add_action(self, text: str, icon: str, on_action: Callable) -> "Button":
        """A second button beside the first (e.g. "Try again" next to "Connection settings"); they stay together."""
        b = Button(text, "secondary", icon, "sm")
        b.clicked.connect(on_action)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(b)
        if self._btn is not None:
            self._inner.removeWidget(self._btn)
            row.addWidget(self._btn)
        row.addStretch(1)
        self._inner.addLayout(row)
        return b

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        want = (QBoxLayout.Direction.TopToBottom if self._btn is not None and not self._btn.isHidden()
                and self.width() < self.NARROW
                else QBoxLayout.Direction.LeftToRight)
        if self._inner.direction() != want:
            self._inner.setDirection(want)

    def refresh(self) -> None:
        self.set(self._text.text(), self.property("kind") or "info")

    def set(self, text: str, kind: str) -> None:
        self.setProperty("kind", kind)
        name = {"info": "info", "success": "check", "warning": "alert", "danger": "alert"}[kind]
        col = theme.color({"info": "info", "success": "success", "warning": "warning", "danger": "danger"}[kind])
        self._icon.setPixmap(icons.pixmap(name, col, 20))
        self._text.setText(text)
        polish(self)


# Empty states show a small animated illustration (ui/art/) chosen from their icon, instead of a plain icon.
ART_FOR_ICON = {"inbox": "inbox", "send": "plane", "shield": "shield", "users": "friends", "cloud": "cloud",
                "search": "search"}


class EmptyState(QWidget):
    def __init__(self, icon: str, title: str, text: str, action: str = "", on_action: Optional[Callable] = None, parent=None):
        super().__init__(parent)
        lay = self._lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 28)
        lay.setSpacing(10)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(230)              # wrapped text needs room, or Qt clips the last line
        from ui import art
        self.badge = art.make(ART_FOR_ICON.get(icon), 118) or IconBadge(icon, "neutral", 56)
        lay.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignHCenter)
        t = self._title = label(title, "h2", wrap=False)
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(t)
        d = self._text = label(text, "muted")
        d.setAlignment(Qt.AlignmentFlag.AlignCenter)
        d.setMinimumWidth(320)
        d.setMaximumWidth(400)
        d.setMinimumHeight(48)
        lay.addWidget(d, 0, Qt.AlignmentFlag.AlignHCenter)
        if action:
            b = Button(action, "primary")
            if on_action:
                b.clicked.connect(on_action)
            lay.addWidget(b, 0, Qt.AlignmentFlag.AlignHCenter)

    def set_text(self, title: str, text: str) -> None:
        self._title.setText(title)
        self._text.setText(text)

    def set_art(self, scene: str) -> None:
        """Switch the illustration (e.g. "search" when a filter matches nothing)."""
        from ui import art
        if getattr(self.badge, "scene", None) == scene:
            return
        new = art.make(scene, 118)
        if new is None:
            return
        self._lay.replaceWidget(self.badge, new)
        self.badge.deleteLater()
        self.badge = new


class SectionHeader(QWidget):
    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.title = label(title, "h1", wrap=False)
        lay.addWidget(self.title)
        if subtitle:
            self.subtitle = label(subtitle, "muted")
            lay.addWidget(self.subtitle)

    def set_icon(self, name: str) -> None:
        """The page's icon (the same as its sidebar item) as a neon badge before the title."""
        lay = self.layout()
        at = lay.indexOf(self.title)
        lay.removeWidget(self.title)
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(IconBadge(name, "primary", 34), 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.title, 1)
        lay.insertLayout(at, row)


class KeyValue(QWidget):
    def __init__(self, key: str, value: str = "", mono: bool = False, parent=None, key_width: int = 110):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        k = self.key_label = label(key, "muted", wrap=False)
        k.setMinimumWidth(key_width)
        self.value = label(value, "mono" if mono else "body", selectable=True)
        lay.addWidget(k, 0, Qt.AlignmentFlag.AlignTop)
        lay.addWidget(self.value, 1)

    def set(self, value: str) -> None:
        self.value.setText(value)


class KeyArt(QWidget):
    """
    A picture of a key: a mirrored 5×5 pattern drawn from its fingerprint, like the "blockies" wallets show next to
    addresses. The same key always draws the same picture, so two screens side by side compare at a glance. It
    helps the eye; the digits stay the real check.
    """

    def __init__(self, size: int = 40, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._fp = ""
        self.setToolTip("A picture of this key: the same key always draws the same pattern. Compare the digits to be sure.")

    def set(self, fp: str) -> None:
        self._fp = fp
        self.setVisible(bool(fp))
        self.update()

    def paintEvent(self, _e) -> None:
        if not self._fp:
            return
        d = hashlib.sha256(self._fp.encode()).digest()
        light = 0.6 if theme.current() == "dark" else 0.42
        ink = QColor.fromHslF(d[0] / 255, 0.78, light)
        ink2 = QColor.fromHslF(((d[0] + 96 + d[1] // 2) % 256) / 255, 0.72, light)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(theme.qcolor("border"), 1))
        p.setBrush(theme.qcolor("bg"))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        pad, n = 5.0, 5
        cell = (self.width() - 2 * pad) / n
        p.setPen(Qt.PenStyle.NoPen)
        for r in range(n):
            for c in range(3):                            # left half and middle, mirrored to the right
                kind = d[2 + r * 3 + c] % 3
                if not kind:
                    continue
                p.setBrush(ink if kind == 1 else ink2)
                for col in {c, n - 1 - c}:
                    p.drawRoundedRect(QRectF(pad + col * cell + 0.6, pad + r * cell + 0.6, cell - 1.2, cell - 1.2), 1.5, 1.5)
        p.end()


class Fingerprint(QFrame):
    """A key fingerprint in readable groups, with a copy button."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("CardAlt")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 10, 10)
        self.art = KeyArt(40)                             # the key as a picture, for a quick side-by-side look
        self.art.hide()
        lay.addWidget(self.art)
        self.text = label("—", "mono", selectable=True)
        self.text.setStyleSheet("font-size: 14px; letter-spacing: 1px;")
        copy = self.copy_btn = Button("", "ghost", "copy", "sm")
        copy.setToolTip("Copy")
        copy.clicked.connect(self._copy)
        lay.addWidget(self.text, 1)
        lay.addWidget(copy)

    def _copy(self) -> None:
        """Copy, and show that it worked: a check mark and "Copied" for a moment."""
        from PyQt6.QtWidgets import QToolTip
        QApplication.clipboard().setText(getattr(self, "_final", self.text.text()))   # never a mid-reveal frame
        self.copy_btn.set_icon("check")
        self.copy_btn.setToolTip("Copied")
        QToolTip.showText(self.copy_btn.mapToGlobal(self.copy_btn.rect().bottomLeft()), "Copied", self.copy_btn)
        motion.later(1400, self._copy_reset)

    def _copy_reset(self) -> None:
        self.copy_btn.set_icon("copy")
        self.copy_btn.setToolTip("Copy")

    def set(self, fp: str) -> None:
        if fp == getattr(self, "_raw", None) and fp:
            return                                        # the same key again: nothing to reveal
        # two lines of four groups reads much better than one 71-character run
        parts = fp.split()
        self._final = "\n".join([" ".join(parts[:4]), " ".join(parts[4:])]) if len(parts) == 8 else (fp or "—")
        self._raw = fp
        self.art.set(fp)
        motion.scramble(self.text, self._final)           # it resolves like a hash (plain text if motion is off)

    def raw(self) -> str:
        return getattr(self, "_raw", "")


class Stepper(QWidget):
    """Numbered steps: done ✓, current highlighted, upcoming muted. Finished steps can be clicked to go back."""
    step_clicked = pyqtSignal(int)

    def __init__(self, steps: list[str], parent=None):
        super().__init__(parent)
        self._steps, self._current = steps, 0
        self._shown = 0.0                                 # where the connecting line's fill has got to (animated)
        self.clickable = True                             # the owner turns this off while going back is not allowed
        self.setMinimumHeight(38)
        self.setMouseTracking(True)

    def _step_at(self, x: float) -> int:
        i = int(x // (self.width() / max(1, len(self._steps))))
        return i if 0 <= i < len(self._steps) else -1

    def _can_go(self, i: int) -> bool:
        return self.clickable and 0 <= i < self._current

    def mouseMoveEvent(self, e) -> None:
        i = self._step_at(e.position().x())
        go = self._can_go(i)
        self.setCursor(Qt.CursorShape.PointingHandCursor if go else Qt.CursorShape.ArrowCursor)
        self.setToolTip(f"Back to “{self._steps[i]}”" if go else "")

    def mouseReleaseEvent(self, e) -> None:
        i = self._step_at(e.position().x())
        if e.button() == Qt.MouseButton.LeftButton and self._can_go(i):
            self.step_clicked.emit(i)

    def _get_shown(self) -> float:
        return self._shown

    def _set_shown(self, v: float) -> None:
        self._shown = v
        self.update()

    shown = pyqtProperty(float, _get_shown, _set_shown)

    def set_current(self, i: int) -> None:
        old, self._current = self._current, i
        if motion.reduced() or not self.isVisible() or i < old:
            prev = getattr(self, "_ansx_step_anim", None)
            if prev is not None:
                prev.stop()
            self._set_shown(float(i))
            return
        anim = QPropertyAnimation(self, b"shown", self)   # the line to the next step fills in
        anim.setDuration(motion.SLOW)
        anim.setStartValue(float(self._shown))
        anim.setEndValue(float(i))
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        motion._keep(self, anim, "_ansx_step_anim")
        anim.start()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        n = len(self._steps)
        w = self.width() / n
        f = QFont(self.font())
        f.setPixelSize(12)
        f.setBold(True)
        for i, name in enumerate(self._steps):
            cx, cy, r = int(i * w + 16), self.height() // 2, 12
            done, cur = i < self._current, i == self._current
            if i:
                x0 = int((i - 1) * w + 16 + r + 6 + QFontMetrics(f).horizontalAdvance(self._steps[i - 1])) + 8
                x1 = cx - r - 6
                p.setPen(theme.qcolor("border"))
                p.drawLine(x0, cy, x1, cy)
                fill = max(0.0, min(1.0, self._shown - (i - 1)))
                if fill > 0 and x1 > x0:
                    p.setPen(theme.qcolor("primary"))
                    p.drawLine(x0, cy, int(x0 + (x1 - x0) * fill), cy)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.qcolor("success") if done else theme.qcolor("primary_fill") if cur else theme.qcolor("surface_alt"))
            p.drawEllipse(QPoint(cx, cy), r, r)
            p.setFont(f)
            p.setPen(theme.qcolor("on_primary") if (done or cur) else theme.qcolor("text_faint"))
            if done:
                p.drawPixmap(cx - 7, cy - 7, icons.pixmap("check", theme.color("on_primary"), 14, 2.6))
            else:
                p.drawText(cx - r, cy - r, 2 * r, 2 * r, Qt.AlignmentFlag.AlignCenter, str(i + 1))
            p.setPen(theme.qcolor("text") if cur else theme.qcolor("text_muted"))
            p.drawText(cx + r + 8, 0, int(w) - r * 2 - 20, self.height(),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, name)


class NeonBar(QProgressBar):
    """
    The progress bar, alive while work goes on: a glint runs along the filled part, and while there is no
    percentage yet a neon segment slides through. Still when finished, hidden or when motion is reduced.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._phase = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(1300)
        self._anim.setLoopCount(-1)
        self._anim.valueChanged.connect(self._frame)
        self.valueChanged.connect(lambda _v: self._run())

    def setRange(self, lo: int, hi: int) -> None:          # noqa: N802 - Qt naming
        super().setRange(lo, hi)
        self._run()

    def _busy(self) -> bool:
        return self.isVisible() and not motion.reduced() and (self.maximum() == 0 or self.value() < self.maximum())

    def _run(self) -> None:
        if self._busy():
            if self._anim.state() != QVariantAnimation.State.Running:
                self._anim.start()
        else:
            self._anim.stop()
            self.update()

    def _frame(self, v) -> None:
        self._phase = float(v)
        self.update()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._run()

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self._anim.stop()                                 # hidden bars cost nothing

    def paintEvent(self, e) -> None:
        w, h = self.width(), self.height()
        track = QPainterPath()
        track.addRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)
        if self.maximum() == 0:                           # no percentage yet: a segment slides through
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.fillPath(track, theme.qcolor("surface_alt"))
            p.setClipPath(track)
            seg = w * 0.32
            x = -seg + (w + seg) * (self._phase if self._busy() else 0.34)
            neon = QLinearGradient(x, 0, x + seg, 0)
            neon.setColorAt(0.0, QColor(0, 0, 0, 0))
            neon.setColorAt(0.35, theme.qcolor("accent"))
            neon.setColorAt(1.0, theme.qcolor("primary"))
            p.fillRect(QRectF(x, 0, seg, h), neon)
            p.end()
            return
        super().paintEvent(e)                             # the track and the filled part, from the stylesheet
        if not self._busy() or self.value() <= self.minimum():
            return
        filled = w * (self.value() - self.minimum()) / max(1, self.maximum() - self.minimum())
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, filled, h), h / 2, h / 2)
        p.setClipPath(clip)
        band = max(36.0, filled * 0.25)
        x = -band + (filled + band) * self._phase
        glint = QLinearGradient(x, 0, x + band, 0)
        glint.setColorAt(0.0, QColor(255, 255, 255, 0))
        glint.setColorAt(0.5, QColor(255, 255, 255, 110))
        glint.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.fillRect(QRectF(x, 0, band, h), glint)
        p.end()


class ShardStrip(QWidget):
    """
    A file's 12 encrypted pieces as a row of cells that light up as the work goes (any 8 rebuild the file). The
    piece being worked on flickers through hex digits, as if it were being hashed; with no percentage yet, a lit
    cell scans along. Still when motion is reduced.
    """
    N = 12

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(18)
        self.setToolTip("Your file is split into 12 encrypted pieces. Any 8 of them rebuild it.")
        self._lit, self._scan, self._char = 0.0, False, "·"
        import random
        self._rng = random.Random()
        self._tick = QVariantAnimation(self)
        self._tick.setStartValue(0.0)
        self._tick.setEndValue(1.0)
        self._tick.setDuration(900)
        self._tick.setLoopCount(-1)
        self._tick.valueChanged.connect(self._frame)
        self._step = -1

    def set_progress(self, pct: int) -> None:
        self._scan, self._lit = False, max(0.0, min(1.0, pct / 100)) * self.N
        self._run(self._lit < self.N)

    def scanning(self) -> None:
        self._scan = True
        self._run(True)

    def _run(self, busy: bool) -> None:
        if busy and self.isVisible() and not motion.reduced():
            if self._tick.state() != QVariantAnimation.State.Running:
                self._tick.start()
        else:
            self._tick.stop()
        self.update()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._run(self._scan or self._lit < self.N)

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self._tick.stop()

    def _frame(self, v) -> None:
        step = int(float(v) * 14)                        # a new digit ~15 times a second: busy, still readable
        if step != self._step:
            self._step = step
            self._char = self._rng.choice(motion.HEX)
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        gap, h = 4.0, self.height() - 2.0
        w = (self.width() - gap * (self.N - 1)) / self.N
        neon = QLinearGradient(0, 0, self.width(), 0)    # one sweep of violet into mint across the whole row
        neon.setColorAt(0.0, theme.qcolor("accent"))
        neon.setColorAt(1.0, theme.qcolor("primary"))
        moving = self._tick.state() == QVariantAnimation.State.Running
        current = int(float(self._tick.currentValue() or 0.0) * self.N) if self._scan else int(self._lit)
        f = QFont(self.font())
        f.setFamily(theme.mono_family())
        f.setPixelSize(10)
        f.setBold(True)
        p.setFont(f)
        for i in range(self.N):
            cell = QRectF(i * (w + gap), 1, w, h)
            if not self._scan and i < int(self._lit):
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(neon))
                p.drawRoundedRect(cell, 3, 3)
            elif i == current and (self._scan or self._lit < self.N):
                p.setPen(QPen(theme.qcolor("primary"), 1.2))
                p.setBrush(theme.qcolor("primary_soft"))
                p.drawRoundedRect(cell.adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
                p.setPen(theme.qcolor("primary"))
                p.drawText(cell, Qt.AlignmentFlag.AlignCenter, self._char if moving else "·")
            else:
                p.setPen(QPen(theme.qcolor("border_strong"), 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(cell.adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
        p.end()


class PieceMap(QWidget):
    """Where a protected file's pieces are, at a glance: mint in cloud storage, violet inside the package."""

    def __init__(self, cloud: int, inline: int, parent=None):
        super().__init__(parent)
        self._cloud, self._inline = max(0, cloud), max(0, inline)
        n = self._cloud + self._inline
        self.setFixedSize(max(1, n) * 7 - 2, 14)
        self.setToolTip("All pieces inside the package" if not self._cloud else
                        f"{self._cloud} of {n} pieces in cloud storage" + (f", {self._inline} inside the package"
                                                                             if self._inline else ""))
        self.setVisible(n > 0)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(self._cloud + self._inline):
            p.setBrush(theme.qcolor("primary" if i < self._cloud else "accent"))
            p.drawRoundedRect(QRectF(i * 7, 1, 5, 12), 1.5, 1.5)
        p.end()


class Sparkline(QWidget):
    """A small neon line of counts over time (e.g. activity per day), with a soft glow under it."""

    def __init__(self, width: int = 150, height: int = 28, parent=None):
        super().__init__(parent)
        self.setFixedSize(width, height)
        self._values: list = []

    def set_values(self, values: list, tip: str = "") -> None:
        self._values = list(values)
        self.setToolTip(tip)
        self.setVisible(any(self._values))
        self.update()

    def paintEvent(self, _e) -> None:
        vals = self._values
        if len(vals) < 2:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h, pad = self.width(), self.height(), 3.0
        top = max(vals) or 1
        step = (w - 2 * pad) / (len(vals) - 1)
        pts = [(pad + i * step, h - pad - (v / top) * (h - 2 * pad)) for i, v in enumerate(vals)]
        line = QPainterPath()
        line.moveTo(*pts[0])
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):          # gentle curves between the days
            mid = (x0 + x1) / 2
            line.cubicTo(mid, y0, mid, y1, x1, y1)
        glow = QPainterPath(line)
        glow.lineTo(pts[-1][0], h)
        glow.lineTo(pts[0][0], h)
        glow.closeSubpath()
        fade = QLinearGradient(0, 0, 0, h)
        soft = theme.qcolor("primary")
        soft.setAlphaF(0.22)
        clear = theme.qcolor("primary")
        clear.setAlphaF(0.0)
        fade.setColorAt(0.0, soft)
        fade.setColorAt(1.0, clear)
        p.fillPath(glow, QBrush(fade))
        neon = QLinearGradient(0, 0, w, 0)
        neon.setColorAt(0.0, theme.qcolor("accent"))
        neon.setColorAt(1.0, theme.qcolor("primary"))
        pen = QPen(QBrush(neon), 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(line)
        x, y = pts[-1]                                     # today, as a bright dot
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.qcolor("primary"))
        p.drawEllipse(QRectF(x - 2.5, y - 2.5, 5, 5))
        p.end()


class StrengthMeter(QWidget):
    """Four segments that fill as a passphrase gets stronger (red, amber, violet, mint), gliding as you type."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(132, 8)
        self._level, self._kind = 0.0, "danger"
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(motion.FAST)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._glide)

    def set_score(self, score: int, kind: str) -> None:
        self._kind = kind
        target = float(max(0, min(3, score)) + 1)         # "too short" still lights one red segment
        if motion.reduced() or not self.isVisible():
            self._level = target
            self.update()
            return
        self._anim.stop()
        self._anim.setStartValue(self._level)
        self._anim.setEndValue(target)
        self._anim.start()

    def _glide(self, v) -> None:
        self._level = float(v)
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        gap, n = 4.0, 4
        w = (self.width() - gap * (n - 1)) / n
        h = self.height() - 2.0
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(n):
            x = i * (w + gap)
            p.setBrush(theme.qcolor("surface_alt"))
            p.drawRoundedRect(QRectF(x, 1, w, h), h / 2, h / 2)
            fill = max(0.0, min(1.0, self._level - i))
            if fill > 0:
                p.setBrush(theme.qcolor(self._kind))
                p.drawRoundedRect(QRectF(x, 1, w * fill, h), h / 2, h / 2)
        p.end()


class ProgressRing(QWidget):
    """A small ring that fills as steps get done, "1/3" in the middle: progress at a glance."""

    def __init__(self, size: int = 40, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._done, self._total = 0, 1

    def set(self, done: int, total: int) -> None:
        self._done, self._total = done, max(1, total)
        self.setToolTip(f"{done} of {total} done")
        self.setAccessibleName(f"{done} of {total} done")
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(3, 3, self.width() - 6, self.height() - 6)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(theme.qcolor("border_strong"), 3.5))
        p.drawEllipse(r)
        if self._done:
            neon = QLinearGradient(r.topLeft(), r.bottomRight())        # violet into mint, like the progress bars
            neon.setColorAt(0.0, theme.qcolor("accent"))
            neon.setColorAt(1.0, theme.qcolor("primary"))
            pen = QPen(QBrush(neon), 3.5)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawArc(r, 90 * 16, -round(360 * 16 * min(1.0, self._done / self._total)))
        f = QFont(self.font())
        f.setFamily(theme.mono_family())
        f.setPixelSize(max(9, self.height() // 4))
        f.setBold(True)
        p.setFont(f)
        p.setPen(theme.qcolor("text"))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, f"{self._done}/{self._total}")
        p.end()


class ProgressPanel(Card):
    """Title, live status text, progress bar and an optional cancel button."""
    cancel_clicked = pyqtSignal()

    def __init__(self, parent=None, cancellable: bool = True, pieces: bool = False):
        super().__init__(parent, padding=18, spacing=10)
        top = QHBoxLayout()
        self.title = label("", "h2", wrap=False)
        self.cancel_btn = Button("Cancel", "ghost", size="sm")
        self.cancel_btn.clicked.connect(self.cancel_clicked)
        self.cancel_btn.setVisible(cancellable)
        top.addWidget(self.title, 1)
        top.addWidget(self.cancel_btn)
        self.body.addLayout(top)
        self.bar = NeonBar()
        self.bar.setRange(0, 100)
        self.body.addWidget(self.bar)
        self.shards = ShardStrip() if pieces else None     # the 12 pieces, for work that splits or rebuilds a file
        if self.shards is not None:
            self.body.addWidget(self.shards)
        row = QHBoxLayout()
        row.setSpacing(12)
        self.detail = label("", "muted")
        self.pct = label("", "mono", wrap=False)          # "42%", in the vault's monospace
        self.pct.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.detail, 1, Qt.AlignmentFlag.AlignTop)
        row.addWidget(self.pct, 0, Qt.AlignmentFlag.AlignTop)
        self.body.addLayout(row)
        self.hide()

    def start(self, title: str) -> None:
        self.title.setText(title)
        self.detail.setText("")
        self.pct.setText("0%")
        self.pct.show()
        if self.shards is not None:
            self.shards.set_progress(0)
        self.bar.setRange(0, 100)
        motion.progress_to(self.bar, 0)
        self.cancel_btn.setEnabled(True)
        motion.reveal(self, motion.FAST)

    def update_progress(self, text: str, pct: int) -> None:
        self.detail.setText(text)
        pct = max(0, min(100, pct))
        if self.bar.maximum() == 0:                       # back from "working on it" to a real percentage
            self.bar.setRange(0, 100)
        self.pct.setText(f"{pct}%")
        self.pct.show()
        if self.shards is not None:
            self.shards.set_progress(pct)
        motion.progress_to(self.bar, pct)

    def indeterminate(self, text: str) -> None:
        self.detail.setText(text)
        self.pct.hide()                                   # no number to show
        self.bar.setRange(0, 0)
        if self.shards is not None:
            self.shards.scanning()

    def finish(self) -> None:
        self.hide()


def _marching(owner: QWidget) -> QVariantAnimation:
    """A looping 0 → 1 phase for a dashed edge that runs round ("marching ants"); repaints its owner."""
    a = QVariantAnimation(owner)
    a.setStartValue(0.0)
    a.setEndValue(1.0)
    a.setDuration(800)
    a.setLoopCount(-1)
    a.valueChanged.connect(lambda _v: owner.update())   # the animation is the owner's child: gone with it
    return a


def _dashed_frame(w: QWidget, r: QRectF, radius: float, ants: QVariantAnimation) -> None:
    p = QPainter(w)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(theme.qcolor("primary"), 2)
    pen.setDashPattern([5, 4])
    pen.setDashOffset(-9 * float(ants.currentValue() or 0.0))     # one dash + gap per loop: a steady crawl
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(r, radius, radius)
    p.end()


class DropZone(QFrame):
    """Drop a file here or click to browse."""
    file_chosen = pyqtSignal(str)                  # the first file (older callers)
    files_chosen = pyqtSignal(list)                # every file dropped or chosen

    def __init__(self, title: str = "Drop a file here", hint: str = "or click to choose one", parent=None):
        super().__init__(parent)
        self._ants = _marching(self)
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(180)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(8)
        lay.addWidget(IconBadge("upload", "primary", 56), 0, Qt.AlignmentFlag.AlignHCenter)
        t = label(title, "h2", wrap=False)
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        h = label(hint, "muted", wrap=False)
        h.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(t)
        lay.addWidget(h)

    def _set_active(self, on: bool) -> None:
        self.setProperty("active", "true" if on else "false")
        polish(self)
        if on and not motion.reduced():
            self._ants.start()                            # the dashed edge runs round while a file hovers over it
        else:
            self._ants.stop()
        self.update()

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        if self.property("active") == "true":
            _dashed_frame(self, QRectF(self.rect()).adjusted(1, 1, -1, -1), 16, self._ants)

    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasUrls() and any(u.isLocalFile() and os.path.isfile(u.toLocalFile()) for u in e.mimeData().urls()):
            e.acceptProposedAction()
            self._set_active(True)

    def dragLeaveEvent(self, _e) -> None:
        self._set_active(False)

    def dropEvent(self, e) -> None:
        self._set_active(False)
        files = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile() and os.path.isfile(u.toLocalFile())]
        if files:
            self.files_chosen.emit(files)
            self.file_chosen.emit(files[0])

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.choose()

    def choose(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Choose files to protect")   # one or several
        if paths:
            self.files_chosen.emit(paths)
            self.file_chosen.emit(paths[0])


class ListRow(QWidget):
    """Avatar/icon + two text lines + optional trailing widgets, for QListWidget.setItemWidget."""

    def __init__(self, title: str, subtitle: str = "", avatar: str = "", icon: str = "", right: Optional[list] = None,
                 parent=None, icon_kind: str = "neutral", verified: bool = False):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(12)
        if avatar:
            self.avatar = Avatar(avatar, 38)
            self.avatar.set_ring(verified)
            lay.addWidget(self.avatar)
        elif icon:
            lay.addWidget(IconBadge(icon, icon_kind, 38))
        col = QVBoxLayout()
        col.setSpacing(1)
        self.title = ElidedLabel(title, "body")
        self.title.setStyleSheet("font-weight: 600;")
        self.subtitle = ElidedLabel(subtitle, "muted")
        col.addWidget(self.title)
        col.addWidget(self.subtitle)
        lay.addLayout(col, 1)
        for w in right or []:
            lay.addWidget(w, 0, Qt.AlignmentFlag.AlignVCenter)


# ── toasts ───────────────────────────────────────────────────────────────────────────────────────
class _Toast(QFrame):
    """
    One notice. It stays while the pointer is on it (you may be reading it, or about to click it), and a thin line
    along its bottom edge shows how long it will stay once you move away.
    """

    def __init__(self, text: str, kind: str, parent=None, ms: int = 5000):
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setProperty("kind", kind)
        self._color = {"success": "success", "warning": "warning", "error": "danger", "info": "info"}.get(kind, "info")
        self._total = self._left = max(1, ms)
        self._fading = False
        self._clock = QTimer(self)                        # when it goes; the host connects timeout to its fade
        self._clock.setSingleShot(True)
        self._tick = QTimer(self)                         # repaints the time-left line
        self._tick.setInterval(40)
        self._tick.timeout.connect(self.update)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)
        icon = {"success": "check", "warning": "alert", "error": "alert", "info": "info"}.get(kind, "info")
        col = {"success": "success", "warning": "warning", "error": "danger", "info": "info"}.get(kind, "info")
        ic = QLabel()
        ic.setPixmap(icons.pixmap(icon, theme.color(col), 18))
        msg = label(text, "body")
        msg.setMinimumWidth(240)
        msg.setMaximumWidth(340)
        lay.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
        lay.addWidget(msg, 1)
        self.close_btn = QPushButton()                    # dismiss without doing what the notice offers
        self.close_btn.setObjectName("ToastClose")
        self.close_btn.setIcon(icons.icon("x", theme.color("text_faint"), 14))
        self.close_btn.setFixedSize(22, 22)
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.setToolTip("Dismiss")
        self.close_btn.setAccessibleName("Dismiss")
        self.close_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.close_btn.clicked.connect(self._dismiss)
        lay.addWidget(self.close_btn, 0, Qt.AlignmentFlag.AlignTop)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        polish(self)

    def _dismiss(self) -> None:
        self.on_click = None
        self.hide()
        self.deleteLater()

    on_click: Optional[Callable[[], None]] = None

    def start(self) -> None:
        self._clock.start(self._left)
        if not motion.reduced():
            self._tick.start()

    def enterEvent(self, e) -> None:
        if self._clock.isActive():                        # hold it while the pointer is on it
            self._left = self._clock.remainingTime()
            self._clock.stop()
            self._tick.stop()
            self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        if not self._clock.isActive() and not self._fading:
            self._left = max(self._left, 1500)            # time to read the end of it
            self.start()
        super().leaveEvent(e)

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        if motion.reduced():
            return
        left = self._clock.remainingTime() if self._clock.isActive() else self._left
        frac = max(0.0, min(1.0, left / self._total))
        if frac <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        line = theme.qcolor(self._color)
        line.setAlphaF(0.6)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(line)
        width = (self.width() - 28) * frac
        p.drawRoundedRect(QRectF(14, self.height() - 5, width, 2), 1, 1)
        p.end()

    def mousePressEvent(self, _e) -> None:
        action, self.on_click = self.on_click, None
        self.hide()
        self.deleteLater()
        if action:
            action()


class DropOverlay(QWidget):
    """
    Covers the window while files are dragged over it and says what dropping them will do. It ignores the mouse
    entirely, so it can never take a click; the window underneath handles the drop.
    """

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._title = self._detail = ""
        self._icon, self._kind = "upload", "primary"
        self._ants = _marching(self)
        parent.installEventFilter(self)
        self.hide()

    def eventFilter(self, obj, ev):
        if obj is self.parent() and ev.type() == QEvent.Type.Resize:
            self.setGeometry(self.parentWidget().rect())
        return False

    def show_for(self, title: str, detail: str, icon: str = "upload", kind: str = "primary") -> None:
        self._title, self._detail, self._icon, self._kind = title, detail, icon, kind
        self.setGeometry(self.parentWidget().rect())
        self.raise_()
        if not self.isVisible():
            self.show()
            motion.fade_in(self, motion.FAST)
        if not motion.reduced():
            self._ants.start()
        self.update()

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self._ants.stop()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        veil = theme.qcolor("bg")
        veil.setAlphaF(0.78)
        p.fillRect(self.rect(), veil)
        frame = QRectF(self.rect()).adjusted(18, 18, -18, -18)
        pen = QPen(theme.qcolor("primary"), 2)
        pen.setDashPattern([5, 4])
        pen.setDashOffset(-9 * float(self._ants.currentValue() or 0.0))   # the edge runs round while dragging
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(frame, 22, 22)
        w = min(560.0, frame.width() - 80)
        card = QRectF(frame.center().x() - w / 2, frame.center().y() - 105, w, 210)
        p.setPen(QPen(theme.qcolor("primary_line"), 1))
        p.setBrush(theme.qcolor("surface"))
        p.drawRoundedRect(card, 18, 18)                   # the message sits on its own card, clear of the page
        cx = card.center().x()
        badge = QRectF(cx - 32, card.top() + 26, 64, 64)
        fg = {"neutral": "text_muted"}.get(self._kind, self._kind)       # what is being dragged: a PDF, a photo, a key…
        bg = {"neutral": "surface_alt"}.get(self._kind, f"{self._kind}_soft")
        p.setBrush(theme.qcolor(bg))
        p.drawRoundedRect(badge, 20, 20)
        p.drawPixmap(int(cx - 16), int(badge.center().y() - 16), icons.pixmap(self._icon, theme.color(fg), 32))
        title = QFont(self.font())
        title.setFamily(theme.mono_family())
        title.setPixelSize(22)
        title.setBold(True)
        p.setFont(title)
        p.setPen(theme.qcolor("text"))
        p.drawText(QRectF(card.left(), card.top() + 104, card.width(), 32), Qt.AlignmentFlag.AlignCenter, self._title)
        body = QFont(self.font())
        body.setPixelSize(13)
        p.setFont(body)
        p.setPen(theme.qcolor("text_muted"))
        detail = QFontMetrics(body).elidedText(self._detail, Qt.TextElideMode.ElideMiddle, int(card.width()) - 40)
        p.drawText(QRectF(card.left(), card.top() + 146, card.width(), 22), Qt.AlignmentFlag.AlignCenter, detail)
        p.end()


class ShortcutSheet(QWidget):
    """
    The keyboard shortcuts, floating over whatever you are doing (⌘/): each one with its keys drawn as keycaps.
    Esc, a click anywhere or ⌘/ again closes it.
    """

    def __init__(self, parent: QWidget, rows: list):
        super().__init__(parent)
        self._rows = rows                                 # (what it does, "⌘ F")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        parent.installEventFilter(self)
        self.hide()

    def eventFilter(self, obj, ev):
        if obj is self.parent() and ev.type() == QEvent.Type.Resize:
            self.setGeometry(self.parentWidget().rect())
        return False

    def toggle(self) -> None:
        if self.isVisible():
            self.hide()
            return
        self.setGeometry(self.parentWidget().rect())
        self.raise_()
        self.show()
        self.setFocus(Qt.FocusReason.ShortcutFocusReason)
        motion.fade_in(self, motion.FAST)

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(e)

    def mousePressEvent(self, _e) -> None:
        self.hide()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        veil = theme.qcolor("bg")
        veil.setAlphaF(0.82)
        p.fillRect(self.rect(), veil)
        row_h = 34
        w = min(560.0, self.width() - 60.0)
        h = 92 + row_h * len(self._rows)
        card = QRectF((self.width() - w) / 2, max(20.0, (self.height() - h) / 2), w, h)
        p.setPen(QPen(theme.qcolor("primary_line"), 1))
        p.setBrush(theme.qcolor("surface"))
        p.drawRoundedRect(card, 18, 18)
        title = QFont(self.font())
        title.setFamily(theme.mono_family())
        title.setPixelSize(18)
        title.setBold(True)
        p.setFont(title)
        p.setPen(theme.qcolor("text"))
        p.drawText(QRectF(card.left() + 26, card.top() + 20, w - 52, 26), Qt.AlignmentFlag.AlignLeft, "Keyboard shortcuts")
        small = QFont(self.font())
        small.setPixelSize(11)
        p.setFont(small)
        p.setPen(theme.qcolor("text_faint"))
        p.drawText(QRectF(card.left() + 26, card.top() + 20, w - 52, 26),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "Esc to close")
        body, cap = QFont(self.font()), QFont(self.font())
        body.setPixelSize(13)
        cap.setFamily(theme.mono_family())
        cap.setPixelSize(12)
        cap.setBold(True)
        y = card.top() + 66
        for what, keys in self._rows:
            p.setFont(body)
            p.setPen(theme.qcolor("text_muted"))
            p.drawText(QRectF(card.left() + 26, y, w * 0.55, row_h), Qt.AlignmentFlag.AlignVCenter, what)
            p.setFont(cap)
            fm = QFontMetrics(cap)
            x = card.right() - 26
            for k in reversed(keys.split()):              # keycaps, right-aligned
                kw = max(24, fm.horizontalAdvance(k) + 14)
                x -= kw
                box = QRectF(x, y + 5, kw, row_h - 10)
                p.setPen(QPen(theme.qcolor("border_strong"), 1))
                p.setBrush(theme.qcolor("surface_alt"))
                p.drawRoundedRect(box, 6, 6)
                p.setPen(theme.qcolor("primary"))
                p.drawText(box, Qt.AlignmentFlag.AlignCenter, k)
                x -= 6
            y += row_h
        p.end()


class ToastHost(QWidget):
    """
    Overlay in the bottom-right corner of a window. It is sized to fit ONLY the toasts it currently shows and hides
    itself when empty, so it can never cover (and swallow clicks meant for) the buttons underneath.
    """
    WIDTH = 400
    MAX = 4

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(8)
        self.setFixedWidth(self.WIDTH)
        parent.installEventFilter(self)
        self.hide()

    def eventFilter(self, obj, ev):
        if obj is self.parent() and ev.type() == ev.Type.Resize:
            self._fit()
        elif isinstance(obj, _Toast) and ev.type() in (ev.Type.Resize, ev.Type.Move):
            self._update_mask()                           # a notice took its final size, or moved up the stack
        return False

    def _update_mask(self) -> None:
        """Only the notices themselves take clicks; the gaps between them let clicks through to the page."""
        area = QRegion()
        for t in self._toasts():
            area = area.united(QRegion(t.geometry()))
        if not area.isEmpty():
            self.setMask(area)

    def _toasts(self) -> list:
        return [self._lay.itemAt(i).widget() for i in range(self._lay.count()) if self._lay.itemAt(i).widget()]

    def _fit(self) -> None:
        toasts = self._toasts()
        if not toasts:
            self.hide()
            return
        self.adjustSize()
        p = self.parentWidget()
        self.move(p.width() - self.WIDTH - 20, p.height() - 38 - self.height() - 14)   # above the status bar
        self._update_mask()
        self.show()
        self.raise_()

    def show_toast(self, text: str, kind: str = "info", ms: int = 5000,
                   on_click: Optional[Callable[[], None]] = None) -> None:
        shown = self._toasts()
        while len(shown) >= self.MAX:                     # a burst of news: the oldest make room, the stack stays short
            old = shown.pop(0)
            self._lay.removeWidget(old)                   # out of the stack now, not when Qt gets round to deleting it
            old.hide()
            old.deleteLater()
        t = _Toast(text, kind, self, ms)
        t.on_click = on_click
        t.installEventFilter(self)                        # keeps the click mask on the notices as they settle
        t.destroyed.connect(lambda *_: QTimer.singleShot(0, self._fit))
        self._lay.addWidget(t)
        t.show()
        self._fit()
        motion.fade_in(t, motion.NORMAL)
        t._clock.timeout.connect(lambda: self._fade(t))  # the toast's own timer: gone with the toast
        t.start()

    def _fade(self, t: QFrame) -> None:
        try:
            t._fading = True
            t._tick.stop()
            if motion.reduced():
                t.hide()
                t.deleteLater()
                return
            eff = QGraphicsOpacityEffect(t)
            t.setGraphicsEffect(eff)
            anim = QPropertyAnimation(eff, b"opacity", t)
            anim.setDuration(350)
            anim.setStartValue(1.0)
            anim.setEndValue(0.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.finished.connect(t.deleteLater)
            anim.start()
            t._anim = anim
        except RuntimeError:
            pass
