"""
ui/theme.py — the design system: color tokens, typography, and the application stylesheet.

Change a token here and the whole app follows. Widgets never hard-code colors; they use
`theme.color("name")` or the dynamic properties (role / variant / kind) that this stylesheet styles.
"""
from __future__ import annotations

from PyQt6.QtGui import QColor, QFont, QFontDatabase
from PyQt6.QtWidgets import QApplication

DARK = {
    "bg": "#0B0F17", "surface": "#131926", "surface_alt": "#1A2233", "surface_hover": "#202A3F",
    "border": "#263047", "border_strong": "#34405C",
    "text": "#E7ECF5", "text_muted": "#93A0B8", "text_faint": "#808BA1",
    "primary": "#5B7CFA", "primary_hover": "#7592FF", "primary_pressed": "#4A68DB", "on_primary": "#FFFFFF",
    # fills behind white text are a deeper blue than blue TEXT, so both reach 4.5:1 (WCAG AA)
    "primary_fill": "#4167F9", "primary_fill_hover": "#3A5DEB", "primary_fill_pressed": "#3150D2",
    "primary_soft": "#1C2748", "primary_on_soft": "#6C8AFB",   # blue text/icons on the soft background
    "success": "#34D399", "success_soft": "#0F2E27",
    "warning": "#F5B94E", "warning_soft": "#33280F",
    "danger": "#F26D6D", "danger_soft": "#361A1F",
    "info": "#60A5FA", "info_soft": "#12263D",
    "shadow": "#000000",
}

