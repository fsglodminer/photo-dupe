"""The Duplicates page: review groups of similar photos and decide what goes."""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from ...grouping import group_statistics
from ...imaging import human_size
from ...records import MARK_DELETE, MARK_NONE, DuplicateGroup, Photo
from ..icons import icon
from ..widgets import ComparisonStrip, StatCard, card, make_label
from .base import Page


class DuplicatesPage(Page):
    title = "Duplicates"
    hint = (
        "Photos that are the same picture are collected into groups. The highest "
        "scoring one is proposed as the keeper; everything you mark for removal is "
        "held here until you confirm it."
    )

    def __init__(self, context, parent=None) -> None:
        super().__init__(context, parent)
        palette = self.palette_
        self._groups: list[DuplicateGroup] = []
        self._current: DuplicateGroup | None = None
        self._worker = None

        # --- summary ---------------------------------------------------
        self.stat_groups = StatCard("Groups found")
        self.stat_extra = StatCard("Surplus photos")
        self.stat_waste = StatCard("Space to reclaim")
        self.stat_marked = StatCard("Marked for removal")
        summary = QtWidgets.QHBoxLayout()
        summary.setSpacing(12)
        for widget in (self.stat_groups, self.stat_extra, self.stat_waste, self.stat_marked):
            summary.addWidget(widget, 1)
        self.body.addLayout(summary)

        # --- toolbar ---------------------------------------------------
        self.find_button = QtWidgets.QPushButton("Find duplicates")
        self.find_button.setObjectName("Primary")
        self.find_button.setIcon(icon("duplicates", palette.accent_text, 16))
        self.find_button.clicked.connect(self.find_duplicates)

        self.auto_button = QtWidgets.QPushButton("Mark all but the best")
        self.auto_button.setIcon(icon("check", palette.text, 16))
        self.auto_button.setToolTip(
            "In every group, mark every photo except the highest scoring one."
        )
        self.auto_button.clicked.connect(self._mark_all_duplicates)

        self.clear_button = QtWidgets.QPushButton("Clear marks")
        self.clear_button.setIcon(icon("refresh", palette.text, 16))
        self.clear_button.clicked.connect(self._clear_marks)

        self.delete_button = QtWidgets.QPushButton("Delete marked photos")
        self.delete_button.setObjectName("Danger")
        self.delete_button.setIcon(icon("trash", "#ffffff", 16))
        self.delete_button.clicked.connect(self._delete_marked)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setVisible(False)
        self.progress.setTextVisible(False)
        self.progress.setMaximumWidth(220)

        toolbar = QtWidgets.QHBoxLayout()
        toolbar.setSpacing(9)
        for widget in (self.find_button, self.auto_button, self.clear_button):
            toolbar.addWidget(widget)
        toolbar.addWidget(self.progress)
        toolbar.addStretch(1)
        toolbar.addWidget(self.delete_button)
        self.body.addLayout(toolbar)

        # --- group list + comparison ------------------------------------
        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)

        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)
        left_layout.addWidget(make_label("GROUPS", "StatLabel"))
        self.group_list = QtWidgets.QListWidget()
        self.group_list.currentRowChanged.connect(self._on_group_selected)
        left_layout.addWidget(self.group_list, 1)
        splitter.addWidget(left)

        right = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)
        self.group_title = make_label("No group selected", "SectionTitle")
        self.group_detail = make_label("", "Muted", wrap=True)
        right_layout.addWidget(self.group_title)
        right_layout.addWidget(self.group_detail)

        self.compare = ComparisonStrip(palette)
        self.compare.mark_toggled.connect(self._toggle_mark)
        right_layout.addWidget(self.compare, 1)

        group_actions = QtWidgets.QHBoxLayout()
        group_actions.setSpacing(9)
        self.keep_best_button = QtWidgets.QPushButton("Keep best, mark the rest")
        self.keep_best_button.clicked.connect(self._keep_best_in_group)
        self.skip_button = QtWidgets.QPushButton("Skip this group")
        self.skip_button.clicked.connect(self._next_group)
        group_actions.addWidget(self.keep_best_button)
        group_actions.addWidget(self.skip_button)
        group_actions.addStretch(1)
        right_layout.addLayout(group_actions)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([300, 820])
        self.body.addWidget(splitter, 1)

        self.empty_hint = make_label(
            "Run a scan on the Library page, then select Find duplicates.", "Muted"
        )
        self.body.addWidget(self.empty_hint)
        self._update_actions()

    # ------------------------------------------------------------------
    def apply_palette(self) -> None:
        self.compare.set_palette(self.palette_)

    def refresh(self) -> None:
        marked = self.context.library.db.statistics()["marks"].get(MARK_DELETE, 0)
        self.stat_marked.set_value(f"{marked:,}")
        self.delete_button.setEnabled(marked > 0)
        if not self._groups:
            self.find_button.setEnabled(self.context.library.db.count() > 1)

    # ------------------------------------------------------------------
    def find_duplicates(self) -> None:
        self.find_button.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)

        def task(progress):
            return self.context.library.duplicate_groups(
                progress=lambda done, total: progress(done, total, "Comparing photos")
            )

        worker = self.context.run_worker("duplicates", task)
        if worker is None:
            self.find_button.setEnabled(True)
            self.progress.setVisible(False)
            return
        self._worker = worker
        worker.progressed.connect(self._on_progress)
        worker.succeeded.connect(self._on_groups)
        worker.failed.connect(
            lambda message: self.context.notify("Could not compare photos", message, "error")
        )
        worker.done.connect(self._on_worker_done)

    @QtCore.Slot(int, int, str)
    def _on_progress(self, done: int, total: int, message: str) -> None:
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(done)

    def _on_worker_done(self) -> None:
        self.find_button.setEnabled(True)
        self.progress.setVisible(False)
        self._worker = None

    @QtCore.Slot(object)
    def _on_groups(self, groups: list[DuplicateGroup]) -> None:
        self._groups = groups
        # Share the result so the Export page does not have to recompute it.
        self.context.set_group_cache(groups)
        self._populate_list()
        stats = group_statistics(groups)
        self.stat_groups.set_value(f"{stats['groups']:,}")
        self.stat_extra.set_value(f"{stats['removable']:,}")
        self.stat_waste.set_value(human_size(stats["wasted_bytes"]))
        self.empty_hint.setVisible(not groups)
        if groups:
            self.context.set_status(
                f"{stats['groups']} duplicate groups - "
                f"{human_size(stats['wasted_bytes'])} could be reclaimed"
            )
            self.group_list.setCurrentRow(0)
        else:
            self.compare.clear()
            self.group_title.setText("No duplicates found")
            self.group_detail.setText(
                "Nothing in the library looks like a duplicate at the current "
                "similarity setting. Settings has a slider to loosen it."
            )
            self.context.set_status("No duplicates found")
        self._update_actions()

    def _populate_list(self) -> None:
        self.group_list.clear()
        for group in self._groups:
            item = QtWidgets.QListWidgetItem(
                f"{group.label()}\n{human_size(group.wasted_bytes())} - "
                f"{group.tightness:.0f}% alike"
            )
            item.setIcon(
                icon(
                    "duplicates",
                    self.palette_.bad if group.kind == "exact" else self.palette_.accent,
                    16,
                )
            )
            self.group_list.addItem(item)

    # ------------------------------------------------------------------
    def _on_group_selected(self, row_index: int) -> None:
        if not 0 <= row_index < len(self._groups):
            self._current = None
            self.compare.clear()
            self._update_actions()
            return
        group = self._groups[row_index]
        self._current = group
        self.group_title.setText(f"Group {group.index} of {len(self._groups)}")
        best = group.best
        detail = [
            f"{len(group.photos)} photos",
            "byte-for-byte identical" if group.kind == "exact"
            else f"{group.tightness:.0f}% visually alike",
            f"{human_size(group.wasted_bytes())} reclaimable",
        ]
        if best is not None:
            detail.append(f"suggested keeper: {best.name} (score {best.score:.1f})")
        self.group_detail.setText("   -   ".join(detail))
        self.compare.show_group(group.photos, group.best_id)
        self._update_actions()

    def _next_group(self) -> None:
        row_index = self.group_list.currentRow()
        if row_index + 1 < self.group_list.count():
            self.group_list.setCurrentRow(row_index + 1)

    # ------------------------------------------------------------------
    def _toggle_mark(self, photo: Photo) -> None:
        new_mark = MARK_NONE if photo.mark == MARK_DELETE else MARK_DELETE
        photo.mark = new_mark
        self.context.library.db.set_mark([photo.id], new_mark)
        self.compare.refresh_marks()
        self.refresh()

    def _keep_best_in_group(self) -> None:
        if self._current is None:
            return
        self._apply_marks([self._current])
        self._next_group()

    def _mark_all_duplicates(self) -> None:
        if not self._groups:
            return
        count = self._apply_marks(self._groups)
        self.context.set_status(f"{count} photos marked for removal")

    def _apply_marks(self, groups: list[DuplicateGroup]) -> int:
        to_mark: list[int] = []
        for group in groups:
            for photo in group.others:
                photo.mark = MARK_DELETE
                to_mark.append(photo.id)
            best = group.best
            if best is not None:
                best.mark = MARK_NONE
        self.context.library.db.set_mark(to_mark, MARK_DELETE)
        self.context.library.db.set_mark(
            [g.best_id for g in groups if g.best_id], MARK_NONE
        )
        self.compare.refresh_marks()
        self.refresh()
        return len(to_mark)

    def _clear_marks(self) -> None:
        marked = self.context.library.db.photos(mark=MARK_DELETE, only_ok=False)
        self.context.library.db.set_mark([p.id for p in marked], MARK_NONE)
        for group in self._groups:
            for photo in group.photos:
                photo.mark = MARK_NONE
        self.compare.refresh_marks()
        self.refresh()
        self.context.set_status("Cleared all removal marks")

    # ------------------------------------------------------------------
    def _delete_marked(self) -> None:
        library = self.context.library
        marked = library.db.photos(mark=MARK_DELETE, only_ok=False)
        if not marked:
            return
        to_trash = self.context.settings.delete_to_trash
        total_bytes = sum(photo.size for photo in marked)

        if self.context.settings.confirm_deletions:
            box = QtWidgets.QMessageBox(self)
            box.setWindowTitle("Delete marked photos")
            box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
            box.setText(
                f"Delete {len(marked)} photo{'s' if len(marked) != 1 else ''} "
                f"({human_size(total_bytes)})?"
            )
            box.setInformativeText(
                "They will be moved to the desktop trash, so you can put them back."
                if to_trash
                else "They will be deleted permanently. This cannot be undone."
            )
            box.setDetailedText("\n".join(photo.path for photo in marked[:200]))
            box.setStandardButtons(
                QtWidgets.QMessageBox.StandardButton.Cancel
                | QtWidgets.QMessageBox.StandardButton.Yes
            )
            box.setDefaultButton(QtWidgets.QMessageBox.StandardButton.Cancel)
            if box.exec() != QtWidgets.QMessageBox.StandardButton.Yes:
                return

        deleted, failures = library.delete_photos(marked, to_trash=to_trash)
        failed_paths = {path for path, _ in failures}
        removed_ids = {photo.id for photo in marked if photo.path not in failed_paths}
        for group in self._groups:
            group.photos = [p for p in group.photos if p.id not in removed_ids]
        self._groups = [g for g in self._groups if len(g.photos) > 1]
        for index, group in enumerate(self._groups, start=1):
            group.index = index
        self._populate_list()
        self.compare.clear()
        self._current = None

        where = "moved to trash" if to_trash else "deleted"
        self.context.set_status(f"{deleted} photos {where}")
        if failures:
            self.context.notify(
                "Some photos could not be deleted",
                "\n".join(f"{path}: {error}" for path, error in failures[:8]),
                "error",
            )
        self.context.library_changed()
        self.refresh()

    # ------------------------------------------------------------------
    def _update_actions(self) -> None:
        has_groups = bool(self._groups)
        self.auto_button.setEnabled(has_groups)
        self.clear_button.setEnabled(has_groups)
        self.keep_best_button.setEnabled(self._current is not None)
        self.skip_button.setEnabled(self._current is not None)
