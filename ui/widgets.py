"""
ui/widgets.py — reusable components. Pages are assembled from these; none of them know about the
network, the vault or the crypto.
"""
from __future__ import annotations

import hashlib
import os
from typing import Callable, Optional

from PyQt6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QProgressBar,
    QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from ui import icons, theme


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


# ── buttons ──────────────────────────────────────────────────────────────────────────────────────
class Button(QPushButton):
    """variant: primary | secondary | ghost | danger;  size: sm | md | lg"""

    def __init__(self, text: str = "", variant: str = "secondary", icon: str = "", size: str = "md", parent=None):
        super().__init__(text, parent)
        self._icon_name = icon
        self.setProperty("variant", variant)
        self.setProperty("size", size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIconSize(QSize(18, 18))
        self.refresh_icon()

    def set_icon(self, name: str) -> None:
        self._icon_name = name
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
        self.setProperty("nav", "true")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIconSize(QSize(20, 20))
        self.setMinimumHeight(44)
        self.toggled.connect(lambda _c: self.refresh_icon())
        self.refresh_icon()

    def refresh_icon(self) -> None:
        col = theme.color("primary") if self.isChecked() else theme.color("text_muted")
        self.setIcon(icons.icon(self._icon_name, col, 20))

    def badge(self) -> int:
        return self._badge

    def set_badge(self, n: int) -> None:
        self._badge = n
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
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.color("primary")))
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

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.pos()):
            self.clicked.emit()


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
        lay.addWidget(self._text, 1)
        if self._btn:
            lay.addWidget(self._btn)
            if on_action:
                self._btn.clicked.connect(on_action)
        self.set(text, kind)

    def refresh(self) -> None:
        self.set(self._text.text(), self.property("kind") or "info")

    def set(self, text: str, kind: str) -> None:
        self.setProperty("kind", kind)
        name = {"info": "info", "success": "check", "warning": "alert", "danger": "alert"}[kind]
        col = theme.color({"info": "info", "success": "success", "warning": "warning", "danger": "danger"}[kind])
        self._icon.setPixmap(icons.pixmap(name, col, 20))
        self._text.setText(text)
        polish(self)


class EmptyState(QWidget):
    def __init__(self, icon: str, title: str, text: str, action: str = "", on_action: Optional[Callable] = None, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 28, 24, 28)
        lay.setSpacing(10)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(230)              # wrapped text needs room, or Qt clips the last line
        self.badge = IconBadge(icon, "neutral", 56)
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
    def __init__(self, key: str, value: str = "", mono: bool = False, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        k = label(key, "muted", wrap=False)
        k.setMinimumWidth(110)
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
        copy = Button("", "ghost", "copy", "sm")
        copy.setToolTip("Copy")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.text.text()))
        lay.addWidget(self.text, 1)
        lay.addWidget(copy)

    def set(self, fp: str) -> None:
        # two lines of four groups reads much better than one 71-character run
        parts = fp.split()
        self.text.setText("\n".join([" ".join(parts[:4]), " ".join(parts[4:])]) if len(parts) == 8 else (fp or "—"))
        self._raw = fp

    def raw(self) -> str:
        return getattr(self, "_raw", "")


class Stepper(QWidget):
    """Numbered steps: done ✓, current highlighted, upcoming muted."""

    def __init__(self, steps: list[str], parent=None):
        super().__init__(parent)
        self._steps, self._current = steps, 0
        self.setMinimumHeight(38)

    def set_current(self, i: int) -> None:
        self._current = i
        self.update()

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
                p.setPen(theme.qcolor("primary") if i <= self._current else theme.qcolor("border"))
                p.drawLine(int((i - 1) * w + 16 + r + 6 + QFontMetrics(f).horizontalAdvance(self._steps[i - 1])) + 8, cy,
                           cx - r - 6, cy)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.qcolor("success") if done else theme.qcolor("primary") if cur else theme.qcolor("surface_alt"))
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
        self.bar.setValue(0)
        self.cancel_btn.setEnabled(True)
        self.show()

    def update_progress(self, text: str, pct: int) -> None:
        self.detail.setText(text)
        self.bar.setValue(max(0, min(100, pct)))

    def indeterminate(self, text: str) -> None:
        self.detail.setText(text)
        self.bar.setRange(0, 0)

    def finish(self) -> None:
        self.hide()


class DropZone(QFrame):
    """Drop a file here or click to browse."""
    file_chosen = pyqtSignal(str)

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
        for u in e.mimeData().urls():
            if u.isLocalFile() and os.path.isfile(u.toLocalFile()):
                self.file_chosen.emit(u.toLocalFile())
                return

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.choose()

    def choose(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose a file to protect")
        if path:
            self.file_chosen.emit(path)


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
        self.title = label(title, "body", wrap=False)
        self.title.setStyleSheet("font-weight: 600;")
        self.subtitle = label(subtitle, "muted", wrap=False)
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

    def mousePressEvent(self, _e) -> None:
        self.hide()
        self.deleteLater()


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

    def show_toast(self, text: str, kind: str = "info", ms: int = 5000) -> None:
        t = _Toast(text, kind, self)
        t.destroyed.connect(lambda *_: QTimer.singleShot(0, self._fit))
        self._lay.addWidget(t)
        t.show()
        self._fit()
        QTimer.singleShot(ms, lambda: self._fade(t))

    def _fade(self, t: QFrame) -> None:
        try:
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
