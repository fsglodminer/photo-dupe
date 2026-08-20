"""Reusable pieces of the interface."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

from PySide6 import QtCore, QtGui, QtWidgets

from ..imaging import aspect_label, human_size
from ..quality import METRIC_LABELS, METRIC_NAMES
from ..records import Photo
from .icons import icon
from .theme import Palette, score_color


def make_label(text: str, object_name: str = "", wrap: bool = False) -> QtWidgets.QLabel:
    label = QtWidgets.QLabel(text)
    if object_name:
        label.setObjectName(object_name)
    label.setWordWrap(wrap)
    return label


def card(*children: QtWidgets.QWidget, spacing: int = 10, margins: int = 16) -> QtWidgets.QFrame:
    """A bordered panel holding a vertical stack of widgets."""
    frame = QtWidgets.QFrame()
    frame.setObjectName("Card")
    layout = QtWidgets.QVBoxLayout(frame)
    layout.setContentsMargins(margins, margins, margins, margins)
    layout.setSpacing(spacing)
    for child in children:
        layout.addWidget(child)
    return frame


class StatCard(QtWidgets.QFrame):
    """A big number with a caption -- the library dashboard tiles."""

    def __init__(self, label: str, value: str = "-", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 13, 16, 13)
        layout.setSpacing(2)
        self.value_label = make_label(value, "StatValue")
        self.caption_label = make_label(label.upper(), "StatLabel")
        layout.addWidget(self.value_label)
        layout.addWidget(self.caption_label)
        self.setMinimumWidth(130)

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)


class MetricBars(QtWidgets.QWidget):
    """Horizontal bars for the seven quality metrics of one photo."""

    def __init__(self, palette: Palette, parent=None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._metrics: dict[str, float] = {}
        self.setMinimumHeight(len(METRIC_NAMES) * 21 + 6)

    def set_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.update()

    def set_metrics(self, metrics: dict[str, float]) -> None:
        self._metrics = dict(metrics or {})
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        p = self._palette
        font = painter.font()
        font.setPointSizeF(8.5)
        painter.setFont(font)
        label_width = 78
        value_width = 34
        row_height = 21

        for row, name in enumerate(METRIC_NAMES):
            value = float(self._metrics.get(name, 0.0) or 0.0)
            top = row * row_height + 3
            painter.setPen(p.q("text_dim"))
            painter.drawText(
                QtCore.QRectF(0, top, label_width, 15),
                QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter,
                METRIC_LABELS.get(name, name),
            )
            track = QtCore.QRectF(
                label_width, top + 4,
                max(20.0, self.width() - label_width - value_width - 4), 7,
            )
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(p.q("surface_alt"))
            painter.drawRoundedRect(track, 3.5, 3.5)
            if value > 0:
                filled = QtCore.QRectF(track)
                filled.setWidth(max(7.0, track.width() * min(1.0, value)))
                painter.setBrush(score_color(p, value * 100.0))
                painter.drawRoundedRect(filled, 3.5, 3.5)
            painter.setPen(p.q("text"))
            painter.drawText(
                QtCore.QRectF(self.width() - value_width, top, value_width, 15),
                QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter,
                f"{value * 100:.0f}",
            )
        painter.end()


class ScoreBadge(QtWidgets.QWidget):
    """A round score dial with the letter grade underneath."""

    def __init__(self, palette: Palette, parent=None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._score = 0.0
        self._grade = "-"
        self.setFixedSize(78, 78)

    def set_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.update()

    def set_score(self, score: float, grade: str) -> None:
        self._score = float(score)
        self._grade = grade
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        p = self._palette
        rect = QtCore.QRectF(6, 6, self.width() - 12, self.height() - 12)
        painter.setPen(QtGui.QPen(p.q("surface_alt"), 6))
        painter.drawEllipse(rect)
        colour = score_color(p, self._score)
        arc_pen = QtGui.QPen(colour, 6)
        arc_pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        painter.setPen(arc_pen)
        span = int(-360 * 16 * min(1.0, max(0.0, self._score / 100.0)))
        painter.drawArc(rect, 90 * 16, span)

        font = painter.font()
        font.setPointSizeF(17)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(p.q("text"))
        painter.drawText(
            rect.adjusted(0, -4, 0, -4),
            QtCore.Qt.AlignmentFlag.AlignCenter,
            f"{self._score:.0f}",
        )
        font.setPointSizeF(8)
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(colour)
        painter.drawText(
            QtCore.QRectF(rect.left(), rect.bottom() - 20, rect.width(), 16),
            QtCore.Qt.AlignmentFlag.AlignCenter,
            self._grade,
        )
        painter.end()


class RatingStars(QtWidgets.QWidget):
    """Five clickable stars for a manual override of the computed score."""

    rating_changed = QtCore.Signal(int)

    def __init__(self, palette: Palette, parent=None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._rating = 0
        self._hover = 0
        self.setFixedHeight(24)
        self.setMinimumWidth(5 * 22)
        self.setMouseTracking(True)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)

    def set_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.update()

    def rating(self) -> int:
        return self._rating

    def set_rating(self, rating: int) -> None:
        self._rating = max(0, min(5, int(rating)))
        self.update()

    def _star_at(self, x: float) -> int:
        return max(0, min(5, int(x // 22) + 1))

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        self._hover = self._star_at(event.position().x())
        self.update()

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self._hover = 0
        self.update()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        chosen = self._star_at(event.position().x())
        # Clicking the current rating clears it, so a rating can be undone.
        self._rating = 0 if chosen == self._rating else chosen
        self.rating_changed.emit(self._rating)
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        active = self._hover or self._rating
        for index in range(5):
            colour = (
                self._palette.q("warn") if index < active else self._palette.q("border")
            )
            pixmap = icon("star", colour, 18).pixmap(18, 18)
            painter.drawPixmap(int(index * 22), 3, pixmap)
        painter.end()


#: Preview images are decoded no larger than this on the long edge. Reading a
#: 24 Mpx JPEG in full takes a couple of hundred milliseconds, which is a
#: visible stutter when arrowing through a grid -- and every pixel beyond the
#: panel's size would be thrown away anyway.
PREVIEW_MAX_EDGE = 1600


class PreviewLabel(QtWidgets.QLabel):
    """Shows one photo scaled to fit, reloading on resize."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(160, 120)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding
        )
        self._source = QtGui.QPixmap()
        self._path = ""

    def set_photo(self, path: str) -> None:
        if path == self._path:
            return
        self._path = path
        self._source = _read_scaled(path) if path else QtGui.QPixmap()
        self._rescale()

    def clear_photo(self) -> None:
        self._path = ""
        self._source = QtGui.QPixmap()
        self.setText("")

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._rescale()

    def _rescale(self) -> None:
        if self._source.isNull():
            self.setPixmap(QtGui.QPixmap())
            if self._path:
                self.setText("Preview unavailable")
            return
        self.setPixmap(
            self._source.scaled(
                self.size(),
                QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation,
            )
        )


