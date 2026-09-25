"""
ui/icons.py — a small vector icon set (24x24, stroke-based) rendered with QtSvg, so icons are crisp on
any screen and any OS and follow the theme color. No image files, no icon fonts, no emoji.
"""
from __future__ import annotations

from PyQt6.QtCore import QByteArray, Qt
from PyQt6.QtGui import QGuiApplication, QIcon, QImage, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

_P = {
    "shield": '<path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6z"/><path d="M9 12l2 2 4-4"/>',
    "lock": '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 018 0v3"/>',
    "unlock": '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 017.5-2"/>',
    "send": '<path d="M21 3L10 14"/><path d="M21 3l-7 18-4-7-7-4z"/>',
    "inbox": '<path d="M3 13l3-8h12l3 8"/><path d="M3 13v6h18v-6h-5l-1.5 2h-5L8 13z"/>',
    "home": '<path d="M4 11l8-7 8 7"/><path d="M6 10v10h12V10"/><path d="M10 20v-6h4v6"/>',
    "users": '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.6-3.5 3.2-5.5 6.5-5.5s5.9 2 6.5 5.5"/><path d="M16 4.6a3.5 3.5 0 010 6.8"/><path d="M18 14.7c2 .7 3.2 2.5 3.5 5.3"/>',
    "settings": '<path d="M4 7h9"/><path d="M17 7h3"/><circle cx="15" cy="7" r="2"/><path d="M4 17h3"/><path d="M11 17h9"/><circle cx="9" cy="17" r="2"/>',
    "file": '<path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5"/>',
    "folder": '<path d="M3 6a1 1 0 011-1h5l2 2h8a1 1 0 011 1v10a1 1 0 01-1 1H4a1 1 0 01-1-1z"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "x": '<path d="M6 6l12 12"/><path d="M18 6L6 18"/>',
    "alert": '<path d="M12 4l9 16H3z"/><path d="M12 10v4.5"/><path d="M12 17.3v.2"/>',
    "cloud": '<path d="M7 18a4.5 4.5 0 01-.6-8.96A6 6 0 0117.9 8.6 4.7 4.7 0 0117 18z"/>',
    "wifi": '<path d="M3 9a13 13 0 0118 0"/><path d="M6 12.5a8.5 8.5 0 0112 0"/><path d="M9 16a4 4 0 016 0"/><path d="M12 19.5v.2"/>',
    "refresh": '<path d="M20 5v5h-5"/><path d="M4 19v-5h5"/><path d="M6.3 9A7 7 0 0118.6 10"/><path d="M17.7 15A7 7 0 015.4 14"/>',
    "plus": '<path d="M12 5v14"/><path d="M5 12h14"/>',
    "trash": '<path d="M4 7h16"/><path d="M9 7V4h6v3"/><path d="M6 7l1 13h10l1-13"/>',
    "copy": '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 012-2h9"/>',
    "download": '<path d="M12 4v11"/><path d="M7 11l5 5 5-5"/><path d="M4 20h16"/>',
    "upload": '<path d="M12 16V5"/><path d="M7 9l5-5 5 5"/><path d="M4 20h16"/>',
    "key": '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9"/><path d="M17 6l3 3"/><path d="M14 9l2 2"/>',
    "card": '<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M3 10h18"/><path d="M7 15h4"/>',
    "nfc": '<path d="M8 6a8 8 0 010 12"/><path d="M12 8.5a4.5 4.5 0 010 7"/><path d="M16 11a1 1 0 010 2"/><path d="M5 4a12 12 0 010 16"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 7.7v.2"/>',
    "search": '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.2-4.2"/>',
    "chevron": '<path d="M9 6l6 6-6 6"/>',
    "back": '<path d="M15 6l-6 6 6 6"/>',
    "eye_off": '<path d="M3 3l18 18"/><path d="M10.6 5.1A10.7 10.7 0 0112 5c6.5 0 10 7 10 7a17.6 17.6 0 01-3.2 4.2"/><path d="M6.6 6.6C3.8 8.4 2 12 2 12s3.5 7 10 7a9.6 9.6 0 005.4-1.6"/><path d="M9.9 9.9a3 3 0 004.2 4.2"/>',
    "eye": '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    "logout": '<path d="M10 4H5v16h5"/><path d="M15 8l4 4-4 4"/><path d="M19 12H9"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6L7 7M17 17l1.4 1.4M5.6 18.4L7 17M17 7l1.4-1.4"/>',
    "moon": '<path d="M20 14.5A8 8 0 019.5 4 8 8 0 1020 14.5z"/>',
    "activity": '<path d="M3 12h4l3-7 4 14 3-7h4"/>',
    "package": '<path d="M12 3l8 4v10l-8 4-8-4V7z"/><path d="M4 7l8 4 8-4"/><path d="M12 11v10"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "doc": '<path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5"/><path d="M10 13h6"/><path d="M10 17h4"/>',
    "image": '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 16l-5-5-9 9"/>',
    "sheet": '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M4 9h16"/><path d="M4 15h16"/><path d="M10 3v18"/>',
    "archive": '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M12 3v2M12 7v2M12 11v2"/><rect x="10.5" y="14" width="3" height="4" rx="1"/>',
    "media": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M10 9l5 3-5 3z"/>',
    "audio": '<path d="M9 18V5l11-2v13"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="17.5" cy="16" r="2.5"/>',
    "code": '<path d="M8 8l-4 4 4 4"/><path d="M16 8l4 4-4 4"/><path d="M13.5 5l-3 14"/>',
    "link": '<path d="M10 14a4 4 0 005.7 0l3-3a4 4 0 00-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 00-5.7 0l-3 3a4 4 0 005.7 5.7l1-1"/>',
}

