"""Shared scaffolding for the application's pages."""

from __future__ import annotations

from typing import Protocol

from PySide6 import QtCore, QtWidgets

from ...config import Settings
from ...library import Library
from ..theme import Palette
from ..widgets import make_label


class AppContext(Protocol):
    """What a page is allowed to ask of the main window.

    Keeping this narrow means the pages never reach into each other, and the
    main window stays the only place that knows about the whole app.
    """

    library: Library
    settings: Settings

    def palette_(self) -> Palette: ...
    def run_worker(self, name: str, function, *args, **kwargs): ...
    def set_status(self, message: str) -> None: ...
    def notify(self, title: str, message: str, level: str = "info") -> None: ...
    def library_changed(self) -> None: ...
    def go_to(self, page: str) -> None: ...


class Page(QtWidgets.QWidget):
    """A page with a title, a one-line explanation and a content area."""

    title = "Page"
    hint = ""

    def __init__(self, context: AppContext, parent=None) -> None:
        super().__init__(parent)
        self.context = context

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 16)
        outer.setSpacing(14)

        header = QtWidgets.QVBoxLayout()
        header.setSpacing(3)
        self.title_label = make_label(self.title, "PageTitle")
        header.addWidget(self.title_label)
        if self.hint:
            header.addWidget(make_label(self.hint, "PageHint", wrap=True))
        outer.addLayout(header)

        self.body = QtWidgets.QVBoxLayout()
        self.body.setSpacing(14)
        outer.addLayout(self.body, 1)

    # ------------------------------------------------------------------
    @property
    def palette_(self) -> Palette:
        return self.context.palette_()

    def refresh(self) -> None:
        """Called whenever the page becomes visible or the library changes."""

    def apply_palette(self) -> None:
        """Called after a theme change so custom-painted widgets repaint."""


def row(*widgets: QtWidgets.QWidget | QtWidgets.QLayout, spacing: int = 10,
        stretch_last: bool = False) -> QtWidgets.QHBoxLayout:
    layout = QtWidgets.QHBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    for index, item in enumerate(widgets):
        last = index == len(widgets) - 1
        weight = 1 if (stretch_last and last) else 0
        if isinstance(item, QtWidgets.QLayout):
            layout.addLayout(item, weight)
        else:
            layout.addWidget(item, weight)
    if not stretch_last:
        layout.addStretch(1)
    return layout


def form(*pairs: tuple[str, QtWidgets.QWidget]) -> QtWidgets.QFormLayout:
    layout = QtWidgets.QFormLayout()
    layout.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)
    layout.setFormAlignment(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop)
    layout.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
    layout.setHorizontalSpacing(16)
    layout.setVerticalSpacing(10)
    for label, widget in pairs:
        layout.addRow(make_label(label, "Muted"), widget)
    return layout
