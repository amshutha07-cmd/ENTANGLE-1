"""Small UI helpers: friendly dates, file-type icons, the sidebar count badge, and the contacts empty state."""
import datetime

import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtWidgets import QApplication  # noqa: E402

from ui import icons  # noqa: E402
from ui.widgets import EmptyState, NavButton, file_icon, friendly_date  # noqa: E402

app = QApplication.instance() or QApplication([])


def test_friendly_dates():
    now = datetime.datetime.now()
    y = now - datetime.timedelta(days=1)
    assert friendly_date(now.strftime("%Y-%m-%d 09:05:00")) == "Today, 09:05"
    assert friendly_date(y.strftime("%Y-%m-%d 23:59:59")) == "Yesterday, 23:59"
    assert friendly_date(f"{now.year - 1}-03-07 10:00:00") == f"Mar 7, {now.year - 1}"
    assert friendly_date("not a date") == "not a date"


def test_file_types_get_their_own_icon_and_every_icon_exists():
    assert file_icon("Q3 board deck.PDF") == ("doc", "danger")
    assert file_icon("passport-scan.jpg") == ("image", "info")
    assert file_icon("budget.xlsx") == ("sheet", "success")
    assert file_icon("backup.tar.gz") == ("archive", "warning")
    assert file_icon("README") == ("file", "neutral")
    from ui.widgets import _FILE_KINDS
    assert {icon for icon, _k in _FILE_KINDS.values()} <= set(icons.available())


def test_sidebar_badge_is_a_count_not_text():
    b = NavButton("Inbox", "inbox")
    b.set_badge(3)
    assert b.text() == "Inbox" and b.badge() == 3 and b.accessibleName() == "Inbox, 3 waiting"
    b.set_badge(0)
    assert b.accessibleName() == "Inbox"
    b.resize(200, 44)
    b.set_badge(120)
    b.grab()                                                        # paints "99+" without error


def test_empty_state_text_can_change():
    e = EmptyState("users", "No one yet", "Import someone.")
    e.set_text("No match", "Nobody called “zed”.")
    assert (e._title.text(), e._text.text()) == ("No match", "Nobody called “zed”.")
