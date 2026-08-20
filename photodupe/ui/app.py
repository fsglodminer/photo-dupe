"""The main window, and the entry point that opens it."""

from __future__ import annotations

import logging
import sys
from typing import Callable, Sequence

from PySide6 import QtCore, QtGui, QtWidgets

from .. import __app_id__, __app_name__, __version__
from ..config import Settings, ensure_app_dirs
from ..db import Database
from ..library import Library
from ..records import DuplicateGroup, Photo
from .icons import app_icon, icon, stylesheet_assets
from .pages import (
    DuplicatesPage,
    ExportPage,
    ImportPage,
    LibraryPage,
    RankingPage,
    SettingsPage,
)
from .theme import Palette, palette_for, stylesheet
from .widgets import make_label
from .workers import ThumbnailLoader, Worker

log = logging.getLogger(__name__)

PAGES = (
    ("library", "Library", "library", LibraryPage),
    ("duplicates", "Duplicates", "duplicates", DuplicatesPage),
    ("ranking", "Ranking", "ranking", RankingPage),
    ("import", "Import", "import", ImportPage),
    ("export", "Export", "export", ExportPage),
    ("settings", "Settings", "settings", SettingsPage),
)


class MainWindow(QtWidgets.QMainWindow):
    """Sidebar navigation over a stack of pages, with a shared status bar."""

    def __init__(self, settings: Settings, library: Library) -> None:
        super().__init__()
        self.settings = settings
        self.library = library
        self._palette = palette_for(settings.theme)
        self._workers: dict[str, Worker] = {}
        #: Duplicate groups, shared between the Duplicates and Export pages.
        #: ``None`` means "not worked out yet", which is different from
        #: an empty list meaning "looked, found none".
        self._group_cache: list[DuplicateGroup] | None = None

        self.thumbnail_loader = ThumbnailLoader(
            max_workers=max(2, min(6, settings.thread_count()))
        )

        self.setWindowTitle(f"{__app_name__} {__version__}")
        self.setWindowIcon(app_icon(self._palette.accent))
        self.resize(1360, 880)
        self.setMinimumSize(1040, 660)

        central = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.sidebar = self._build_sidebar()
        layout.addWidget(self.sidebar)

        right = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        self.stack = QtWidgets.QStackedWidget()
        self.pages: dict[str, QtWidgets.QWidget] = {}
        for key, _title, _icon_name, page_class in PAGES:
            page = page_class(self)
            self.pages[key] = page
            self.stack.addWidget(page)
        right_layout.addWidget(self.stack, 1)
        right_layout.addWidget(self._build_status_bar())
        layout.addWidget(right, 1)
        self.setCentralWidget(central)

        self._build_shortcuts()
        self.apply_theme()
        self.go_to("library")
        self.set_status(self._welcome_message())

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------
    def _build_sidebar(self) -> QtWidgets.QWidget:
        sidebar = QtWidgets.QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(196)
        layout = QtWidgets.QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 12)
        layout.setSpacing(0)

        layout.addWidget(make_label(__app_name__, "SidebarTitle"))
        layout.addWidget(make_label("Sort, rank, keep the best", "SidebarSubtitle"))

        self.nav_buttons: dict[str, QtWidgets.QPushButton] = {}
        self.nav_group = QtWidgets.QButtonGroup(self)
        self.nav_group.setExclusive(True)
        for key, title, icon_name, _page_class in PAGES:
            button = QtWidgets.QPushButton(f"  {title}")
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            button.setIconSize(QtCore.QSize(17, 17))
            button.clicked.connect(lambda _checked=False, k=key: self.go_to(k))
            self.nav_group.addButton(button)
            layout.addWidget(button)
            self.nav_buttons[key] = button

        layout.addStretch(1)
        self.version_label = make_label(f"v{__version__}", "SidebarSubtitle")
        layout.addWidget(self.version_label)
        return sidebar

    def _build_status_bar(self) -> QtWidgets.QWidget:
        bar = QtWidgets.QWidget()
        bar.setObjectName("StatusBar")
        bar.setFixedHeight(34)
        layout = QtWidgets.QHBoxLayout(bar)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(12)
        self.status_label = make_label("")
        layout.addWidget(self.status_label, 1)
        self.busy_label = make_label("")
        layout.addWidget(self.busy_label)
        return bar

    def _build_shortcuts(self) -> None:
        for index, (key, _title, _icon, _cls) in enumerate(PAGES, start=1):
            QtGui.QShortcut(
                QtGui.QKeySequence(f"Ctrl+{index}"), self,
                activated=lambda k=key: self.go_to(k),
            )
        QtGui.QShortcut(
            QtGui.QKeySequence("Ctrl+R"), self,
            activated=lambda: self.pages["library"].start_scan(False),
        )
        QtGui.QShortcut(
            QtGui.QKeySequence("Ctrl+D"), self,
            activated=lambda: (
                self.go_to("duplicates"), self.pages["duplicates"].find_duplicates()
            ),
        )
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Q"), self, activated=self.close)

    def _welcome_message(self) -> str:
        count = self.library.db.count()
        if count:
            return f"{count:,} photos indexed. Ctrl+R rescans, Ctrl+D finds duplicates."
        return "Add a folder on the Library page to get started."

    # ------------------------------------------------------------------
    # AppContext protocol
    # ------------------------------------------------------------------
    def palette_(self) -> Palette:
        return self._palette

    def set_status(self, message: str) -> None:
        self.status_label.setText(message)

    def notify(self, title: str, message: str, level: str = "info") -> None:
        icons = {
            "info": QtWidgets.QMessageBox.Icon.Information,
            "warning": QtWidgets.QMessageBox.Icon.Warning,
            "error": QtWidgets.QMessageBox.Icon.Critical,
        }
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(icons.get(level, QtWidgets.QMessageBox.Icon.Information))
        box.setText(title)
        box.setInformativeText(message)
        box.exec()

    def go_to(self, page: str) -> None:
        widget = self.pages.get(page)
        if widget is None:
            return
        self.stack.setCurrentWidget(widget)
        button = self.nav_buttons.get(page)
        if button is not None:
            button.setChecked(True)
        widget.refresh()

    def library_changed(self) -> None:
        """The index changed: drop caches and let every page catch up."""
        self._group_cache = None
        current = self.stack.currentWidget()
        for page in self.pages.values():
            if page is current:
                page.refresh()
        self.pages["library"].refresh()

    def cached_groups(self) -> list[DuplicateGroup] | None:
        """Duplicate groups if they have already been worked out, else ``None``.

        Grouping a large library takes seconds, so it is never done on the GUI
        thread. Pages that need groups ask for the cache and, if it is empty,
        start a background job.
        """
        return self._group_cache

    def set_group_cache(self, groups: Sequence[DuplicateGroup]) -> None:
        self._group_cache = list(groups)

    def export_selection(self, photos: Sequence[Photo]) -> None:
        export_page = self.pages["export"]
        export_page.set_explicit_selection(photos)
        self.go_to("export")

    def run_worker(
        self, name: str, function: Callable, *args, **kwargs
    ) -> Worker | None:
        """Start a named background job, refusing to run two of the same kind."""
        existing = self._workers.get(name)
        if existing is not None and existing.isRunning():
            self.notify(
                "Already running",
                f"A {name} job is already in progress. Wait for it to finish or "
                f"stop it first.",
                "warning",
            )
            return None
        worker = Worker(function, *args, parent=self, **kwargs)
        self._workers[name] = worker
        worker.progressed.connect(
            lambda done, total, message: self._on_worker_progress(name, done, total, message)
        )
        worker.done.connect(lambda: self._on_worker_done(name))
        self.busy_label.setText(f"{name.capitalize()} running...")
        worker.start()
        return worker

    def _on_worker_progress(self, name: str, done: int, total: int, message: str) -> None:
        if total > 0:
            self.busy_label.setText(f"{name.capitalize()} {100 * done // max(1, total)}%")
        else:
            self.busy_label.setText(f"{name.capitalize()} running...")

    def _on_worker_done(self, name: str) -> None:
        self._workers.pop(name, None)
        if not any(worker.isRunning() for worker in self._workers.values()):
            self.busy_label.setText("")

    # ------------------------------------------------------------------
    def apply_theme(self) -> None:
        self._palette = palette_for(self.settings.theme)
        palette = self._palette
        application = QtWidgets.QApplication.instance()
        if application is not None:
            application.setStyleSheet(stylesheet(palette, stylesheet_assets(palette)))
        self.setWindowIcon(app_icon(palette.accent))
        for key, _title, icon_name, _cls in PAGES:
            self.nav_buttons[key].setIcon(icon(icon_name, palette.text_dim, 17))
        for page in self.pages.values():
            page.apply_palette()

    # ------------------------------------------------------------------
    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        running = [name for name, worker in self._workers.items() if worker.isRunning()]
        if running:
            answer = QtWidgets.QMessageBox.question(
                self,
                "Still working",
                f"{', '.join(running)} still running. Stop and quit?",
                QtWidgets.QMessageBox.StandardButton.Cancel
                | QtWidgets.QMessageBox.StandardButton.Yes,
                QtWidgets.QMessageBox.StandardButton.Cancel,
            )
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            for worker in self._workers.values():
                worker.cancel()
            for worker in self._workers.values():
                worker.wait(4000)
        self.settings.grid_icon_size = self.pages["ranking"].zoom.value()
        self.settings.save()
        self.thumbnail_loader.shutdown()
        self.library.close()
        event.accept()


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def run_gui(argv: list[str] | None = None) -> int:
    """Open the main window. Returns the process exit code."""
    logging.basicConfig(
        level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s"
    )
    ensure_app_dirs()

    QtCore.QCoreApplication.setAttribute(
        QtCore.Qt.ApplicationAttribute.AA_DontUseNativeMenuBar, False
    )
    application = QtWidgets.QApplication(argv if argv is not None else sys.argv)
    application.setApplicationName(__app_name__)
    application.setApplicationDisplayName(__app_name__)
    application.setDesktopFileName(__app_id__)
    application.setOrganizationName(__app_id__)

    settings = Settings.load()
    try:
        library = Library(settings, Database(settings.database_path))
    except Exception as exc:  # noqa: BLE001 - show the user why, do not traceback
        QtWidgets.QMessageBox.critical(
            None,
            "Cannot open the photo index",
            f"{settings.database_path}\n\n{exc}\n\n"
            f"Check that the folder exists and is writable.",
        )
        return 1

    window = MainWindow(settings, library)
    window.show()
    return application.exec()