def _read_scaled(path: str, max_edge: int = PREVIEW_MAX_EDGE) -> QtGui.QPixmap:
    """Decode an image no bigger than ``max_edge``, honouring EXIF orientation.

    QImageReader can scale during decoding rather than after it, so a large
    JPEG never has to be expanded in memory at full size just to be shrunk.
    """
    reader = QtGui.QImageReader(path)
    reader.setAutoTransform(True)          # apply the EXIF rotation
    size = reader.size()
    if size.isValid() and max(size.width(), size.height()) > max_edge:
        scaled = size.scaled(
            max_edge, max_edge, QtCore.Qt.AspectRatioMode.KeepAspectRatio
        )
        reader.setScaledSize(scaled)
    image = reader.read()
    return QtGui.QPixmap.fromImage(image) if not image.isNull() else QtGui.QPixmap()


class PhotoDetails(QtWidgets.QWidget):
    """The inspector panel: preview, score dial, metric bars and file facts."""

    rating_changed = QtCore.Signal(int, int)   # photo id, rating
    open_requested = QtCore.Signal(str)

    def __init__(self, palette: Palette, parent=None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._photo: Photo | None = None

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(11)

        self.preview = PreviewLabel()
        self.preview.setMinimumHeight(200)
        preview_frame = card(self.preview, margins=8)
        layout.addWidget(preview_frame, 1)

        header = QtWidgets.QWidget()
        header_layout = QtWidgets.QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(12)
        self.badge = ScoreBadge(palette)
        header_layout.addWidget(self.badge)
        text_column = QtWidgets.QVBoxLayout()
        text_column.setSpacing(2)
        self.name_label = make_label("No photo selected", "SectionTitle", wrap=True)
        self.meta_label = make_label("", "Muted", wrap=True)
        self.percentile_label = make_label("", "Muted", wrap=True)
        text_column.addWidget(self.name_label)
        text_column.addWidget(self.meta_label)
        text_column.addWidget(self.percentile_label)
        text_column.addStretch(1)
        header_layout.addLayout(text_column, 1)

        self.stars = RatingStars(palette)
        self.stars.rating_changed.connect(self._on_rating)

        self.metrics = MetricBars(palette)
        self.facts = make_label("", "Muted", wrap=True)
        self.flags_label = make_label("", "Muted", wrap=True)
        self.open_button = QtWidgets.QPushButton("Open in image viewer")
        self.open_button.clicked.connect(self._on_open)
        self.open_button.setEnabled(False)

        layout.addWidget(
            card(
                header,
                make_label("YOUR RATING", "StatLabel"),
                self.stars,
                make_label("QUALITY BREAKDOWN", "StatLabel"),
                self.metrics,
                self.facts,
                self.flags_label,
                self.open_button,
            )
        )

    # ------------------------------------------------------------------
    def set_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.badge.set_palette(palette)
        self.metrics.set_palette(palette)
        self.stars.set_palette(palette)

    def _on_rating(self, rating: int) -> None:
        if self._photo is not None:
            self._photo.rating = rating
            self.rating_changed.emit(self._photo.id, rating)

    def _on_open(self) -> None:
        if self._photo is not None:
            self.open_requested.emit(self._photo.path)

    # ------------------------------------------------------------------
    def show_photo(self, photo: Photo | None, percentile: float | None = None) -> None:
        self._photo = photo
        if photo is None:
            self.preview.clear_photo()
            self.name_label.setText("No photo selected")
            self.meta_label.setText("Select a photo to see its quality breakdown.")
            self.percentile_label.setText("")
            self.badge.set_score(0, "-")
            self.metrics.set_metrics({})
            self.facts.setText("")
            self.flags_label.setText("")
            self.stars.set_rating(0)
            self.open_button.setEnabled(False)
            return

        self.preview.set_photo(photo.path)
        self.name_label.setText(photo.name)
        self.meta_label.setText(photo.folder)
        self.badge.set_score(photo.score, photo.grade())
        self.metrics.set_metrics(photo.metrics)
        self.stars.set_rating(photo.rating)
        self.open_button.setEnabled(True)

        if percentile is not None:
            self.percentile_label.setText(
                f"Better than {percentile:.0f}% of your library"
            )
        else:
            self.percentile_label.setText("")

        facts = [
            f"{photo.width} x {photo.height}  ({photo.megapixels:.1f} MP, "
            f"{aspect_label(photo.width, photo.height)})",
            f"{human_size(photo.size)}  {photo.format}",
        ]
        if photo.camera:
            facts.append(photo.camera)
        if photo.iso:
            facts.append(f"ISO {photo.iso}")
        if photo.taken_at:
            facts.append(photo.taken_at.strftime("Taken %d %b %Y at %H:%M"))
        self.facts.setText("\n".join(facts))
        self.flags_label.setText(
            ("Noted: " + ", ".join(photo.flags)) if photo.flags else ""
        )


class FolderList(QtWidgets.QWidget):
    """An add/remove list of folders."""

    changed = QtCore.Signal()

    def __init__(self, palette: Palette, add_label: str = "Add folder...", parent=None) -> None:
        super().__init__(parent)
        self._palette = palette
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.list = QtWidgets.QListWidget()
        self.list.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.list.setMinimumHeight(110)
        layout.addWidget(self.list)

        buttons = QtWidgets.QHBoxLayout()
        buttons.setSpacing(8)
        self.add_button = QtWidgets.QPushButton(add_label)
        self.add_button.setIcon(icon("folder", palette.text, 16))
        self.remove_button = QtWidgets.QPushButton("Remove")
        self.remove_button.setIcon(icon("cross", palette.text, 16))
        self.add_button.clicked.connect(self._on_add)
        self.remove_button.clicked.connect(self._on_remove)
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.remove_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

    def _on_add(self) -> None:
        chosen = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Choose a folder", str(Path.home())
        )
        if chosen:
            self.add_folder(chosen)

    def _on_remove(self) -> None:
        for item in self.list.selectedItems():
            self.list.takeItem(self.list.row(item))
        self.changed.emit()

    def add_folder(self, path: str) -> None:
        if path in self.folders():
            return
        item = QtWidgets.QListWidgetItem(path)
        item.setIcon(icon("folder", self._palette.text_dim, 16))
        self.list.addItem(item)
        self.changed.emit()

    def set_folders(self, folders: Iterable[str]) -> None:
        self.list.clear()
        for folder in folders:
            self.add_folder(folder)

    def folders(self) -> list[str]:
        return [self.list.item(row).text() for row in range(self.list.count())]


