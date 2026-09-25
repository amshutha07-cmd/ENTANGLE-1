"""ui/main_window.py — the application shell: sign-in flow, sidebar, pages, status bar, toasts, tray."""
from __future__ import annotations

import logging
import os
from typing import Optional

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer
from PyQt6.QtGui import QIcon, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QMainWindow, QMenu, QPushButton, QStackedWidget, QSystemTrayIcon, QVBoxLayout,
    QWidget,
)

import engine
from ui import icons, motion, theme
from ui.controller import AppController, pref, set_pref
from ui.pages.auth import AuthPage
from ui.pages.contacts import ContactsPage
from ui.pages.home import HomePage
from ui.pages.inbox import InboxPage
from ui.pages.send import SendPage
from ui.pages.settings import SettingsPage
from ui.pages.vault import VaultPage
from ui.widgets import Avatar, Banner, Button, ClickablePill, IconBadge, NavButton, Pill, ToastHost, label

logger = logging.getLogger(__name__)
APP_VERSION = "1.0"

NAV = [("home", "Home", "home"), ("vault", "Protect", "shield"), ("send", "Send", "send"),
       ("inbox", "Inbox", "inbox"), ("contacts", "People & keys", "users"), ("settings", "Settings", "settings")]


class _ActivityWatcher(QObject):
    """Resets the idle timer on any real user input."""

    def __init__(self, on_activity, parent=None):
        super().__init__(parent)
        self._cb = on_activity

    def eventFilter(self, obj, ev):
        if ev.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.KeyPress, QEvent.Type.MouseMove, QEvent.Type.Wheel):
            self._cb()
        return False


class _FocusVisible(QObject):
    """
    Focus rings only while someone is using the keyboard (like the web's :focus-visible). Buttons never take focus from
    a click, but the first button can receive focus when the window opens, and a ring from earlier Tabbing would
    otherwise stay on screen while the mouse is used. A mouse click clears focus from buttons; Tab brings it back.
    """

    KEYBOARD = (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason, Qt.FocusReason.ShortcutFocusReason)

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QEvent.Type.MouseButtonPress:
            drop_button_focus()
        elif t == QEvent.Type.FocusIn and _is_button(obj) and ev.reason() not in self.KEYBOARD:
            # Focus landed on a button without Tab (a field it followed was hidden, a page changed, the window
            # opened): drop it on the next turn of the event loop, so no ring appears that nobody asked for.
            QTimer.singleShot(0, lambda o=obj: _clear_if_focused(o))
        return False


def _is_button(w) -> bool:
    from ui.widgets import ClickableCard
    return isinstance(w, (QPushButton, ClickableCard))


def _clear_if_focused(w) -> None:
    try:
        if QApplication.focusWidget() is w:
            w.clearFocus()
    except RuntimeError:                                   # deleted in the meantime
        pass


def drop_button_focus() -> None:
    w = QApplication.focusWidget()
    if _is_button(w):
        w.clearFocus()


