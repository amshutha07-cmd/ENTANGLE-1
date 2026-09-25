"""
ui/widgets.py — reusable components. Pages are assembled from these; none of them know about the
network, the vault or the crypto.
"""
from __future__ import annotations

import hashlib
import os
from typing import Callable, Optional

from PyQt6.QtCore import (
    QEasingCurve, QEvent, QObject, QPoint, QPropertyAnimation, QSize, Qt, QTimer, pyqtProperty, pyqtSignal,
)
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QProgressBar,
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
    PALETTE = ["#5B7CFA", "#8B5CF6", "#EC4899", "#F59E0B", "#10B981", "#06B6D4", "#EF4444", "#84CC16"]

    def __init__(self, name: str = "?", size: int = 40, parent=None):
        super().__init__(parent)
        self._name, self._size = name, size
        self.setFixedSize(size, size)

    def set_name(self, name: str) -> None:
        self._name = name
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        idx = int(hashlib.md5(self._name.encode()).hexdigest(), 16) % len(self.PALETTE)
        p.setBrush(QColor(self.PALETTE[idx]))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(0, 0, self._size, self._size)
        p.setPen(QColor("#FFFFFF"))
        f = QFont(self.font())
        f.setBold(True)
        f.setPixelSize(int(self._size * 0.38))
        p.setFont(f)
        letters = "".join(w[0] for w in self._name.replace("_", " ").replace(".", " ").replace("-", " ").split()[:2]) or "?"
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, letters.upper())


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
        self.setStyleSheet(f"background: {bg}; border-radius: {self._size // 3}px;")
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


class Fingerprint(QFrame):
    """A key fingerprint in readable groups, with a copy button."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("CardAlt")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 10, 10)
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
        QApplication.clipboard().setText(self.text.text())
        self.copy_btn.set_icon("check")
        self.copy_btn.setToolTip("Copied")
        QToolTip.showText(self.copy_btn.mapToGlobal(self.copy_btn.rect().bottomLeft()), "Copied", self.copy_btn)
        motion.later(1400, self._copy_reset)

    def _copy_reset(self) -> None:
        self.copy_btn.set_icon("copy")
        self.copy_btn.setToolTip("Copy")

    def set(self, fp: str) -> None:
        # two lines of four groups reads much better than one 71-character run
        parts = fp.split()
        self.text.setText("\n".join([" ".join(parts[:4]), " ".join(parts[4:])]) if len(parts) == 8 else (fp or "—"))
        self._raw = fp

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
            p.setPen(QColor("#FFFFFF") if (done or cur) else theme.qcolor("text_faint"))
            if done:
                p.drawPixmap(cx - 7, cy - 7, icons.pixmap("check", "#FFFFFF", 14, 2.6))
            else:
                p.drawText(cx - r, cy - r, 2 * r, 2 * r, Qt.AlignmentFlag.AlignCenter, str(i + 1))
            p.setPen(theme.qcolor("text") if cur else theme.qcolor("text_muted"))
            p.drawText(cx + r + 8, 0, int(w) - r * 2 - 20, self.height(),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, name)


class ProgressPanel(Card):
    """Title, live status text, progress bar and an optional cancel button."""
    cancel_clicked = pyqtSignal()

    def __init__(self, parent=None, cancellable: bool = True):
        super().__init__(parent, padding=18, spacing=10)
        top = QHBoxLayout()
        self.title = label("", "h2", wrap=False)
        self.cancel_btn = Button("Cancel", "ghost", size="sm")
        self.cancel_btn.clicked.connect(self.cancel_clicked)
        self.cancel_btn.setVisible(cancellable)
        top.addWidget(self.title, 1)
        top.addWidget(self.cancel_btn)
        self.body.addLayout(top)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.body.addWidget(self.bar)
        self.detail = label("", "muted")
        self.body.addWidget(self.detail)
        self.hide()

    def start(self, title: str) -> None:
        self.title.setText(title)
        self.detail.setText("")
        self.bar.setRange(0, 100)
        motion.progress_to(self.bar, 0)
        self.cancel_btn.setEnabled(True)
        motion.reveal(self, motion.FAST)

    def update_progress(self, text: str, pct: int) -> None:
        self.detail.setText(text)
        motion.progress_to(self.bar, max(0, min(100, pct)))

    def indeterminate(self, text: str) -> None:
        self.detail.setText(text)
        self.bar.setRange(0, 0)

    def finish(self) -> None:
        self.hide()


class DropZone(QFrame):
    """Drop a file here or click to browse."""
    file_chosen = pyqtSignal(str)                  # the first file (older callers)
    files_chosen = pyqtSignal(list)                # every file dropped or chosen

    def __init__(self, title: str = "Drop a file here", hint: str = "or click to choose one", parent=None):
        super().__init__(parent)
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
                 parent=None, icon_kind: str = "neutral"):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(12)
        if avatar:
            lay.addWidget(Avatar(avatar, 38))
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
    def __init__(self, text: str, kind: str, parent=None):
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setProperty("kind", kind)
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
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        polish(self)

    on_click: Optional[Callable[[], None]] = None

    def mousePressEvent(self, _e) -> None:
        action, self.on_click = self.on_click, None
        self.hide()
        self.deleteLater()
        if action:
            action()


class ToastHost(QWidget):
    """
    Overlay in the bottom-right corner of a window. It is sized to fit ONLY the toasts it currently shows and hides
    itself when empty, so it can never cover (and swallow clicks meant for) the buttons underneath.
    """
    WIDTH = 380

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
        return False

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
        self.show()
        self.raise_()

    def show_toast(self, text: str, kind: str = "info", ms: int = 5000,
                   on_click: Optional[Callable[[], None]] = None) -> None:
        t = _Toast(text, kind, self)
        t.on_click = on_click
        t.destroyed.connect(lambda *_: QTimer.singleShot(0, self._fit))
        self._lay.addWidget(t)
        t.show()
        self._fit()
        motion.fade_in(t, motion.NORMAL)
        QTimer.singleShot(ms, lambda: self._fade(t))

    def _fade(self, t: QFrame) -> None:
        try:
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
