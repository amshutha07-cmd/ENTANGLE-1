"""
ui/theme.py — the design system: color tokens, typography, and the application stylesheet.

Change a token here and the whole app follows. Widgets never hard-code colors; they use
`theme.color("name")` or the dynamic properties (role / variant / kind) that this stylesheet styles.

The look is the app's original "neon vault": near-black glass, neon mint and violet, monospace headings. The light
theme keeps the same identity (deep mint, violet) on a bright background.
"""
from __future__ import annotations

from PyQt6.QtGui import QColor, QFont, QFontDatabase
from PyQt6.QtWidgets import QApplication

DARK = {
    "bg": "#08090E", "surface": "#10121A", "surface_top": "#151823", "surface_alt": "#171A25", "surface_hover": "#1E2230", "sidebar": "#0B0C12",
    "border": "#222838", "border_strong": "#30384F",
    "text": "#E8ECF5", "text_muted": "#8F9AB8", "text_faint": "#8590B0",
    # neon mint: text, icons, focus rings and the active item
    "primary": "#00FFCC", "primary_hover": "#66FFE0", "primary_pressed": "#00D9AE", "on_primary": "#03130F",
    # primary buttons are a mint → cyan gradient with dark text on it
    "primary_fill": "#00F5C4", "primary_fill_hover": "#5CFFDC", "primary_fill_pressed": "#00C9A2",
    "primary_fill_end": "#00CFFF", "primary_fill_end_hover": "#4DDCFF", "primary_fill_end_pressed": "#00A6CC",
    "primary_soft": "#0B2A25", "primary_on_soft": "#00FFCC",   # mint text/icons on the soft background
    "primary_line": "#0F5B4E",                                 # a thin neon edge (hover, selection)
    "accent": "#B14CFF",                                       # violet: gradients and glow only, never text
    "glow": "#1A1036", "glow_2": "#051F1B",                    # the light behind the window
    "success": "#39FF14", "success_soft": "#0B240A",
    "warning": "#FFC400", "warning_soft": "#2A2106",
    "danger": "#FF3D6E", "danger_soft": "#2C0D17",
    "info": "#C08CFF", "info_soft": "#1D1431",
    "shadow": "#000000",
}

LIGHT = {
    "bg": "#F5F6FA", "surface": "#FFFFFF", "surface_top": "#FFFFFF", "surface_alt": "#F0F1F7", "surface_hover": "#E6E8F2", "sidebar": "#FFFFFF",
    "border": "#E0E3ED", "border_strong": "#C7CCDD",
    "text": "#12141F", "text_muted": "#4A5068", "text_faint": "#62687F",
    "primary": "#007A66", "primary_hover": "#006655", "primary_pressed": "#005446", "on_primary": "#FFFFFF",
    "primary_fill": "#007D68", "primary_fill_hover": "#006B59", "primary_fill_pressed": "#005A4B",
    "primary_fill_end": "#006F99", "primary_fill_end_hover": "#005F85", "primary_fill_end_pressed": "#004F70",
    "primary_soft": "#D3F3EA", "primary_on_soft": "#006B59",
    "primary_line": "#8FD9C9",
    "accent": "#7C3AED",
    "glow": "#ECE3FF", "glow_2": "#DDF6EF",
    "success": "#237A12", "success_soft": "#E2F7DA",
    "warning": "#8F5F00", "warning_soft": "#FFF2D1",
    "danger": "#C4163E", "danger_soft": "#FDE6EC",
    "info": "#6D28D9", "info_soft": "#EFE6FD",
    "shadow": "#1B2540",
}

# Avatar colors, one per name (same position in both lists, so a person keeps "their" color), and the ink on them.
AVATARS = {
    "dark": (["#00FFCC", "#B14CFF", "#FF4FD8", "#FFC400", "#39FF14", "#00D4FF", "#FF4D6D", "#5C8CFF"], "#07080D"),
    "light": (["#00806B", "#7B2FE0", "#C2188E", "#9A6700", "#2F7D0E", "#006FA3", "#C4203F", "#2C55D6"], "#FFFFFF"),
}

_state = {"name": "dark", "tokens": DARK}

FONT_STACK = ["Inter", "Segoe UI Variable Text", "Segoe UI", "SF Pro Text", ".AppleSystemUIFont",
              "Helvetica Neue", "Ubuntu", "Roboto", "Noto Sans", "Arial"]
MONO_STACK = ["JetBrains Mono", "SF Mono", "Menlo", "Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Courier New"]

RADIUS = 12
RADIUS_SM = 8


def current() -> str:
    return _state["name"]


