"""The Library page: which folders to watch, and what is in the index."""

from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtWidgets

from ...imaging import human_size
from ...library import ScanResult
from ..icons import icon
from ..widgets import FolderList, StatCard, card, make_label
from .base import Page, row


class LibraryPage(Page):
    title = "Library"
    hint = (
        "Point Photo Dupe at the folders holding your photos. Scanning reads each "
        "file once, measures its quality and remembers the result, so later scans "
        "only look at what changed."
    )

    def __init__(self, context, parent=None) -> None:
        super().__init__(context, parent)
        palette = self.palette_

        # --- statistics ------------------------------------------------
        self.stat_photos = StatCard("Photos indexed")
        self.stat_size = StatCard("On disk")
        self.stat_score = StatCard("Average score")
        self.stat_pixels = StatCard("Total pixels")
        self.stat_errors = StatCard("Unreadable")
        stats = QtWidgets.QHBoxLayout()
        stats.setSpacing(12)
        for widget in (
            self.stat_photos, self.stat_size, self.stat_score,
            self.stat_pixels, self.stat_errors,
        ):
            stats.addWidget(widget, 1)
        self.body.addLayout(stats)

        # --- folders ---------------------------------------------------
        self.folders = FolderList(palette, "Add folder to watch...")
        self.folders.changed.connect(self._save_folders)

        self.scan_button = QtWidgets.QPushButton("Scan now")
        self.scan_button.setObjectName("Primary")
        self.scan_button.setIcon(icon("search", palette.accent_text, 16))
        self.scan_button.clicked.connect(lambda: self.start_scan(force=False))

        self.rescan_button = QtWidgets.QPushButton("Full re-analysis")
        self.rescan_button.setIcon(icon("refresh", palette.text, 16))
        self.rescan_button.setToolTip(
            "Re-read every file even if it has not changed. Use this after "
            "changing the analysis settings."
        )
        self.rescan_button.clicked.connect(lambda: self.start_scan(force=True))

        self.cancel_button = QtWidgets.QPushButton("Stop")
        self.cancel_button.setIcon(icon("cross", palette.text, 16))
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self._cancel)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setVisible(False)
        self.progress.setTextVisible(False)
        self.progress_label = make_label("", "Muted")
        self.progress_label.setVisible(False)

        self.body.addWidget(
            card(
                make_label("WATCHED FOLDERS", "StatLabel"),
                self.folders,
                _as_widget(
                    row(self.scan_button, self.rescan_button, self.cancel_button)
                ),
                self.progress,
                self.progress_label,
            )
        )

        # --- problems --------------------------------------------------
        self.issues_title = make_label("FILES THAT COULD NOT BE READ", "StatLabel")
        self.issues = QtWidgets.QPlainTextEdit()
        self.issues.setReadOnly(True)
        self.issues.setMaximumHeight(120)
        self.issues_card = card(self.issues_title, self.issues)
        self.issues_card.setVisible(False)
        self.body.addWidget(self.issues_card)
        self.body.addStretch(1)

        self._worker = None

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        settings = self.context.settings
        if self.folders.folders() != settings.watched_folders:
            self.folders.set_folders(settings.watched_folders)
        stats = self.context.library.db.statistics()
        self.stat_photos.set_value(f"{stats['total']:,}")
        self.stat_size.set_value(human_size(stats["bytes"]))
        self.stat_score.set_value(
            f"{stats['mean_score']:.1f}" if stats["total"] else "-"
        )
        self.stat_pixels.set_value(f"{stats['megapixels']:,.0f} MP")
        self.stat_errors.set_value(f"{stats['errors']:,}")
        self.scan_button.setEnabled(bool(self.folders.folders()))

    def _save_folders(self) -> None:
        self.context.settings.watched_folders = self.folders.folders()
        self.context.settings.save()
        self.scan_button.setEnabled(bool(self.folders.folders()))

    # ------------------------------------------------------------------
    def start_scan(self, force: bool = False) -> None:
        folders = self.folders.folders()
        if not folders:
            self.context.notify(
                "Nothing to scan",
                "Add at least one folder before scanning.",
                "warning",
            )
            return
        self._save_folders()
        self._set_busy(True)
        worker = self.context.run_worker(
            "scan",
            self.context.library.scan,
            [Path(folder) for folder in folders],
            force=force,
        )
        if worker is None:
            self._set_busy(False)
            return
        self._worker = worker
        worker.progressed.connect(self._on_progress)
        worker.succeeded.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        worker.done.connect(lambda: self._set_busy(False))

    def _cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.progress_label.setText("Stopping...")

    def _set_busy(self, busy: bool) -> None:
        self.scan_button.setEnabled(not busy and bool(self.folders.folders()))
        self.rescan_button.setEnabled(not busy)
        self.folders.setEnabled(not busy)
        self.cancel_button.setVisible(busy)
        self.progress.setVisible(busy)
        self.progress_label.setVisible(busy)
        if not busy:
            self._worker = None

    @QtCore.Slot(int, int, str)
    def _on_progress(self, done: int, total: int, message: str) -> None:
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(done)
            self.progress_label.setText(f"{done} of {total} - {message}")
        else:
            self.progress.setRange(0, 0)
            self.progress_label.setText(message)

    @QtCore.Slot(object)
    def _on_finished(self, result: ScanResult) -> None:
        self.context.set_status(result.summary())
        if result.errors:
            self.issues.setPlainText(
                "\n".join(f"{Path(path).name}  --  {error}" for path, error in result.errors)
            )
            self.issues_title.setText(
                f"FILES THAT COULD NOT BE READ ({len(result.errors)})"
            )
        self.issues_card.setVisible(bool(result.errors))
        self.context.library_changed()
        self.refresh()

    @QtCore.Slot(str)
    def _on_failed(self, message: str) -> None:
        self.context.notify("Scan failed", message, "error")


def _as_widget(layout: QtWidgets.QLayout) -> QtWidgets.QWidget:
    holder = QtWidgets.QWidget()
    holder.setLayout(layout)
    return holder