LIGHT = {
    "bg": "#F4F6FB", "surface": "#FFFFFF", "surface_alt": "#EEF2F9", "surface_hover": "#E5EBF6",
    "border": "#DCE3EF", "border_strong": "#C3CDE0",
    "text": "#161E30", "text_muted": "#4A556C", "text_faint": "#626D83",
    "primary": "#3159EF", "primary_hover": "#2A4DD8", "primary_pressed": "#2442BC", "on_primary": "#FFFFFF",
    "primary_fill": "#3159EF", "primary_fill_hover": "#2A4DD8", "primary_fill_pressed": "#2442BC",
    "primary_soft": "#E4EBFF", "primary_on_soft": "#3159EF",
    "success": "#0B7C56", "success_soft": "#DDF7EC",
    "warning": "#966319", "warning_soft": "#FFF3D6",
    "danger": "#C92C2C", "danger_soft": "#FDE8E8",
    "info": "#2B6CB0", "info_soft": "#E1EEFB",
    "shadow": "#1B2540",
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


def stylesheet() -> str:
    t = _state["tokens"]
    mono = mono_family()
    return f"""
* {{ outline: none; }}
QWidget {{ color: {t['text']}; background: transparent; }}
QMainWindow, QDialog, QWidget#Root {{ background: {t['bg']}; }}
QToolTip {{ background: {t['surface_alt']}; color: {t['text']}; border: 1px solid {t['border_strong']};
           padding: 6px 8px; border-radius: 6px; }}

/* ── typography ── */
QLabel[role="display"] {{ font-size: 28px; font-weight: 700; }}
QLabel[role="h1"] {{ font-size: 22px; font-weight: 700; }}
QLabel[role="h2"] {{ font-size: 16px; font-weight: 650; }}
QLabel[role="h3"] {{ font-size: 13px; font-weight: 650; color: {t['text_muted']}; }}
QLabel[role="body"] {{ font-size: 13px; }}
QLabel[role="muted"] {{ font-size: 12px; color: {t['text_muted']}; }}
QLabel[role="faint"] {{ font-size: 11px; color: {t['text_faint']}; }}
QLabel[role="eyebrow"] {{ font-size: 11px; font-weight: 700; color: {t['text_faint']}; letter-spacing: 1px; }}
QLabel[role="mono"] {{ font-family: "{mono}"; font-size: 12px; }}
QLabel[role="error"] {{ color: {t['danger']}; font-size: 12px; }}

/* ── surfaces ── */
QFrame#Card {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: {RADIUS}px; }}
QFrame#CardAlt {{ background: {t['surface_alt']}; border: 1px solid {t['border']}; border-radius: {RADIUS}px; }}
QFrame#Card[clickable="true"]:hover {{ border: 1px solid {t['primary']}; background: {t['surface_alt']}; }}
QFrame#Sidebar {{ background: {t['surface']}; border-right: 1px solid {t['border']}; }}
QFrame#StatusBar {{ background: {t['surface']}; border-top: 1px solid {t['border']}; }}
QFrame#Divider {{ background: {t['border']}; max-height: 1px; min-height: 1px; border: none; }}

/* ── buttons ── */
QPushButton {{ background: {t['surface_alt']}; color: {t['text']}; border: 1px solid {t['border']};
              border-radius: {RADIUS_SM}px; padding: 9px 16px; font-weight: 600; font-size: 13px; }}
QPushButton:hover {{ background: {t['surface_hover']}; border-color: {t['border_strong']}; }}
QPushButton:pressed {{ background: {t['border']}; }}
QPushButton:disabled {{ color: {t['text_faint']}; background: {t['surface']}; border-color: {t['border']}; }}
QPushButton[variant="primary"] {{ background: {t['primary_fill']}; color: {t['on_primary']}; border: 1px solid {t['primary_fill']}; }}
QPushButton[variant="primary"]:hover {{ background: {t['primary_fill_hover']}; border-color: {t['primary_fill_hover']}; }}
QPushButton[variant="primary"]:pressed {{ background: {t['primary_fill_pressed']}; }}
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

/* ── inputs ── */
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox {{
    background: {t['bg']}; border: 1px solid {t['border']}; border-radius: {RADIUS_SM}px;
    padding: 10px 12px; font-size: 13px; selection-background-color: {t['primary']}; }}
QLineEdit:focus, QTextEdit:focus, QComboBox:focus {{ border: 1px solid {t['primary']}; }}
QLineEdit:disabled {{ color: {t['text_faint']}; }}
QLineEdit[invalid="true"] {{ border: 1px solid {t['danger']}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox QAbstractItemView {{ background: {t['surface']}; border: 1px solid {t['border_strong']};
    selection-background-color: {t['primary_soft']}; selection-color: {t['text']}; padding: 4px; outline: none; }}

/* ── lists ── */
QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ border-radius: 10px; margin: 2px 0; padding: 0; }}
QListWidget::item:hover {{ background: {t['surface_alt']}; }}
QListWidget::item:selected {{ background: {t['primary_soft']}; }}

/* ── progress ── */
QProgressBar {{ background: {t['surface_alt']}; border: none; border-radius: 4px; max-height: 8px; min-height: 8px;
               text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {t['primary']}; border-radius: 4px; }}

/* ── scroll ── */
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t['border_strong']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {t['text_faint']}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; height: 0; }}

/* ── pills / badges ── */
QLabel[pill="neutral"] {{ background: {t['surface_alt']}; color: {t['text_muted']}; border: 1px solid transparent; border-radius: 10px; padding: 3px 10px; font-size: 11px; font-weight: 700; }}
QLabel[pill="success"] {{ background: {t['success_soft']}; color: {t['success']}; border: 1px solid transparent; border-radius: 10px; padding: 3px 10px; font-size: 11px; font-weight: 700; }}
QLabel[pill="warning"] {{ background: {t['warning_soft']}; color: {t['warning']}; border: 1px solid transparent; border-radius: 10px; padding: 3px 10px; font-size: 11px; font-weight: 700; }}
QLabel[pill="danger"]  {{ background: {t['danger_soft']};  color: {t['danger']};  border: 1px solid transparent; border-radius: 10px; padding: 3px 10px; font-size: 11px; font-weight: 700; }}
QLabel[pill="info"]    {{ background: {t['info_soft']};    color: {t['info']};    border: 1px solid transparent; border-radius: 10px; padding: 3px 10px; font-size: 11px; font-weight: 700; }}
QLabel[pill="primary"] {{ background: {t['primary_soft']}; color: {t['primary_on_soft']}; border: 1px solid transparent; border-radius: 10px; padding: 3px 10px; font-size: 11px; font-weight: 700; }}

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
QFrame#DropZone {{ background: {t['surface']}; border: 2px dashed {t['border_strong']}; border-radius: 16px; }}
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