def color(name: str) -> str:
    return _state["tokens"][name]


def qcolor(name: str) -> QColor:
    return QColor(color(name))


def rgba(name: str, alpha: float) -> str:
    """A token as a stylesheet rgba() color with the given opacity (0…1)."""
    c = qcolor(name)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {round(alpha * 255)})"


def avatar_colors() -> tuple[list[str], str]:
    """(fill colors, ink color) for initials badges in the current theme."""
    return AVATARS["dark" if _state["name"] == "dark" else "light"]


def pick_font(candidates: list[str]) -> str:
    families = set(QFontDatabase.families())
    for c in candidates:
        if c in families:
            return c
    return candidates[-1]


def base_font() -> QFont:
    f = QFont(pick_font(FONT_STACK))
    f.setPointSizeF(10.5)
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return f


def mono_family() -> str:
    return pick_font(MONO_STACK)


def _check_mark(ink: str = "#FFFFFF") -> str:
    """A check mark image for ticked checkboxes (stylesheets can only draw it from a file), in the given color."""
    import os
    import tempfile
    name = f"check-{ink.lstrip('#').lower()}.png"
    path = os.path.join(tempfile.gettempdir(), f"ansx_ui_{os.getuid() if hasattr(os, 'getuid') else 'u'}", name)
    if not os.path.exists(path):
        try:
            from ui import icons
            os.makedirs(os.path.dirname(path), exist_ok=True)
            icons.pixmap("check", ink, 14, 3.0).save(path, "PNG")
        except Exception:
            return ""
    return path.replace("\\", "/")


def _dot_grid(ink: str, alpha: int) -> str:
    """A faint dot grid tile (the ledger-paper texture behind the pages), as an image file for the stylesheet."""
    import os
    import tempfile
    name = f"grid-{ink.lstrip('#').lower()}-{alpha}.png"
    path = os.path.join(tempfile.gettempdir(), f"ansx_ui_{os.getuid() if hasattr(os, 'getuid') else 'u'}", name)
    if not os.path.exists(path):
        try:
            from PyQt6.QtGui import QImage
            os.makedirs(os.path.dirname(path), exist_ok=True)
            img = QImage(24, 24, QImage.Format.Format_ARGB32)
            img.fill(QColor(0, 0, 0, 0))
            dot = QColor(ink)
            dot.setAlpha(alpha)
            img.setPixelColor(0, 0, dot)
            img.save(path, "PNG")
        except Exception:
            return ""
    return path.replace("\\", "/")


def _grad(a: str, b: str) -> str:
    """A diagonal two-stop gradient, for filled buttons."""
    return f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {a}, stop:1 {b})"