_cache: dict[tuple, QIcon] = {}


def clear_cache() -> None:
    _cache.clear()


def available() -> list[str]:
    return sorted(_P)


def pixmap(name: str, color: str, size: int = 20, stroke: float = 1.9) -> QPixmap:
    body = _P.get(name, _P["info"])
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
           f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')
    ratio = 2.0
    app = QGuiApplication.instance()
    if app is not None:
        ratio = max(2.0, float(app.primaryScreen().devicePixelRatio())) if app.primaryScreen() else 2.0
    px = int(size * ratio)
    img = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(ratio)
    return pm


def app_icon() -> QIcon:
    """
    The application's own icon (Dock, taskbar, window title, tray): a neon mint-to-violet shield on a dark glass tile,
    drawn at every size a desktop asks for. Brand colours are fixed, not themed, so the icon looks the same everywhere.
    """
    from PyQt6.QtCore import QRectF
    from PyQt6.QtGui import QColor, QLinearGradient, QPen
    ic = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256, 512, 1024):
        img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        inset = size * 0.06                               # the margin macOS icons conventionally keep
        box = QRectF(inset, inset, size - 2 * inset, size - 2 * inset)
        tile = QLinearGradient(box.topLeft(), box.bottomRight())
        tile.setColorAt(0.0, QColor("#1E1840"))           # a violet sheen in the corner, into near-black
        tile.setColorAt(0.55, QColor("#0C0E18"))
        tile.setColorAt(1.0, QColor("#07130F"))
        edge = QLinearGradient(box.topLeft(), box.bottomRight())
        edge.setColorAt(0.0, QColor(0, 255, 204, 150))
        edge.setColorAt(1.0, QColor(177, 76, 255, 150))
        stroke = max(1.0, size * 0.018)
        p.setPen(QPen(edge, stroke) if size >= 32 else Qt.PenStyle.NoPen)
        p.setBrush(tile)
        half = stroke / 2
        p.drawRoundedRect(box.adjusted(half, half, -half, -half), box.width() * 0.23, box.height() * 0.23)
        glyph = size * 0.58
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="url(#neon)" '
               f'stroke-width="{2.1 if size >= 64 else 2.5}" stroke-linecap="round" stroke-linejoin="round">'
               f'<defs><linearGradient id="neon" x1="5" y1="3" x2="19" y2="21" gradientUnits="userSpaceOnUse">'
               f'<stop offset="0" stop-color="#00FFCC"/><stop offset="1" stop-color="#B14CFF"/></linearGradient></defs>'
               f'{_P["shield"]}</svg>')
        QSvgRenderer(QByteArray(svg.encode())).render(
            p, QRectF((size - glyph) / 2, (size - glyph) / 2 + size * 0.01, glyph, glyph))
        p.end()
        ic.addPixmap(QPixmap.fromImage(img))
    return ic


def icon(name: str, color: str, size: int = 20) -> QIcon:
    key = (name, color, size)
    if key not in _cache:
        _cache[key] = QIcon(pixmap(name, color, size))
    return _cache[key]
