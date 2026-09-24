"""ui/pages/base.py — every screen is a scrollable page with a title, optional action buttons and content."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from ui.widgets import SectionHeader


class Page(QScrollArea):
    def __init__(self, ctl, title: str, subtitle: str = ""):
        super().__init__()
        self.ctl = ctl
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("Root")
        self.setWidget(inner)
        self.root = QVBoxLayout(inner)
        self.root.setContentsMargins(36, 30, 36, 30)
        self.root.setSpacing(18)
        head = QHBoxLayout()
        self.header = SectionHeader(title, subtitle)
        head.addWidget(self.header, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        head.addLayout(self.actions)
        self.root.addLayout(head)

    def on_show(self) -> None:
        """Called each time the page becomes visible; refresh anything that may have changed."""

    def on_session_ended(self) -> None:
        """Called when the vault locks; clear anything sensitive."""