def stylesheet() -> str:
    t = _state["tokens"]
    mono = mono_family()
    check = _check_mark(t["on_primary"])
    clear = rgba("bg", 0)
    grid = _dot_grid(t["text"], 34 if _state["name"] == "dark" else 40)
    pill = f'border: 1px solid transparent; border-radius: 10px; padding: 3px 10px; font-family: "{mono}"; ' \
           f'font-size: 11px; font-weight: 700;'
    return f"""
* {{ outline: none; }}
QWidget {{ color: {t['text']}; background: transparent; }}
QDialog, QWidget#Root {{ background: {t['bg']}; }}
/* the window: a violet light in the top corner and a mint one low down; pages inside let it through */
QMainWindow {{ background: qradialgradient(cx:0.55, cy:1.15, radius:0.75, fx:0.55, fy:1.15,
                                        stop:0 {t['glow_2']}, stop:1 {t['bg']}); }}
QMainWindow > QWidget#Root {{ background-color: qradialgradient(cx:1.0, cy:-0.1, radius:0.8, fx:1.0, fy:-0.1,
                                        stop:0 {t['glow']}, stop:1 {clear});
                               background-image: url("{grid}"); background-repeat: repeat-xy; }}
QWidget#Root QWidget#Root {{ background: transparent; }}
QToolTip {{ background: {t['surface_alt']}; color: {t['text']}; border: 1px solid {t['primary_line']};
           padding: 6px 8px; border-radius: 6px; }}

/* ── typography: monospace headings, the vault's terminal voice ── */
QLabel[role="display"] {{ font-family: "{mono}"; font-size: 28px; font-weight: 800; color: {t['primary']}; letter-spacing: 1px; }}
QLabel[role="h1"] {{ font-family: "{mono}"; font-size: 22px; font-weight: 700; }}
QLabel[role="h2"] {{ font-size: 16px; font-weight: 650; }}
QLabel[role="h3"] {{ font-size: 13px; font-weight: 650; color: {t['text_muted']}; }}
QLabel[role="body"] {{ font-size: 13px; }}
QLabel[role="muted"] {{ font-size: 12px; color: {t['text_muted']}; }}
QLabel[role="faint"] {{ font-size: 11px; color: {t['text_faint']}; }}
QLabel[role="eyebrow"] {{ font-family: "{mono}"; font-size: 11px; font-weight: 700; color: {t['text_faint']}; letter-spacing: 2px; }}
QLabel[role="mono"] {{ font-family: "{mono}"; font-size: 12px; color: {t['primary']}; }}
QLabel[role="error"] {{ color: {t['danger']}; font-size: 12px; }}
QLabel#Brand {{ font-family: "{mono}"; font-size: 15px; font-weight: 800; color: {t['primary']}; letter-spacing: 1px; }}

/* ── surfaces ── */
QFrame#Card {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {t['surface_top']}, stop:0.4 {t['surface']});
              border: 1px solid {t['border']}; border-radius: {RADIUS}px; }}
QFrame#CardAlt {{ background: {t['surface_alt']}; border: 1px solid {t['border']}; border-radius: {RADIUS}px; }}
QFrame#Card[clickable="true"]:hover {{ border: 1px solid {t['primary']}; background: {t['surface_alt']}; }}
QFrame#Sidebar {{ background: {t['sidebar']}; border-right: 1px solid {t['border']}; }}
QFrame#StatusBar {{ background: {t['sidebar']}; border-top: 1px solid {t['border']}; }}
QFrame#Divider {{ background: {t['border']}; max-height: 1px; min-height: 1px; border: none; }}

/* ── buttons ── */
QPushButton {{ background: {t['surface_alt']}; color: {t['text']}; border: 1px solid {t['border']};
              border-radius: {RADIUS_SM}px; padding: 9px 16px; font-weight: 600; font-size: 13px; }}
QPushButton:hover {{ background: {t['surface_hover']}; border-color: {t['primary_line']}; }}
QPushButton:pressed {{ background: {t['border']}; }}
QPushButton:disabled {{ color: {t['text_faint']}; background: {t['surface']}; border-color: {t['border']}; }}
QPushButton[variant="primary"] {{ background: {_grad(t['primary_fill'], t['primary_fill_end'])}; color: {t['on_primary']};
                                 border: 1px solid transparent; font-weight: 700; }}
QPushButton[variant="primary"]:hover {{ background: {_grad(t['primary_fill_hover'], t['primary_fill_end_hover'])}; }}
QPushButton[variant="primary"]:pressed {{ background: {_grad(t['primary_fill_pressed'], t['primary_fill_end_pressed'])}; }}
QPushButton[variant="primary"]:disabled {{ background: {t['surface_alt']}; color: {t['text_faint']}; border-color: {t['border']}; }}
QPushButton[variant="ghost"] {{ background: transparent; border: 1px solid transparent; color: {t['text_muted']}; }}
QPushButton[variant="ghost"]:hover {{ background: {t['surface_alt']}; color: {t['text']}; }}
QPushButton[variant="danger"] {{ background: {t['danger_soft']}; color: {t['danger']}; border: 1px solid {t['danger']}; }}
QPushButton[variant="danger"]:hover {{ background: {t['danger']}; color: {t['on_primary']}; }}
QPushButton[size="lg"] {{ padding: 13px 22px; font-size: 14px; border-radius: 10px; }}
QPushButton[size="sm"] {{ padding: 6px 12px; font-size: 12px; }}
QPushButton[nav="true"] {{ text-align: left; background: transparent; border: none; color: {t['text_muted']};
                          padding: 11px 14px; border-radius: 10px; font-size: 13px; font-weight: 600; }}
QPushButton[nav="true"]:hover {{ background: {t['surface_alt']}; color: {t['text']}; }}
QPushButton[nav="true"]:checked {{ background: {t['primary_soft']}; color: {t['primary_on_soft']}; }}
QPushButton:focus {{ border-color: {t['primary']}; }}
QPushButton[variant="primary"]:focus, QPushButton[variant="danger"]:focus {{ border-color: {t['text']}; }}
QPushButton[variant="ghost"]:focus {{ border-color: {t['primary']}; color: {t['text']}; }}
QPushButton[nav="true"]:focus {{ border: 1px solid {t['primary']}; padding: 10px 13px; }}
QFrame#Card[clickable="true"]:focus {{ border: 1px solid {t['primary']}; }}
QCheckBox {{ spacing: 10px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid {t['border_strong']};
                       background: {t['surface_alt']}; }}
QCheckBox::indicator:hover {{ border-color: {t['primary']}; }}
QCheckBox::indicator:checked {{ background: {t['primary_fill']}; border-color: {t['primary_fill']}; image: url("{check}"); }}
QCheckBox:focus {{ color: {t['text']}; }}

/* ── inputs ── */
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox {{
    background: {t['bg']}; border: 1px solid {t['border']}; border-radius: {RADIUS_SM}px;
    padding: 10px 12px; font-size: 13px; selection-background-color: {t['primary']}; selection-color: {t['on_primary']}; }}
QLineEdit:hover, QComboBox:hover {{ border-color: {t['border_strong']}; }}
QLineEdit:focus, QTextEdit:focus, QComboBox:focus {{ border: 1px solid {t['primary']}; }}
QLineEdit:disabled {{ color: {t['text_faint']}; }}
QLineEdit[invalid="true"] {{ border: 1px solid {t['danger']}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox QAbstractItemView {{ background: {t['surface']}; border: 1px solid {t['border_strong']};
    selection-background-color: {t['primary_soft']}; selection-color: {t['text']}; padding: 4px; outline: none; }}

/* ── lists ── */
QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ border-radius: 10px; margin: 2px 0; padding: 0; border: 1px solid transparent; }}
QListWidget::item:hover {{ background: {t['surface_alt']}; }}
QListWidget::item:selected {{ background: {t['primary_soft']}; border: 1px solid {t['primary_line']}; }}

/* ── progress: violet into mint ── */
QProgressBar {{ background: {t['surface_alt']}; border: none; border-radius: 4px; max-height: 8px; min-height: 8px;
               text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {t['accent']}, stop:1 {t['primary']});
                      border-radius: 4px; }}

/* ── scroll ── */
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t['border_strong']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {t['primary_line']}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; height: 0; }}

/* ── pills / badges: status readouts in monospace ── */
QLabel[pill="neutral"] {{ background: {t['surface_alt']}; color: {t['text_muted']}; {pill} }}
QLabel[pill="success"] {{ background: {t['success_soft']}; color: {t['success']}; {pill} }}
QLabel[pill="warning"] {{ background: {t['warning_soft']}; color: {t['warning']}; {pill} }}
QLabel[pill="danger"]  {{ background: {t['danger_soft']};  color: {t['danger']};  {pill} }}
QLabel[pill="info"]    {{ background: {t['info_soft']};    color: {t['info']};    {pill} }}
QLabel[pill="primary"] {{ background: {t['primary_soft']}; color: {t['primary_on_soft']}; {pill} }}

/* ── banners ── */
QFrame#Banner[kind="info"]    {{ background: {t['info_soft']};    border: 1px solid {t['info']};    border-radius: {RADIUS_SM}px; }}
QFrame#Banner[kind="warning"] {{ background: {t['warning_soft']}; border: 1px solid {t['warning']}; border-radius: {RADIUS_SM}px; }}
QFrame#Banner[kind="danger"]  {{ background: {t['danger_soft']};  border: 1px solid {t['danger']};  border-radius: {RADIUS_SM}px; }}
QFrame#Banner[kind="success"] {{ background: {t['success_soft']}; border: 1px solid {t['success']}; border-radius: {RADIUS_SM}px; }}

/* ── toasts ── */
QFrame#Toast {{ background: {t['surface_alt']}; border: 1px solid {t['border_strong']}; border-radius: 10px; }}
QFrame#Toast[kind="success"] {{ border-left: 4px solid {t['success']}; }}
QFrame#Toast[kind="warning"] {{ border-left: 4px solid {t['warning']}; }}
QFrame#Toast[kind="error"]   {{ border-left: 4px solid {t['danger']}; }}
QFrame#Toast[kind="info"]    {{ border-left: 4px solid {t['info']}; }}

/* ── drop zone ── */
QFrame#DropZone {{ background: {t['surface']}; border: 2px dashed {t['primary_line']}; border-radius: 16px; }}
QFrame#DropZone[active="true"] {{ background: {t['primary_soft']}; border: 2px dashed {t['primary']}; }}

QMessageBox {{ background: {t['surface']}; }}
"""


def apply(app: QApplication, name: str = "dark") -> None:
    _state["name"] = name
    _state["tokens"] = DARK if name == "dark" else LIGHT
    app.setFont(base_font())
    app.setStyleSheet(stylesheet())
    from ui import icons
    icons.clear_cache()


def toggle(app: QApplication) -> str:
    apply(app, "light" if _state["name"] == "dark" else "dark")
    return _state["name"]
