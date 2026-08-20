"""The Import and Export pages -- getting photos in and out of the library."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from PySide6 import QtCore, QtWidgets

from ...exporter import (
    MODE_CHOICES,
    NAMING_CHOICES,
    above_score,
    export_photos,
    keepers_only,
    top_ranked,
)
from ...imaging import human_size
from ...importer import import_photos
from ...records import MARK_DELETE, MARK_KEEP, Photo
from ..icons import icon
from ..widgets import FolderList, card, make_label
from .base import Page, form


class _TransferPage(Page):
    """Shared plumbing: a destination picker, a run button and a result log."""

    def _make_log(self) -> QtWidgets.QPlainTextEdit:
        log = QtWidgets.QPlainTextEdit()
        log.setReadOnly(True)
        log.setMinimumHeight(120)
        log.setPlaceholderText("Results will appear here.")
        return log

    def _make_progress(self) -> tuple[QtWidgets.QProgressBar, QtWidgets.QLabel]:
        progress = QtWidgets.QProgressBar()
        progress.setVisible(False)
        progress.setTextVisible(False)
        label = make_label("", "Muted")
        label.setVisible(False)
        return progress, label

    def _update_progress(
        self,
        progress: QtWidgets.QProgressBar,
        label: QtWidgets.QLabel,
        done: int,
        total: int,
        message: str,
    ) -> None:
        if total > 0:
            progress.setRange(0, total)
            progress.setValue(done)
            label.setText(f"{done} of {total} - {message}")
        else:
            progress.setRange(0, 0)
            label.setText(message)

    def _pick_folder(self, line_edit: QtWidgets.QLineEdit, caption: str) -> None:
        start = line_edit.text().strip() or str(Path.home())
        chosen = QtWidgets.QFileDialog.getExistingDirectory(self, caption, start)
        if chosen:
            line_edit.setText(chosen)


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

class ImportPage(_TransferPage):
    title = "Import"
    hint = (
        "Copy or move photos from a camera, memory card or any folder into your "
        "library. Duplicates can be turned away at the door, which is the cheapest "
        "place to catch them."
    )

    def __init__(self, context, parent=None) -> None:
        super().__init__(context, parent)
        palette = self.palette_
        settings = context.settings
        self._worker = None

        self.sources = FolderList(palette, "Add source folder...")

        self.destination = QtWidgets.QLineEdit(settings.library_root)
        browse = QtWidgets.QPushButton("Browse...")
        browse.setIcon(icon("folder", palette.text, 16))
        browse.clicked.connect(
            lambda: self._pick_folder(self.destination, "Choose the library folder")
        )
        destination_row = QtWidgets.QWidget()
        destination_layout = QtWidgets.QHBoxLayout(destination_row)
        destination_layout.setContentsMargins(0, 0, 0, 0)
        destination_layout.setSpacing(8)
        destination_layout.addWidget(self.destination, 1)
        destination_layout.addWidget(browse)

        self.mode = QtWidgets.QComboBox()
        self.mode.addItem("Copy - leave the originals where they are", "copy")
        self.mode.addItem("Move - remove the originals after copying", "move")
        self.mode.setCurrentIndex(1 if settings.import_mode == "move" else 0)

        self.organise = QtWidgets.QComboBox()
        self.organise.addItem("By capture date (2024/2024-06)", "date")
        self.organise.addItem("All in one folder", "flat")
        self.organise.addItem("Mirror the source folder layout", "source")
        self.organise.setCurrentIndex(
            {"date": 0, "flat": 1, "source": 2}.get(settings.import_organise, 0)
        )

        self.skip_exact = QtWidgets.QCheckBox(
            "Skip files already in the library (identical bytes)"
        )
        self.skip_exact.setChecked(settings.import_skip_exact)
        self.skip_similar = QtWidgets.QCheckBox(
            "Also skip photos that merely look the same (slower - decodes every file)"
        )
        self.skip_similar.setChecked(settings.import_skip_similar)
        self.index_after = QtWidgets.QCheckBox("Index the imported photos afterwards")
        self.index_after.setChecked(True)

        self.dry_run_button = QtWidgets.QPushButton("Preview (change nothing)")
        self.dry_run_button.setIcon(icon("info", palette.text, 16))
        self.dry_run_button.clicked.connect(lambda: self._start(dry_run=True))
        self.run_button = QtWidgets.QPushButton("Import photos")
        self.run_button.setObjectName("Primary")
        self.run_button.setIcon(icon("import", palette.accent_text, 16))
        self.run_button.clicked.connect(lambda: self._start(dry_run=False))
        self.cancel_button = QtWidgets.QPushButton("Stop")
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self._cancel)

        buttons = QtWidgets.QWidget()
        button_layout = QtWidgets.QHBoxLayout(buttons)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(9)
        button_layout.addWidget(self.run_button)
        button_layout.addWidget(self.dry_run_button)
        button_layout.addWidget(self.cancel_button)
        button_layout.addStretch(1)

        self.progress, self.progress_label = self._make_progress()
        self.log = self._make_log()

        options = QtWidgets.QWidget()
        options.setLayout(
            form(
                ("Import into", destination_row),
                ("Files", self.mode),
                ("Organise", self.organise),
            )
        )

        self.body.addWidget(
            card(
                make_label("IMPORT FROM", "StatLabel"),
                self.sources,
                options,
                self.skip_exact,
                self.skip_similar,
                self.index_after,
                buttons,
                self.progress,
                self.progress_label,
            )
        )
        self.body.addWidget(card(make_label("RESULT", "StatLabel"), self.log), 1)

    # ------------------------------------------------------------------
    def _collect_settings(self) -> None:
        settings = self.context.settings
        settings.library_root = self.destination.text().strip() or settings.library_root
        settings.import_mode = self.mode.currentData()
        settings.import_organise = self.organise.currentData()
        settings.import_skip_exact = self.skip_exact.isChecked()
        settings.import_skip_similar = self.skip_similar.isChecked()
        settings.save()

    def _start(self, dry_run: bool) -> None:
        sources = self.sources.folders()
        if not sources:
            self.context.notify(
                "Nothing to import", "Add at least one source folder first.", "warning"
            )
            return
        self._collect_settings()
        settings = self.context.settings

        if settings.import_mode == "move" and not dry_run:
            answer = QtWidgets.QMessageBox.question(
                self,
                "Move photos?",
                f"Photos will be removed from their source folders and moved into\n"
                f"{settings.library_root}\n\nContinue?",
                QtWidgets.QMessageBox.StandardButton.Cancel
                | QtWidgets.QMessageBox.StandardButton.Yes,
                QtWidgets.QMessageBox.StandardButton.Cancel,
            )
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                return

        self._set_busy(True)
        self.log.setPlainText("Working...")
        worker = self.context.run_worker(
            "import",
            import_photos,
            sources,
            settings,
            self.context.library.db,
            dry_run=dry_run,
        )
        if worker is None:
            self._set_busy(False)
            return
        self._worker = worker
        worker.progressed.connect(
            lambda done, total, message: self._update_progress(
                self.progress, self.progress_label, done, total, message
            )
        )
        worker.succeeded.connect(lambda result: self._finished(result, dry_run))
        worker.failed.connect(
            lambda message: self.context.notify("Import failed", message, "error")
        )
        worker.done.connect(lambda: self._set_busy(False))

    def _cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()

    def _set_busy(self, busy: bool) -> None:
        for widget in (self.run_button, self.dry_run_button, self.sources, self.destination):
            widget.setEnabled(not busy)
        self.cancel_button.setVisible(busy)
        self.progress.setVisible(busy)
        self.progress_label.setVisible(busy)
        if not busy:
            self._worker = None

    def _finished(self, result, dry_run: bool) -> None:
        lines = [result.summary()]
        if dry_run:
            lines.insert(0, "PREVIEW ONLY - nothing was written.\n")
        if result.bytes_copied:
            lines.append(f"Data handled: {human_size(result.bytes_copied)}")
        lines.append(f"Time: {result.seconds:.1f}s")
        if result.rejected:
            lines.append(f"\nTurned away ({len(result.rejected)}):")
            for source, reason, existing in result.rejected[:60]:
                lines.append(f"  {Path(source).name}  --  {reason}  {Path(existing).name}")
            if len(result.rejected) > 60:
                lines.append(f"  ... and {len(result.rejected) - 60} more")
        if result.errors:
            lines.append(f"\nFailed ({len(result.errors)}):")
            for path, error in result.errors[:40]:
                lines.append(f"  {Path(path).name}  --  {error}")
        if result.files:
            lines.append(f"\nImported ({len(result.files)}):")
            for source, target in result.files[:60]:
                lines.append(f"  {Path(source).name}  ->  {target}")
            if len(result.files) > 60:
                lines.append(f"  ... and {len(result.files) - 60} more")
        self.log.setPlainText("\n".join(lines))
        self.context.set_status(result.summary())

        if result.imported and not dry_run and self.index_after.isChecked():
            library_root = Path(self.context.settings.library_root)
            settings = self.context.settings
            if str(library_root) not in settings.watched_folders:
                settings.watched_folders.append(str(library_root))
                settings.save()
            self.context.set_status("Indexing imported photos...")
            worker = self.context.run_worker(
                "scan", self.context.library.scan, [library_root]
            )
            if worker is not None:
                worker.succeeded.connect(
                    lambda scan_result: (
                        self.context.set_status(scan_result.summary()),
                        self.context.library_changed(),
                    )
                )
        elif result.imported and not dry_run:
            self.context.library_changed()


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

class ExportPage(_TransferPage):
    title = "Export"
    hint = (
        "Write photos out of the library -- the whole thing, the best of every "
        "duplicate group, the top of the ranking, or whatever you selected on the "
        "Ranking page."
    )

    SELECTIONS = [
        ("all", "Every indexed photo"),
        ("keepers", "Best of each duplicate group (skip the surplus)"),
        ("top", "Top N by score"),
        ("min_score", "Everything scoring at least..."),
        ("marked_keep", "Photos marked as keepers"),
        ("selection", "The photos selected on the Ranking page"),
    ]

    def __init__(self, context, parent=None) -> None:
        super().__init__(context, parent)
        palette = self.palette_
        settings = context.settings
        self._worker = None
        self._group_worker = None
        self._explicit: list[Photo] = []

        self.selection = QtWidgets.QComboBox()
        for key, label in self.SELECTIONS:
            self.selection.addItem(label, key)
        self.selection.currentIndexChanged.connect(self._on_selection_changed)

        self.top_count = QtWidgets.QSpinBox()
        self.top_count.setRange(1, 100000)
        self.top_count.setValue(100)
        self.top_count.valueChanged.connect(lambda _: self._update_preview())

        self.threshold = QtWidgets.QSpinBox()
        self.threshold.setRange(0, 100)
        self.threshold.setValue(70)
        self.threshold.setSuffix(" points")
        self.threshold.valueChanged.connect(lambda _: self._update_preview())

        self.destination = QtWidgets.QLineEdit(str(Path.home() / "Pictures" / "Photo Dupe Export"))
        browse = QtWidgets.QPushButton("Browse...")
        browse.setIcon(icon("folder", palette.text, 16))
        browse.clicked.connect(
            lambda: self._pick_folder(self.destination, "Choose an export folder")
        )
        destination_row = QtWidgets.QWidget()
        destination_layout = QtWidgets.QHBoxLayout(destination_row)
        destination_layout.setContentsMargins(0, 0, 0, 0)
        destination_layout.setSpacing(8)
        destination_layout.addWidget(self.destination, 1)
        destination_layout.addWidget(browse)

        self.mode = QtWidgets.QComboBox()
        for key, label in MODE_CHOICES.items():
            self.mode.addItem(label, key)
        self.mode.setCurrentIndex(
            list(MODE_CHOICES).index(settings.export_mode)
            if settings.export_mode in MODE_CHOICES else 0
        )
        self.naming = QtWidgets.QComboBox()
        for key, label in NAMING_CHOICES.items():
            self.naming.addItem(label, key)
        self.naming.setCurrentIndex(
            list(NAMING_CHOICES).index(settings.export_naming)
            if settings.export_naming in NAMING_CHOICES else 0
        )
        self.manifest = QtWidgets.QComboBox()
        self.manifest.addItem("CSV spreadsheet", "csv")
        self.manifest.addItem("JSON", "json")
        self.manifest.addItem("None", "none")

        self.max_edge = QtWidgets.QSpinBox()
        self.max_edge.setRange(0, 20000)
        self.max_edge.setValue(settings.export_max_edge)
        self.max_edge.setSpecialValueText("Original size")
        self.max_edge.setSuffix(" px long edge")

        self.quality = QtWidgets.QSpinBox()
        self.quality.setRange(50, 100)
        self.quality.setValue(settings.export_jpeg_quality)
        self.quality.setSuffix("% JPEG quality")

        self.group_folders = QtWidgets.QCheckBox("One subfolder per duplicate group")
        self.group_folders.setChecked(settings.export_group_folders)
        self.group_folders.toggled.connect(lambda _: self._update_preview())

        self.preview_label = make_label("", "Muted", wrap=True)
        self.run_button = QtWidgets.QPushButton("Export photos")
        self.run_button.setObjectName("Primary")
        self.run_button.setIcon(icon("export", palette.accent_text, 16))
        self.run_button.clicked.connect(self._start)
        self.cancel_button = QtWidgets.QPushButton("Stop")
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self._cancel)

        buttons = QtWidgets.QWidget()
        button_layout = QtWidgets.QHBoxLayout(buttons)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(9)
        button_layout.addWidget(self.run_button)
        button_layout.addWidget(self.cancel_button)
        button_layout.addStretch(1)

        self.progress, self.progress_label = self._make_progress()
        self.log = self._make_log()

        options = QtWidgets.QWidget()
        options.setLayout(
            form(
                ("What to export", self.selection),
                ("How many", self.top_count),
                ("Minimum score", self.threshold),
                ("Export to", destination_row),
                ("Method", self.mode),
                ("Filenames", self.naming),
                ("Manifest", self.manifest),
                ("Resize", self.max_edge),
                ("Re-encode", self.quality),
            )
        )
        self.body.addWidget(
            card(options, self.group_folders, self.preview_label, buttons,
                 self.progress, self.progress_label)
        )
        self.body.addWidget(card(make_label("RESULT", "StatLabel"), self.log), 1)
        self._on_selection_changed()

    # ------------------------------------------------------------------
    def set_explicit_selection(self, photos: Sequence[Photo]) -> None:
        """Called when the user picks "Export selection" on the Ranking page."""
        self._explicit = list(photos)
        index = self.selection.findData("selection")
        if index >= 0:
            self.selection.setCurrentIndex(index)
        self._update_preview()

    def refresh(self) -> None:
        self._update_preview()

    def _on_selection_changed(self) -> None:
        key = self.selection.currentData()
        self.top_count.setEnabled(key == "top")
        self.threshold.setEnabled(key == "min_score")
        self._update_preview()

    # ------------------------------------------------------------------
    def _needs_groups(self) -> bool:
        return (
            self.selection.currentData() == "keepers"
            or self.group_folders.isChecked()
        )

    def _ensure_groups(self) -> bool:
        """Make sure duplicate groups exist, computing them in the background.

        Returns ``True`` when they are ready now. Grouping a large library takes
        seconds, so it must never run on the GUI thread -- a frozen window is a
        worse answer than a moment's wait with a progress message.
        """
        if self.context.cached_groups() is not None:
            return True
        if self._group_worker is not None:
            return False
        worker = self.context.run_worker(
            "duplicates",
            lambda progress: self.context.library.duplicate_groups(
                progress=lambda done, total: progress(done, total, "Comparing photos")
            ),
        )
        if worker is None:
            return False
        self._group_worker = worker
        worker.succeeded.connect(self._on_groups_ready)
        worker.failed.connect(
            lambda message: self.context.notify(
                "Could not compare photos", message, "error"
            )
        )
        worker.done.connect(self._on_group_worker_done)
        return False

    def _on_groups_ready(self, groups) -> None:
        self.context.set_group_cache(groups)

    def _on_group_worker_done(self) -> None:
        self._group_worker = None
        self._update_preview()

    def _resolve(self) -> list[Photo]:
        key = self.selection.currentData()
        db = self.context.library.db
        if key == "selection":
            return list(self._explicit)
        if key == "marked_keep":
            return db.photos(sort="score", mark=MARK_KEEP)
        everything = db.photos(sort="score")
        if key == "keepers":
            groups = self.context.cached_groups()
            return keepers_only(groups or [], everything) if groups is not None else []
        if key == "top":
            return top_ranked(everything, self.top_count.value())
        if key == "min_score":
            return above_score(everything, float(self.threshold.value()))
        return everything

    def _update_preview(self) -> None:
        if self._needs_groups() and not self._ensure_groups():
            self.preview_label.setText("Working out which photos are duplicates...")
            self.run_button.setEnabled(False)
            return
        try:
            photos = self._resolve()
        except Exception:  # noqa: BLE001 - preview must never break the page
            photos = []
        total_bytes = sum(photo.size for photo in photos)
        if photos:
            self.preview_label.setText(
                f"{len(photos):,} photos, about {human_size(total_bytes)}"
                + ("  (resized copies will be smaller)" if self.max_edge.value() else "")
            )
        else:
            key = self.selection.currentData()
            self.preview_label.setText(
                "Nothing selected on the Ranking page yet."
                if key == "selection"
                else "Nothing matches -- scan a folder first, or loosen the filter."
            )
        self.run_button.setEnabled(bool(photos))

    # ------------------------------------------------------------------
    def _collect_settings(self) -> None:
        settings = self.context.settings
        settings.export_mode = self.mode.currentData()
        settings.export_naming = self.naming.currentData()
        settings.export_manifest = self.manifest.currentData()
        settings.export_max_edge = self.max_edge.value()
        settings.export_jpeg_quality = self.quality.value()
        settings.export_group_folders = self.group_folders.isChecked()
        settings.save()

    def _start(self) -> None:
        destination = self.destination.text().strip()
        if not destination:
            self.context.notify(
                "No destination", "Choose a folder to export into.", "warning"
            )
            return
        photos = self._resolve()
        if not photos:
            return
        self._collect_settings()
        settings = self.context.settings

        if settings.export_mode == "symlink" and settings.export_max_edge:
            self.context.notify(
                "Cannot do both",
                "Symbolic links point at the original file, so they cannot be "
                "resized. Turn resizing off, or export copies instead.",
                "warning",
            )
            return
        if settings.export_mode == "move":
            answer = QtWidgets.QMessageBox.question(
                self,
                "Move photos out of the library?",
                f"{len(photos)} photos will be moved to\n{destination}\n\n"
                f"They will no longer be in their current folders. Continue?",
                QtWidgets.QMessageBox.StandardButton.Cancel
                | QtWidgets.QMessageBox.StandardButton.Yes,
                QtWidgets.QMessageBox.StandardButton.Cancel,
            )
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                return

        groups = self.context.cached_groups() if settings.export_group_folders else None

        self._set_busy(True)
        self.log.setPlainText("Working...")
        worker = self.context.run_worker(
            "export", export_photos, photos, destination, settings, groups=groups
        )
        if worker is None:
            self._set_busy(False)
            return
        self._worker = worker
        worker.progressed.connect(
            lambda done, total, message: self._update_progress(
                self.progress, self.progress_label, done, total, message
            )
        )
        worker.succeeded.connect(self._finished)
        worker.failed.connect(
            lambda message: self.context.notify("Export failed", message, "error")
        )
        worker.done.connect(lambda: self._set_busy(False))

    def _cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()

    def _set_busy(self, busy: bool) -> None:
        self.run_button.setEnabled(not busy)
        self.selection.setEnabled(not busy)
        self.cancel_button.setVisible(busy)
        self.progress.setVisible(busy)
        self.progress_label.setVisible(busy)
        if not busy:
            self._worker = None

    def _finished(self, result) -> None:
        lines = [result.summary(), f"Destination: {result.destination}"]
        if result.manifest:
            lines.append(f"Manifest: {result.manifest}")
        lines.append(f"Time: {result.seconds:.1f}s")
        if result.errors:
            lines.append(f"\nFailed ({len(result.errors)}):")
            for path, error in result.errors[:40]:
                lines.append(f"  {Path(path).name}  --  {error}")
        self.log.setPlainText("\n".join(lines))
        self.context.set_status(result.summary())
        if self.context.settings.export_mode == "move":
            self.context.library_changed()
