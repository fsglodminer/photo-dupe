"""The photo grid: item model plus the delegate that paints each cell."""

from __future__ import annotations

from typing import Iterable, Sequence

from PySide6 import QtCore, QtGui, QtWidgets

from ..imaging import human_size
from ..records import MARK_DELETE, MARK_KEEP, Photo
from .theme import Palette, score_color
from .workers import ThumbnailLoader

PHOTO_ROLE = int(QtCore.Qt.ItemDataRole.UserRole) + 1
PIXMAP_ROLE = int(QtCore.Qt.ItemDataRole.UserRole) + 2

#: Extra room under the thumbnail for the filename and the metadata line.
CAPTION_HEIGHT = 40
CELL_PADDING = 10


class PhotoListModel(QtCore.QAbstractListModel):
    """A flat, sorted list of photos backed by lazily loaded thumbnails."""

    def __init__(
        self,
        loader: ThumbnailLoader,
        thumbnail_size: int = 256,
        parent: QtCore.QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._photos: list[Photo] = []
        self._pixmaps: dict[int, QtGui.QPixmap] = {}
        self._rows: dict[int, int] = {}
        self._loader = loader
        self._thumbnail_size = thumbnail_size
        self._best_ids: set[int] = set()
        loader.loaded.connect(self._on_thumbnail)

    # ------------------------------------------------------------------
    def rowCount(self, parent: QtCore.QModelIndex = QtCore.QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._photos)

    def data(self, index: QtCore.QModelIndex, role: int = QtCore.Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._photos):
            return None
        photo = self._photos[index.row()]
        if role == PHOTO_ROLE:
            return photo
        if role == QtCore.Qt.ItemDataRole.DisplayRole:
            return photo.name
        if role == QtCore.Qt.ItemDataRole.ToolTipRole:
            return self._tooltip(photo)
        if role == PIXMAP_ROLE:
            pixmap = self._pixmaps.get(photo.id)
            if pixmap is None:
                self._loader.request(photo.id, photo.path, self._thumbnail_size)
            return pixmap
        return None

    def flags(self, index: QtCore.QModelIndex) -> QtCore.Qt.ItemFlag:
        base = super().flags(index)
        if index.isValid():
            return base | QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled
        return base

    # ------------------------------------------------------------------
    def _tooltip(self, photo: Photo) -> str:
        lines = [
            f"<b>{photo.name}</b>",
            f"{photo.width} x {photo.height}  ({photo.megapixels:.1f} MP)",
            f"{human_size(photo.size)}  {photo.format}",
            f"Score {photo.score:.1f}  (grade {photo.grade()})",
        ]
        if photo.camera:
            lines.append(photo.camera)
        if photo.taken_at:
            lines.append(photo.taken_at.strftime("%d %b %Y, %H:%M"))
        if photo.flags:
            lines.append("Flags: " + ", ".join(photo.flags))
        lines.append(f"<span style='color:#98a0b3'>{photo.folder}</span>")
        return "<br>".join(lines)

    # ------------------------------------------------------------------
    def set_photos(self, photos: Sequence[Photo], best_ids: Iterable[int] = ()) -> None:
        self.beginResetModel()
        self._photos = list(photos)
        self._rows = {photo.id: row for row, photo in enumerate(self._photos)}
        self._best_ids = set(best_ids)
        # Pixmaps are deliberately *not* cleared: switching sort order or
        # filtering should not make every thumbnail reload.
        self.endResetModel()

    def photos(self) -> list[Photo]:
        return list(self._photos)

    def photo_at(self, index: QtCore.QModelIndex | int) -> Photo | None:
        row = index.row() if isinstance(index, QtCore.QModelIndex) else index
        if 0 <= row < len(self._photos):
            return self._photos[row]
        return None

    def index_of(self, photo_id: int) -> QtCore.QModelIndex:
        row = self._rows.get(photo_id)
        return self.index(row, 0) if row is not None else QtCore.QModelIndex()

    def is_best(self, photo_id: int) -> bool:
        return photo_id in self._best_ids

    def set_thumbnail_size(self, size: int) -> None:
        if size != self._thumbnail_size:
            self._thumbnail_size = size
            self._pixmaps.clear()
            self._loader.clear()
            if self._photos:
                self.dataChanged.emit(
                    self.index(0, 0), self.index(len(self._photos) - 1, 0), [PIXMAP_ROLE]
                )

    def update_marks(self, marks: dict[int, str]) -> None:
        """Apply new review decisions and repaint just the affected cells."""
        for photo_id, mark in marks.items():
            row = self._rows.get(photo_id)
            if row is None:
                continue
            self._photos[row].mark = mark
            index = self.index(row, 0)
            self.dataChanged.emit(index, index, [PHOTO_ROLE])

    def remove_ids(self, photo_ids: Iterable[int]) -> None:
        removing = set(photo_ids)
        if not removing:
            return
        self.beginResetModel()
        self._photos = [p for p in self._photos if p.id not in removing]
        self._rows = {photo.id: row for row, photo in enumerate(self._photos)}
        for photo_id in removing:
            self._pixmaps.pop(photo_id, None)
        self.endResetModel()

    # ------------------------------------------------------------------
    @QtCore.Slot(int, QtCore.QByteArray)
    def _on_thumbnail(self, photo_id: int, data: QtCore.QByteArray) -> None:
        row = self._rows.get(photo_id)
        if row is None:
            return
        pixmap = QtGui.QPixmap()
        if not data.isEmpty():
            pixmap.loadFromData(data)
        # An empty pixmap is cached too, so a broken file is not retried forever.
        self._pixmaps[photo_id] = pixmap
        index = self.index(row, 0)
        self.dataChanged.emit(index, index, [PIXMAP_ROLE])


class PhotoDelegate(QtWidgets.QStyledItemDelegate):
    """Paints one photo cell: thumbnail, score pill, badges and caption."""

    def __init__(self, palette: Palette, icon_size: int = 180, parent=None) -> None:
        super().__init__(parent)
        self.palette_ = palette
        self.icon_size = icon_size
        self.show_score = True
        #: Set by the view so cells share out the row evenly instead of
        #: leaving a ragged strip of unused space on the right.
        self.cell_width = 0

    def set_palette(self, palette: Palette) -> None:
        self.palette_ = palette

    def set_icon_size(self, size: int) -> None:
        self.icon_size = size

    def sizeHint(self, option, index) -> QtCore.QSize:
        return QtCore.QSize(
            self.cell_width or (self.icon_size + CELL_PADDING * 2),
            self.icon_size + CAPTION_HEIGHT + CELL_PADDING * 2,
        )

    # ------------------------------------------------------------------
    def paint(self, painter: QtGui.QPainter, option, index) -> None:
        photo: Photo | None = index.data(PHOTO_ROLE)
        if photo is None:
            return
        painter.save()
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        p = self.palette_
        rect = QtCore.QRectF(option.rect).adjusted(4, 4, -4, -4)
        selected = bool(option.state & QtWidgets.QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QtWidgets.QStyle.StateFlag.State_MouseOver)

        # --- cell background -------------------------------------------
        if selected:
            painter.setBrush(p.q("accent"))
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.drawRoundedRect(rect, 9, 9)
        elif hovered:
            painter.setBrush(p.q("surface_alt"))
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.drawRoundedRect(rect, 9, 9)

        image_rect = QtCore.QRectF(
            rect.left() + 6, rect.top() + 6, rect.width() - 12,
            rect.height() - CAPTION_HEIGHT - 10,
        )

        # --- thumbnail ---------------------------------------------------
        pixmap: QtGui.QPixmap | None = index.data(PIXMAP_ROLE)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(p.q("window"))
        painter.drawRoundedRect(image_rect, 6, 6)
        if pixmap is not None and not pixmap.isNull():
            scaled = pixmap.size().scaled(
                image_rect.size().toSize(), QtCore.Qt.AspectRatioMode.KeepAspectRatio
            )
            target = QtCore.QRectF(
                image_rect.center().x() - scaled.width() / 2.0,
                image_rect.center().y() - scaled.height() / 2.0,
                scaled.width(), scaled.height(),
            )
            path = QtGui.QPainterPath()
            path.addRoundedRect(target, 5, 5)
            painter.save()
            painter.setClipPath(path)
            painter.drawPixmap(target, pixmap, QtCore.QRectF(pixmap.rect()))
            painter.restore()
        else:
            painter.setPen(QtGui.QPen(p.q("border"), 1))
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            font = painter.font()
            font.setPointSizeF(max(7.0, font.pointSizeF() - 1))
            painter.setFont(font)
            painter.setPen(p.q("text_dim"))
            label = "unreadable" if photo.status != "ok" else "loading"
            painter.drawText(image_rect, QtCore.Qt.AlignmentFlag.AlignCenter, label)

        # --- score pill ---------------------------------------------------
        if self.show_score and photo.status == "ok":
            self._draw_pill(
                painter,
                QtCore.QRectF(image_rect.right() - 44, image_rect.top() + 6, 38, 19),
                f"{photo.score:.0f}",
                score_color(p, photo.score),
            )

        # --- review badges -------------------------------------------------
        badge_x = image_rect.left() + 6
        if index.model() is not None and getattr(index.model(), "is_best", None):
            if index.model().is_best(photo.id):
                self._draw_pill(
                    painter,
                    QtCore.QRectF(badge_x, image_rect.top() + 6, 46, 19),
                    "BEST", p.q("keep"),
                )
                badge_x += 50
        if photo.mark == MARK_DELETE:
            self._draw_pill(
                painter, QtCore.QRectF(badge_x, image_rect.top() + 6, 58, 19),
                "REMOVE", p.q("bad"),
            )
        elif photo.mark == MARK_KEEP:
            self._draw_pill(
                painter, QtCore.QRectF(badge_x, image_rect.top() + 6, 46, 19),
                "KEEP", p.q("good"),
            )

        if photo.mark == MARK_DELETE:
            painter.setBrush(QtGui.QColor(0, 0, 0, 110))
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.drawRoundedRect(image_rect, 6, 6)

        # --- caption ---------------------------------------------------------
        text_color = p.q("accent_text") if selected else p.q("text")
        dim_color = p.q("accent_text") if selected else p.q("text_dim")
        name_rect = QtCore.QRectF(
            rect.left() + 8, image_rect.bottom() + 5, rect.width() - 16, 17
        )
        meta_rect = QtCore.QRectF(
            rect.left() + 8, image_rect.bottom() + 21, rect.width() - 16, 15
        )
        font = painter.font()
        font.setPointSizeF(9.0)
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(text_color)
        metrics = QtGui.QFontMetrics(font)
        painter.drawText(
            name_rect, QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter,
            metrics.elidedText(photo.name, QtCore.Qt.TextElideMode.ElideMiddle, int(name_rect.width())),
        )
        font.setPointSizeF(8.0)
        painter.setFont(font)
        painter.setPen(dim_color)
        detail = f"{photo.width}x{photo.height}   {human_size(photo.size)}"
        if photo.rating:
            detail = "*" * photo.rating + "   " + detail
        painter.drawText(
            meta_rect, QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter,
            QtGui.QFontMetrics(font).elidedText(
                detail, QtCore.Qt.TextElideMode.ElideRight, int(meta_rect.width())
            ),
        )
        painter.restore()

    # ------------------------------------------------------------------
    def _draw_pill(
        self, painter: QtGui.QPainter, rect: QtCore.QRectF, text: str, color: QtGui.QColor
    ) -> None:
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(rect, rect.height() / 2.0, rect.height() / 2.0)
        font = painter.font()
        font.setPointSizeF(7.5)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QtGui.QColor("#ffffff"))
        painter.drawText(rect, QtCore.Qt.AlignmentFlag.AlignCenter, text)