class ComparisonStrip(QtWidgets.QWidget):
    """Side-by-side view of every photo in one duplicate group.

    This is where the app has to earn trust: before anyone deletes a file they
    want to see the candidates next to each other at a decent size, with the
    numbers that led to the recommendation shown underneath.
    """

    selection_changed = QtCore.Signal(object)
    mark_toggled = QtCore.Signal(object)

    def __init__(self, palette: Palette, parent=None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._tiles: list[_CompareTile] = []
        self._selected_id: int | None = None

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.container = QtWidgets.QWidget()
        self.row = QtWidgets.QHBoxLayout(self.container)
        self.row.setContentsMargins(4, 4, 4, 4)
        self.row.setSpacing(12)
        self.row.addStretch(1)
        self.scroll.setWidget(self.container)
        outer.addWidget(self.scroll)

    def set_palette(self, palette: Palette) -> None:
        self._palette = palette
        for tile in self._tiles:
            tile.set_palette(palette)

    def clear(self) -> None:
        for tile in self._tiles:
            tile.setParent(None)
            tile.deleteLater()
        self._tiles = []
        self._selected_id = None

    def show_group(self, photos: Sequence[Photo], best_id: int) -> None:
        self.clear()
        for photo in photos:
            tile = _CompareTile(photo, photo.id == best_id, self._palette, self)
            tile.clicked.connect(self._on_tile_clicked)
            tile.mark_clicked.connect(self.mark_toggled.emit)
            self.row.insertWidget(self.row.count() - 1, tile)
            self._tiles.append(tile)
        if self._tiles:
            self._on_tile_clicked(self._tiles[0].photo)

    def refresh_marks(self) -> None:
        for tile in self._tiles:
            tile.refresh()

    def _on_tile_clicked(self, photo: Photo) -> None:
        self._selected_id = photo.id
        for tile in self._tiles:
            tile.set_selected(tile.photo.id == photo.id)
        self.selection_changed.emit(photo)


class _CompareTile(QtWidgets.QFrame):
    """One photo inside :class:`ComparisonStrip`."""

    clicked = QtCore.Signal(object)
    mark_clicked = QtCore.Signal(object)

    def __init__(self, photo: Photo, is_best: bool, palette: Palette, parent=None) -> None:
        super().__init__(parent)
        self.photo = photo
        self.is_best = is_best
        self._palette = palette
        self._selected = False
        self.setObjectName("Card")
        self.setFixedWidth(258)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(7)

        self.preview = PreviewLabel()
        self.preview.setFixedHeight(190)
        self.preview.set_photo(photo.thumbnail or photo.path)
        layout.addWidget(self.preview)

        self.title = make_label(photo.name, "SectionTitle", wrap=True)
        # Fixed height keeps the facts and buttons on one line across all tiles,
        # however long an individual filename turns out to be.
        self.title.setFixedHeight(46)
        self.title.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop
        )
        layout.addWidget(self.title)

        self.stats = make_label("", "Muted", wrap=True)
        layout.addWidget(self.stats)

        self.mark_button = QtWidgets.QPushButton()
        self.mark_button.clicked.connect(lambda: self.mark_clicked.emit(self.photo))
        layout.addWidget(self.mark_button)
        self.refresh()

    def set_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.refresh()

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self.refresh()

    def refresh(self) -> None:
        from ..records import MARK_DELETE

        photo = self.photo
        badge = "  <b>BEST OF GROUP</b>" if self.is_best else ""
        colour = self._palette.keep if self.is_best else self._palette.text_dim
        self.title.setText(
            f"{photo.name}<br><span style='color:{colour};font-size:10px'>"
            f"score {photo.score:.1f} - grade {photo.grade()}{badge}</span>"
        )
        lines = [
            f"{photo.width} x {photo.height}   {human_size(photo.size)}",
            f"{photo.format}   {photo.bytes_per_pixel():.2f} bytes/pixel",
        ]
        if photo.flags:
            lines.append(", ".join(photo.flags))
        lines.append(photo.folder)
        self.stats.setText("\n".join(lines))

        marked = photo.mark == MARK_DELETE
        self.mark_button.setText("Undo remove" if marked else "Mark for removal")
        self.mark_button.setObjectName("" if marked else "Danger")
        self.mark_button.setIcon(
            icon("refresh" if marked else "trash", self._palette.text, 16)
        )
        border = (
            self._palette.accent if self._selected
            else (self._palette.keep if self.is_best else self._palette.border)
        )
        self.setStyleSheet(
            f"#Card {{ border: {2 if self._selected or self.is_best else 1}px solid {border}; }}"
        )
        self.mark_button.style().unpolish(self.mark_button)
        self.mark_button.style().polish(self.mark_button)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        self.clicked.emit(self.photo)
        super().mousePressEvent(event)
