"""The Ranking page: every photo, best first, with a quality inspector."""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from ...db import SORT_FIELDS
from ...records import MARK_DELETE, MARK_KEEP, MARK_NONE, Photo
from ..icons import icon
from ..models import PHOTO_ROLE, PhotoGrid, PhotoListModel
from ..widgets import PhotoDetails, make_label
from .base import Page

SORT_LABELS = [
    ("score", "Best first"),
    ("score_asc", "Worst first"),
    ("date", "Newest first"),
    ("date_asc", "Oldest first"),
    ("name", "Name (A-Z)"),
    ("size", "Largest file"),
    ("resolution", "Most pixels"),
    ("folder", "Folder"),
]

FILTER_LABELS = [
    ("", "All photos"),
    ("blurry", "Blurry"),
    ("dark", "Too dark"),
    ("bright", "Too bright"),
    ("blown highlights", "Blown highlights"),
    ("noisy", "Noisy"),
    ("flat", "Low contrast"),
    ("empty", "Almost empty"),
]


class RankingPage(Page):
    title = "Ranking"
    hint = (
        "Every indexed photo, ordered by a quality score built from sharpness, "
        "exposure, contrast, colour, grain, resolution and detail. Select a photo "
        "to see how the score was reached."
    )

    def __init__(self, context, parent=None) -> None:
        super().__init__(context, parent)
        palette = self.palette_
        self._percentiles: list[float] = []

        # --- toolbar ---------------------------------------------------
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Search filename, folder or camera...")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(240)
        self.search.textChanged.connect(self._schedule_reload)

        self.sort_box = QtWidgets.QComboBox()
        for key, label in SORT_LABELS:
            self.sort_box.addItem(label, key)
        self.sort_box.currentIndexChanged.connect(lambda _: self.reload())

        self.filter_box = QtWidgets.QComboBox()
        for key, label in FILTER_LABELS:
            self.filter_box.addItem(label, key)
        self.filter_box.currentIndexChanged.connect(lambda _: self.reload())

        self.min_score = QtWidgets.QSpinBox()
        self.min_score.setRange(0, 100)
        self.min_score.setPrefix("min ")
        self.min_score.valueChanged.connect(self._schedule_reload)

        self.zoom = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.zoom.setRange(110, 320)
        self.zoom.setValue(context.settings.grid_icon_size)
        self.zoom.setFixedWidth(110)
        self.zoom.valueChanged.connect(self._on_zoom)

        toolbar = QtWidgets.QHBoxLayout()
        toolbar.setSpacing(9)
        toolbar.addWidget(self.search)
        toolbar.addWidget(make_label("Sort", "Muted"))
        toolbar.addWidget(self.sort_box)
        toolbar.addWidget(make_label("Show", "Muted"))
        toolbar.addWidget(self.filter_box)
        toolbar.addWidget(self.min_score)
        toolbar.addStretch(1)
        toolbar.addWidget(icon_label(palette))
        toolbar.addWidget(self.zoom)
        self.body.addLayout(toolbar)

        # --- grid + inspector ------------------------------------------
        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)

        grid_side = QtWidgets.QWidget()
        grid_layout = QtWidgets.QVBoxLayout(grid_side)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.setSpacing(8)
        self.count_label = make_label("", "Muted")
        grid_layout.addWidget(self.count_label)
        self.grid = PhotoGrid(palette, context.settings.grid_icon_size)
        self.model = PhotoListModel(context.thumbnail_loader, context.settings.thumbnail_size)
        self.grid.setModel(self.model)
        self.grid.selectionModel().selectionChanged.connect(self._on_selection)
        self.grid.photo_activated.connect(self._open_photo)
        self.grid.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._context_menu)
        grid_layout.addWidget(self.grid, 1)

        actions = QtWidgets.QHBoxLayout()
        actions.setSpacing(9)
        self.mark_keep = QtWidgets.QPushButton("Mark as keeper")
        self.mark_keep.setIcon(icon("check", palette.text, 16))
        self.mark_keep.clicked.connect(lambda: self._mark_selected(MARK_KEEP))
        self.mark_remove = QtWidgets.QPushButton("Mark for removal")
        self.mark_remove.setObjectName("Danger")
        self.mark_remove.setIcon(icon("trash", "#ffffff", 16))
        self.mark_remove.clicked.connect(lambda: self._mark_selected(MARK_DELETE))
        self.mark_clear = QtWidgets.QPushButton("Clear mark")
        self.mark_clear.clicked.connect(lambda: self._mark_selected(MARK_NONE))
        self.export_selection = QtWidgets.QPushButton("Export selection...")
        self.export_selection.setIcon(icon("export", palette.text, 16))
        self.export_selection.clicked.connect(self._export_selection)
        for widget in (self.mark_keep, self.mark_remove, self.mark_clear):
            actions.addWidget(widget)
        actions.addStretch(1)
        actions.addWidget(self.export_selection)
        grid_layout.addLayout(actions)
        splitter.addWidget(grid_side)

        self.details = PhotoDetails(palette)
        self.details.rating_changed.connect(self._on_rating)
        self.details.open_requested.connect(self._open_path)
        details_holder = QtWidgets.QScrollArea()
        details_holder.setWidgetResizable(True)
        details_holder.setWidget(self.details)
        details_holder.setMinimumWidth(300)
        details_holder.setMaximumWidth(430)
        splitter.addWidget(details_holder)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([820, 360])
        self.body.addWidget(splitter, 1)

        self._reload_timer = QtCore.QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.setInterval(250)   # debounce typing in the search box
        self._reload_timer.timeout.connect(self.reload)
        self._update_actions()

    # ------------------------------------------------------------------
    def apply_palette(self) -> None:
        palette = self.palette_
        self.grid.delegate.set_palette(palette)
        self.details.set_palette(palette)
        self.grid.viewport().update()

    def refresh(self) -> None:
        self.reload()

    def _schedule_reload(self) -> None:
        self._reload_timer.start()

    def reload(self) -> None:
        db = self.context.library.db
        photos = db.photos(
            sort=self.sort_box.currentData() or "score",
            search=self.search.text().strip(),
            flag=self.filter_box.currentData() or "",
            min_score=float(self.min_score.value()) if self.min_score.value() else None,
        )
        self.model.set_photos(photos)
        self._percentiles = db.score_percentiles()
        total = db.count()
        if len(photos) == total:
            self.count_label.setText(f"{total:,} photos")
        else:
            self.count_label.setText(f"{len(photos):,} of {total:,} photos")
        self.details.show_photo(None)
        self._update_actions()

    # ------------------------------------------------------------------
    def _on_zoom(self, value: int) -> None:
        self.grid.set_icon_size(value)
        self.context.settings.grid_icon_size = value

    def _on_selection(self) -> None:
        photos = self.grid.selected_photos()
        if len(photos) == 1:
            percentile = self.context.library.percentile_of(
                photos[0].score, self._percentiles
            )
            self.details.show_photo(photos[0], percentile)
        else:
            self.details.show_photo(None)
            if photos:
                self.context.set_status(f"{len(photos)} photos selected")
        self._update_actions()

    def _on_rating(self, photo_id: int, rating: int) -> None:
        self.context.library.db.set_rating(photo_id, rating)
        index = self.model.index_of(photo_id)
        if index.isValid():
            self.model.dataChanged.emit(index, index, [PHOTO_ROLE])

    def _mark_selected(self, mark: str) -> None:
        photos = self.grid.selected_photos()
        if not photos:
            return
        ids = [photo.id for photo in photos]
        self.context.library.db.set_mark(ids, mark)
        self.model.update_marks({photo_id: mark for photo_id in ids})
        word = {
            MARK_KEEP: "marked as keepers",
            MARK_DELETE: "marked for removal",
            MARK_NONE: "cleared",
        }[mark]
        self.context.set_status(f"{len(ids)} photos {word}")

    def _export_selection(self) -> None:
        photos = self.grid.selected_photos()
        if not photos:
            self.context.notify(
                "Nothing selected", "Select some photos in the grid first.", "warning"
            )
            return
        self.context.export_selection(photos)

    # ------------------------------------------------------------------
    def _context_menu(self, position: QtCore.QPoint) -> None:
        index = self.grid.indexAt(position)
        if not index.isValid():
            return
        photo: Photo | None = index.data(PHOTO_ROLE)
        if photo is None:
            return
        palette = self.palette_
        menu = QtWidgets.QMenu(self)
        open_action = menu.addAction(icon("info", palette.text, 16), "Open in image viewer")
        folder_action = menu.addAction(icon("folder", palette.text, 16), "Open containing folder")
        menu.addSeparator()
        keep_action = menu.addAction(icon("check", palette.good, 16), "Mark as keeper")
        remove_action = menu.addAction(icon("trash", palette.bad, 16), "Mark for removal")
        clear_action = menu.addAction("Clear mark")
        menu.addSeparator()
        copy_action = menu.addAction("Copy file path")

        chosen = menu.exec(self.grid.viewport().mapToGlobal(position))
        if chosen is None:
            return
        if chosen is open_action:
            self._open_path(photo.path)
        elif chosen is folder_action:
            self._open_path(photo.folder)
        elif chosen is keep_action:
            self._mark_selected(MARK_KEEP)
        elif chosen is remove_action:
            self._mark_selected(MARK_DELETE)
        elif chosen is clear_action:
            self._mark_selected(MARK_NONE)
        elif chosen is copy_action:
            QtWidgets.QApplication.clipboard().setText(photo.path)
            self.context.set_status("Path copied to clipboard")

    def _open_photo(self, photo: Photo) -> None:
        self._open_path(photo.path)

    def _open_path(self, path: str) -> None:
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))

    def _update_actions(self) -> None:
        has_selection = bool(self.grid.selectionModel().hasSelection())
        for widget in (
            self.mark_keep, self.mark_remove, self.mark_clear, self.export_selection
        ):
            widget.setEnabled(has_selection)


def icon_label(palette) -> QtWidgets.QLabel:
    label = QtWidgets.QLabel()
    label.setPixmap(icon("library", palette.text_dim, 15).pixmap(15, 15))
    return label