class PhotoGrid(QtWidgets.QListView):
    """Icon-mode view wired up for multi-select thumbnail browsing."""

    photo_activated = QtCore.Signal(object)

    def __init__(self, palette: Palette, icon_size: int = 180, parent=None) -> None:
        super().__init__(parent)
        self.delegate = PhotoDelegate(palette, icon_size, self)
        self.setItemDelegate(self.delegate)
        self.setViewMode(QtWidgets.QListView.ViewMode.IconMode)
        self.setResizeMode(QtWidgets.QListView.ResizeMode.Adjust)
        self.setMovement(QtWidgets.QListView.Movement.Static)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setUniformItemSizes(True)
        self.setSpacing(2)
        self.setMouseTracking(True)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(
            QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self.doubleClicked.connect(self._on_activated)

    def _on_activated(self, index: QtCore.QModelIndex) -> None:
        photo = index.data(PHOTO_ROLE)
        if photo is not None:
            self.photo_activated.emit(photo)

    def selected_photos(self) -> list[Photo]:
        return [
            index.data(PHOTO_ROLE)
            for index in self.selectionModel().selectedIndexes()
            if index.data(PHOTO_ROLE) is not None
        ]

    def set_icon_size(self, size: int) -> None:
        self.delegate.set_icon_size(size)
        model = self.model()
        if isinstance(model, PhotoListModel):
            model.set_thumbnail_size(max(128, min(512, size * 2)))
        self._fit_columns()
        self.reset()
        self.scheduleDelayedItemsLayout()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._fit_columns()

    def _fit_columns(self) -> None:
        """Widen the cells so a whole number of them fills the viewport."""
        minimum = self.delegate.icon_size + CELL_PADDING * 2
        available = self.viewport().width() - 2 * self.spacing()
        columns = max(1, available // (minimum + 2 * self.spacing()))
        width = max(minimum, available // columns - 2 * self.spacing())
        if width != self.delegate.cell_width:
            self.delegate.cell_width = width
            self.scheduleDelayedItemsLayout()