class MainWindow(QMainWindow):
    def __init__(self, ctl: Optional[AppController] = None):
        super().__init__()
        self.ctl = ctl or AppController()
        self.setWindowTitle("A.N.Sx Vault")
        self.setAcceptDrops(True)                      # drop a file anywhere to protect it
        self.setMinimumSize(980, 680)
        self.resize(1200, 800)
        self._restore_window_place()

        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.root_stack = QStackedWidget()
        outer.addWidget(self.root_stack, 1)

        # ── sign-in flow ──
        self.auth = AuthPage(self.ctl)
        self.root_stack.addWidget(self.auth)

        # ── application shell ──
        shell = QWidget()
        shell_v = QVBoxLayout(shell)
        shell_v.setContentsMargins(0, 0, 0, 0)
        shell_v.setSpacing(0)
        body = QHBoxLayout()
        body.setSpacing(0)
        body.addWidget(self._build_sidebar())
        self.content = QStackedWidget()
        body.addWidget(self.content, 1)
        shell_v.addLayout(body, 1)
        shell_v.addWidget(self._build_status_bar())
        self.root_stack.addWidget(shell)

        self.pages = {
            "home": HomePage(self.ctl), "vault": VaultPage(self.ctl), "send": SendPage(self.ctl),
            "inbox": InboxPage(self.ctl), "contacts": ContactsPage(self.ctl), "settings": SettingsPage(self.ctl),
        }
        for page in self.pages.values():
            self.content.addWidget(page)
        self._wire()

        self.toasts = ToastHost(root)
        self._tray = self._make_tray()
        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.timeout.connect(self._idle_lock)
        self._idle_warn = QTimer(self)                 # a heads-up shortly before locking
        self._idle_warn.setSingleShot(True)
        self._idle_warn.timeout.connect(self._idle_warning)
        # App-wide event filters are children of this window, so Qt removes them when the window goes. Unowned, they
        # outlived it and kept calling into a deleted window on every click (a crash).
        self._watcher = _ActivityWatcher(self._activity, self)
        QApplication.instance().installEventFilter(self._watcher)
        self._focus_visible = _FocusVisible(self)
        QApplication.instance().installEventFilter(self._focus_visible)
        self._really_quit = False
        self._install_commands()
        QApplication.instance().applicationStateChanged.connect(self._app_state_changed)

        if self.ctl.operator:                       # attached to a session that is already running: adopt its state
            self._session_started(self.ctl.operator)
            self._set_waiting(len(self.ctl.inbox))
            self._relay_state(self.ctl.relay_state, self.ctl.relay_message)
        else:
            self.show_auth()

    # ── construction helpers ─────────────────────────────────────────────────
    def _build_sidebar(self) -> QFrame:
        side = QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(232)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(14, 20, 14, 14)
        lay.setSpacing(4)
        brand = QHBoxLayout()
        brand.setContentsMargins(6, 0, 0, 14)
        brand.addWidget(IconBadge("shield", "primary", 36))
        name = label("A.N.Sx Vault", "h2", wrap=False)
        brand.addWidget(name, 1)
        lay.addLayout(brand)
        self.nav: dict[str, NavButton] = {}
        for key, text, icon in NAV:
            b = NavButton(text, icon)
            b.clicked.connect(lambda _c, k=key: self.go(k))
            lay.addWidget(b)
            self.nav[key] = b
        lay.addStretch(1)
        chip = QFrame()
        chip.setObjectName("CardAlt")
        cl = QHBoxLayout(chip)
        cl.setContentsMargins(10, 10, 8, 10)
        cl.setSpacing(10)
        self.chip_avatar = Avatar("?", 34)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.chip_name = label("", "body", wrap=False)
        self.chip_name.setStyleSheet("font-weight: 650;")
        self.chip_state = label("Unlocked", "faint", wrap=False)
        col.addWidget(self.chip_name)
        col.addWidget(self.chip_state)
        lock = Button("", "ghost", "lock", "sm")
        lock.setToolTip(f"Lock now   {QKeySequence('Ctrl+L').toString(QKeySequence.SequenceFormat.NativeText)}")
        lock.clicked.connect(self._lock)
        cl.addWidget(self.chip_avatar)
        cl.addLayout(col, 1)
        cl.addWidget(lock)
        lay.addWidget(chip)
        return side

    def _build_status_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("StatusBar")
        bar.setFixedHeight(38)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(10)
        self.relay_pill = ClickablePill("Not connected", "neutral")
        self.storage_pill = ClickablePill("", "neutral")
        for pill in (self.relay_pill, self.storage_pill):          # a status you can see is a status you can fix
            pill.clicked.connect(lambda: self.root_stack.currentIndex() == 1 and self.go("settings"))
        self.engine_banner_pill = Pill("", "danger")
        self.engine_banner_pill.hide()
        self.work_pill = ClickablePill("", "info")           # "Sending to sam… 42%" while you are elsewhere
        self.work_pill.hide()
        self.work_pill.clicked.connect(self._open_work)
        self._work: dict[str, dict] = {}
        lay.addWidget(self.relay_pill)
        lay.addWidget(self.storage_pill)
        lay.addWidget(self.work_pill)
        lay.addWidget(self.engine_banner_pill)
        lay.addStretch(1)
        lay.addWidget(label(f"v{APP_VERSION}", "faint", wrap=False))
        return bar

    def _make_tray(self) -> Optional[QSystemTrayIcon]:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        tray = QSystemTrayIcon(icons.app_icon(), self)
        tray.setToolTip("A.N.Sx Vault")
        menu = QMenu(self)
        menu.addAction("Open A.N.Sx Vault", self.bring_to_front)
        self._tray_lock = menu.addAction("Lock now", self._lock)
        menu.addSeparator()
        menu.addAction("Quit", self.quit_app)             # goes through the "transfer still running?" check
        menu.aboutToShow.connect(self._tray_menu_opening)
        tray.setContextMenu(menu)
        self._tray_menu = menu
        tray.activated.connect(self._tray_clicked)
        tray.messageClicked.connect(self.bring_to_front)      # "New file from sam" clicked
        tray.show()
        return tray

    def _tray_menu_opening(self) -> None:
        self._tray_lock.setEnabled(self.root_stack.currentIndex() == 1)

    def _tray_clicked(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.bring_to_front()

    def bring_to_front(self) -> None:
        """Show the window and give it focus (tray click, or the app launched a second time)."""
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    # ── commands: one list, shown as a menu bar on macOS and as window shortcuts elsewhere ──
    def _commands(self) -> list:
        """(menu, label, shortcut, handler). Each shortcut is registered exactly once: a key bound twice does nothing."""
        cmds = [("File", "Protect a File…", "Ctrl+O", self._protect_shortcut),
                ("File", "Open a Package File…", "", self._open_package_command),
                ("File", "Lock", "Ctrl+L", self._lock)]
        for i, (key, text, _icon) in enumerate(NAV, start=1):
            cmds.append(("Go", text, f"Ctrl+{i}", lambda _c=False, k=key: self._shortcut(k)))
        cmds.append(("File", "Quit A.N.Sx Vault", "Ctrl+Q", self.quit_app))
        cmds += [("Help", "Keyboard Shortcuts", "", self._show_shortcuts),
                 ("Help", "Save a Support Report…", "", self._support_report),
                 ("Help", "About A.N.Sx Vault", "", self._about)]
        return cmds

    def _install_commands(self) -> None:
        native = QKeySequence.SequenceFormat.NativeText
        for i, (key, text, _icon) in enumerate(NAV, start=1):
            self.nav[key].setToolTip(f"{text}   {QKeySequence(f'Ctrl+{i}').toString(native)}")
        # The macOS global menu bar (where Mac users look for commands). Only on the real macOS display: elsewhere a
        # menu bar would be drawn inside the window.
        if QApplication.platformName() == "cocoa":
            bar = self.menuBar()
            menus: dict = {}
            for menu, label_text, keys, fn in self._commands():
                m = menus.get(menu) or menus.setdefault(menu, bar.addMenu(menu))
                act = m.addAction(label_text)
                if keys:
                    act.setShortcut(QKeySequence(keys))
                if label_text.startswith("About"):
                    act.setMenuRole(act.MenuRole.AboutRole)   # macOS moves it into the app menu
                elif label_text.startswith("Quit"):
                    act.setMenuRole(act.MenuRole.QuitRole)    # replaces the default ⌘Q, so it really quits
                act.triggered.connect(fn)
            self._menus = menus
        else:                                             # Windows / Linux: keep the window free of a menu bar
            for _menu, _label, keys, fn in self._commands():
                if keys:
                    QShortcut(QKeySequence(keys), self, activated=fn)

    def _open_package_command(self) -> None:
        if self.root_stack.currentIndex() == 1:
            self.go("inbox")
            self.pages["inbox"]._open_package()

    def _show_shortcuts(self) -> None:
        if self.root_stack.currentIndex() == 1:
            self.go("settings")
            page = self.pages["settings"]
            page.ensureWidgetVisible(page.shortcuts_card)

    def _support_report(self) -> None:
        self.bring_to_front()
        if self.root_stack.currentIndex() == 1:
            self.go("settings")
        self.pages["settings"].save_support_report()

    def _about(self) -> None:
        from ui import dialogs
        dialogs.info(self, "About A.N.Sx Vault",
                     f"Version {APP_VERSION}\n\nSend files that only the right person can open. Files are encrypted on "
                     "this computer, split into 12 pieces (any 8 rebuild them) and sealed for one person's key. "
                     "The relay and cloud storage only ever see encrypted pieces.")

    def _wire(self) -> None:
        ctl = self.ctl
        for key in ("home", "vault", "send", "inbox", "contacts", "settings"):
            page = self.pages[key]
            if hasattr(page, "navigate"):
                page.navigate.connect(self.go)
            if hasattr(page, "send_to_requested"):
                page.send_to_requested.connect(self._send_to)
        self.pages["vault"].send_requested.connect(self._send_entry)
        self.pages["settings"].theme_changed.connect(self.set_theme)
        self.pages["settings"].lock_requested.connect(self._lock)
        self.pages["settings"].autolock_changed.connect(lambda _m: self._activity())
        ctl.session_started.connect(self._session_started)
        ctl.session_ended.connect(self._session_ended)
        ctl.toast.connect(self.toasts_show)
        ctl.new_transfers.connect(self._new_transfers)
        ctl.inbox_changed.connect(self._inbox_changed)
        ctl.work_progress.connect(self._work_progress)
        ctl.work_done.connect(self._work_done)
        ctl.relay_state_changed.connect(self._relay_state)
        ctl.vault_changed.connect(self._storage_pill)

    # ── navigation ───────────────────────────────────────────────────────────
    def go(self, key: str) -> None:
        tab = None
        if key == "sent":
            key, tab = "inbox", "sent"
        page = self.pages[key]
        changed = self.content.currentWidget() is not page
        self.content.setCurrentWidget(page)
        if changed:
            motion.fade_in(page, motion.FAST)
            self._show_work()
        for k, b in self.nav.items():
            b.setChecked(k == key)
        page.on_show()
        if tab:
            page.show_tab(tab)
        elif key == "inbox":
            page.show_tab("received")

    # ── drop a file anywhere to protect it ───────────────────────────────────
    def _dropped_files(self, mime) -> list:
        """The files (not folders) in a drop, while signed in."""
        if self.root_stack.currentIndex() != 1 or not mime.hasUrls():
            return []
        return [u.toLocalFile() for u in mime.urls() if u.isLocalFile() and os.path.isfile(u.toLocalFile())]

    def dragEnterEvent(self, e) -> None:
        if self._dropped_files(e.mimeData()):
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        paths = self._dropped_files(e.mimeData())
        if not paths:
            return
        e.acceptProposedAction()
        send = self.pages["send"]
        if self.content.currentWidget() is send and len(paths) == 1:   # on Send: protect it and carry on sending
            if send._job is not None:
                self.toasts_show("Still working on the last file. Drop the next one when it has finished.", "warning")
            else:
                send.protect_and_continue(paths[0])
            return
        vault = self.pages["vault"]
        busy = vault._job is not None
        self.go("vault")
        vault.protect_files(paths)                          # several at once: protected one after another
        if busy:
            self.toasts_show(f"Added {len(paths)} file(s) to the queue.", "info")

    # ── reopen where it was ──────────────────────────────────────────────────
    def _save_window_place(self) -> None:
        g = self.normalGeometry() if self.isMaximized() else self.geometry()
        set_pref("window_place", {"x": g.x(), "y": g.y(), "w": g.width(), "h": g.height(), "max": self.isMaximized()})

    def _restore_window_place(self) -> None:
        """Back to last time's size and place, but only if that still fits on a screen (a monitor may be gone)."""
        from PyQt6.QtCore import QPoint, QRect
        from PyQt6.QtGui import QGuiApplication
        p = pref("window_place", None)
        if not isinstance(p, dict):
            return
        try:
            rect = QRect(int(p["x"]), int(p["y"]), int(p["w"]), int(p["h"]))
        except (KeyError, TypeError, ValueError):
            return
        screen = QGuiApplication.screenAt(rect.center()) or QGuiApplication.screenAt(QPoint(rect.x() + 40, rect.y() + 20))
        if screen is None or rect.width() < self.minimumWidth() or rect.height() < self.minimumHeight():
            return
        room = screen.availableGeometry()
        if rect.width() > room.width() or rect.height() > room.height():
            return
        self.setGeometry(rect)
        if p.get("max"):
            self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        QTimer.singleShot(0, drop_button_focus)        # Qt focuses the first button on open: no ring until Tab

    # ── work in progress, visible from any page ──────────────────────────────
    def _work_progress(self, key: str, page: str, label: str, pct: int) -> None:
        import time
        self._work[key] = {"page": page, "label": label, "pct": pct, "t": time.monotonic()}
        self._show_work()

    def _work_done(self, key: str) -> None:
        self._work.pop(key, None)
        self._show_work()

    def _show_work(self) -> None:
        here = self.content.currentWidget()
        away = [w for w in self._work.values() if self.pages.get(w["page"]) is not here]
        if not away or self.root_stack.currentIndex() != 1:
            self.work_pill.hide()
            motion.breathe(self.work_pill, False)
            return
        w = max(away, key=lambda x: x["t"])
        label = w["label"].rstrip("…. ")
        label = label if len(label) <= 42 else label[:41].rstrip() + "…"
        more = f"  (+{len(away) - 1} more)" if len(away) > 1 else ""
        self.work_pill.set(f"{label} {w['pct']}%{more}" if w["pct"] else f"{label}…{more}", "info")
        self.work_pill.setToolTip("Click to see it")
        self.work_pill._page = w["page"]
        if self.work_pill.isHidden():
            self.work_pill.show()
            motion.breathe(self.work_pill, True)

    def _open_work(self) -> None:
        page = getattr(self.work_pill, "_page", "")
        if page and self.root_stack.currentIndex() == 1:
            self.go(page)

    def _inbox_changed(self, items: list) -> None:
        self._set_waiting(len(items))

    def _set_waiting(self, n: int) -> None:
        """Inbox count on the sidebar badge and in the window title (seen in the taskbar / window list)."""
        self.nav["inbox"].set_badge(n)
        self.setWindowTitle(f"A.N.Sx Vault ({n} waiting)" if n else "A.N.Sx Vault")
        try:
            QApplication.instance().setBadgeNumber(n)         # the count on the Dock / taskbar icon (Qt 6.5+)
        except (AttributeError, RuntimeError):
            pass

    def _shortcut(self, key: str) -> None:
        if self.root_stack.currentIndex() == 1:
            self.go(key)

    def _protect_shortcut(self) -> None:
        """Ctrl/⌘+O: straight to choosing a file to protect."""
        if self.root_stack.currentIndex() == 1:
            self.go("vault")
            self.pages["vault"].drop.choose()

    def _send_to(self, name: str) -> None:
        self.pages["send"].send_to(name)
        self.go("send")

    def _send_entry(self, entry_id: str) -> None:
        self.pages["send"].preselect(entry_id)
        self.go("send")

    # ── session lifecycle ────────────────────────────────────────────────────
    def show_auth(self) -> None:
        self.root_stack.setCurrentIndex(0)
        self.auth.show_login()

    def _session_started(self, name: str) -> None:
        self.chip_name.setText(name)
        self.chip_avatar.set_name(name)
        self.root_stack.setCurrentIndex(1)
        motion.fade_in(self.root_stack.currentWidget(), motion.NORMAL)
        self._storage_pill()
        self._engine_check()
        self.go("home")
        self._activity()
        self.ctl.run_job(self.ctl.make_directory_job())          # quietly learn who is on the relay

    def _session_ended(self) -> None:
        self._idle.stop()
        self._idle_warn.stop()
        for page in self.pages.values():
            page.on_session_ended()
        self._set_waiting(0)
        self.show_auth()

    def _lock(self) -> None:
        if self.root_stack.currentIndex() == 1:
            self.ctl.logout()

    IDLE_WARNING_S = 30

    def _idle_lock(self) -> None:
        if not self.ctl.operator:
            return
        if self.ctl.busy():
            # Locking cancels running jobs: never cut off a transfer someone walked away from. Check again shortly.
            self._idle.start(60 * 1000)
            return
        self.ctl.logout()
        self.toasts_show("Locked after a period of inactivity.", "info")

    def _idle_warning(self) -> None:
        if self.ctl.operator and not self.ctl.busy():
            self.toasts.show_toast(f"Locking in {self.IDLE_WARNING_S} seconds because you've been away. "
                                   "Move the mouse or press a key to stay unlocked.", "warning", 10000)

    def _activity(self) -> None:
        if not self.ctl.operator:
            return
        mins = int(pref("auto_lock_minutes", 10) or 0)
        if mins > 0:
            self._idle.start(mins * 60 * 1000)
            if mins * 60 > self.IDLE_WARNING_S * 2:
                self._idle_warn.start((mins * 60 - self.IDLE_WARNING_S) * 1000)
        else:
            self._idle.stop()
            self._idle_warn.stop()

    # ── status / feedback ────────────────────────────────────────────────────
    def toasts_show(self, message: str, kind: str = "info") -> None:
        self.toasts.show_toast(message, kind)
        # Something happened while the app was in the background (e.g. "sam received your file"): tell the system too.
        if self._tray is not None and not self.isActiveWindow() and kind in ("success", "warning", "error"):
            icon = {"success": QSystemTrayIcon.MessageIcon.Information, "warning": QSystemTrayIcon.MessageIcon.Warning,
                    "error": QSystemTrayIcon.MessageIcon.Critical}[kind]
            self._tray.showMessage("A.N.Sx Vault", message, icon, 6000)

    def _relay_state(self, state: str, message: str) -> None:
        text, kind = {"online": ("Online", "success"), "connecting": ("Connecting…", "info"),
                      "offline": ("Offline", "warning"), "conflict": ("Name conflict", "danger"),
                      "idle": ("Not connected", "neutral")}.get(state, (state, "neutral"))
        self.relay_pill.set(f"● {text}" if state != "idle" else text, kind)
        self.relay_pill.setToolTip(f"{message}\nClick to open connection settings." if message
                                   else "Click to open connection settings.")
        motion.breathe(self.relay_pill, state == "connecting")

    def _storage_pill(self) -> None:
        n = len(self.ctl.storage_targets())
        self.storage_pill.set(f"Cloud storage: {n}" if n else "Storage: inside package", "success" if n else "neutral")
        self.storage_pill.setToolTip("Click to manage cloud storage.")

    def _engine_check(self) -> None:
        ok = engine.available()
        self.engine_banner_pill.setVisible(not ok)
        if not ok:
            self.engine_banner_pill.set("Encryption engine missing — run build_engine.py", "danger")

    def _new_transfers(self, items: list) -> None:
        names = ", ".join(sorted({i["from"] for i in items}))
        self.toasts.show_toast(f"New file from {names}. Click here to open your inbox.", "info", 7000,
                               on_click=lambda: self.root_stack.currentIndex() == 1 and self.go("inbox"))
        QApplication.alert(self)
        if self._tray and not self.isActiveWindow():
            self._tray.showMessage("A.N.Sx Vault", f"New file from {names}", QSystemTrayIcon.MessageIcon.Information, 6000)

    # ── theme ────────────────────────────────────────────────────────────────
    def set_theme(self, name: str) -> None:
        theme.apply(QApplication.instance(), name)
        set_pref("theme", name)
        self.retheme()

    def retheme(self) -> None:
        for cls in (IconBadge,):
            for w in self.findChildren(cls):
                w.refresh()
        for w in self.findChildren(Button):
            w.refresh_icon()
        for w in self.findChildren(NavButton):
            w.refresh_icon()
        for w in self.findChildren(Banner):
            w.refresh()
        from ui.art import Illustration
        for w in self.findChildren(Illustration):         # background circle and shadow follow the theme
            w.retheme()
        if self._tray:
            self._tray.setIcon(QIcon(icons.pixmap("shield", theme.color("primary"), 32)))
        page = self.content.currentWidget()
        if self.root_stack.currentIndex() == 1 and page is not None:
            page.on_show()

    # ── shutdown ─────────────────────────────────────────────────────────────
    # ── closing the window: keep running in the tray, or really quit ─────────
    def _keep_running(self, event) -> bool:
        """Close button pressed (a spontaneous close) with a tray available and the setting on: hide, don't quit."""
        return (not self._really_quit and self._tray is not None and bool(pref("close_to_tray", True))
                and event.spontaneous())

    def quit_app(self) -> None:
        """Really quit (tray menu, ⌘Q / Ctrl+Q): locks, stops receiving, closes."""
        self._really_quit = True
        self.show()                                      # a hidden window must be closable to finish quitting
        if not self.close():
            self._really_quit = False                    # "Keep working" chosen while a transfer runs

    def _into_background(self) -> None:
        self._save_window_place()
        self.hide()
        if not pref("tray_hint_shown", False) and self._tray is not None:
            set_pref("tray_hint_shown", True)
            self._tray.showMessage(
                "A.N.Sx Vault is still running",
                "It keeps receiving and tells you when a file arrives (until it locks). Click the icon to open it, "
                "or choose Quit from its menu.", QSystemTrayIcon.MessageIcon.Information, 8000)

    def _app_state_changed(self, state) -> None:
        """macOS: clicking the Dock icon while the window is hidden brings it back."""
        if state == Qt.ApplicationState.ApplicationActive and self.isHidden() and not self._really_quit:
            self.bring_to_front()

    def closeEvent(self, event) -> None:
        if self._keep_running(event):
            event.ignore()
            self._into_background()
            return
        if self.ctl.busy() and self.isVisible():
            from ui import dialogs
            if not dialogs.confirm(self, "Quit while a transfer is running?",
                                   "A file is still being protected, sent or received. Quitting now cancels it.",
                                   ok="Quit anyway", danger=True, cancel="Keep working"):
                event.ignore()
                return
        self._save_window_place()
        try:
            self.ctl.logout()
        finally:
            if self._tray:
                self._tray.hide()
            super().closeEvent(event)
