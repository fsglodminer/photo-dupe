"""The Settings page: similarity thresholds, ranking weights and behaviour."""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

from PySide6 import QtCore, QtWidgets

from ...config import QualityWeights, Settings
from ...hashing import HASH_BITS
from ...quality import METRIC_LABELS
from ..icons import icon
from ..widgets import card, make_label
from .base import Page, form


class SettingsPage(Page):
    title = "Settings"
    hint = (
        "How strict the duplicate detector is, what 'good' means when ranking, and "
        "how the app behaves. Changing the ranking weights re-scores the library "
        "instantly -- no rescan needed."
    )

    def __init__(self, context, parent=None) -> None:
        super().__init__(context, parent)
        palette = self.palette_
        self._loading = False

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(inner)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(14)

        # --- similarity -------------------------------------------------
        self.similarity = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.similarity.setRange(0, 24)
        self.similarity.valueChanged.connect(self._on_similarity)
        self.similarity_label = make_label("", "Muted", wrap=True)

        self.dhash_threshold = QtWidgets.QSpinBox()
        self.dhash_threshold.setRange(0, HASH_BITS)
        self.dhash_threshold.setToolTip(
            "Second opinion on a candidate pair. Lower means stricter."
        )
        self.color_threshold = QtWidgets.QDoubleSpinBox()
        self.color_threshold.setRange(0.0, 1.0)
        self.color_threshold.setSingleStep(0.02)
        self.color_threshold.setDecimals(2)
        self.color_threshold.setToolTip(
            "How different two photos' colours may be and still count as the same "
            "picture."
        )
        self.detect_rotations = QtWidgets.QCheckBox(
            "Also match rotated copies (slower scans)"
        )
        self.group_exact = QtWidgets.QCheckBox(
            "Group byte-for-byte identical files separately"
        )

        similarity_card = card(
            make_label("DUPLICATE DETECTION", "StatLabel"),
            make_label(
                "How different two photos may look and still be treated as the same "
                "picture.", "Muted", wrap=True,
            ),
            self.similarity,
            self.similarity_label,
            _widget(
                form(
                    ("Structure check (0-64)", self.dhash_threshold),
                    ("Colour tolerance (0-1)", self.color_threshold),
                )
            ),
            self.detect_rotations,
            self.group_exact,
        )
        layout.addWidget(similarity_card)

        # --- ranking weights --------------------------------------------
        self.weight_sliders: dict[str, QtWidgets.QSlider] = {}
        self.weight_values: dict[str, QtWidgets.QLabel] = {}
        weights_form = QtWidgets.QFormLayout()
        weights_form.setHorizontalSpacing(16)
        weights_form.setVerticalSpacing(9)
        for field_ in fields(QualityWeights):
            slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
            slider.setRange(0, 50)          # 0.0 - 5.0 in tenths
            slider.valueChanged.connect(self._on_weight_changed)
            value_label = make_label("", "Muted")
            value_label.setFixedWidth(38)
            holder = QtWidgets.QWidget()
            holder_layout = QtWidgets.QHBoxLayout(holder)
            holder_layout.setContentsMargins(0, 0, 0, 0)
            holder_layout.setSpacing(8)
            holder_layout.addWidget(slider, 1)
            holder_layout.addWidget(value_label)
            weights_form.addRow(
                make_label(METRIC_LABELS.get(field_.name, field_.name), "Muted"), holder
            )
            self.weight_sliders[field_.name] = slider
            self.weight_values[field_.name] = value_label

        self.prefer_larger = QtWidgets.QCheckBox(
            "When scores tie, prefer the larger / higher resolution file"
        )
        reset_weights = QtWidgets.QPushButton("Reset to defaults")
        reset_weights.setIcon(icon("refresh", palette.text, 16))
        reset_weights.clicked.connect(self._reset_weights)
        reset_row = QtWidgets.QWidget()
        reset_layout = QtWidgets.QHBoxLayout(reset_row)
        reset_layout.setContentsMargins(0, 0, 0, 0)
        reset_layout.addWidget(reset_weights)
        reset_layout.addStretch(1)

        layout.addWidget(
            card(
                make_label("RANKING WEIGHTS", "StatLabel"),
                make_label(
                    "What matters to you in a photo. Only the ratios count, and the "
                    "library is re-scored as soon as you let go of a slider.",
                    "Muted", wrap=True,
                ),
                _widget(weights_form),
                self.prefer_larger,
                reset_row,
            )
        )

        # --- scanning ----------------------------------------------------
        self.include_raw = QtWidgets.QCheckBox("Include camera RAW files (.cr2, .nef, .arw, ...)")
        self.follow_symlinks = QtWidgets.QCheckBox("Follow symbolic links while scanning")
        self.threads = QtWidgets.QSpinBox()
        self.threads.setRange(0, 64)
        self.threads.setSpecialValueText("Automatic")
        self.threads.setToolTip("How many photos to analyse at once. 0 picks a sensible value.")
        self.analysis_edge = QtWidgets.QComboBox()
        for size in (384, 512, 768, 1024):
            self.analysis_edge.addItem(f"{size} px  ({_speed_hint(size)})", size)
        self.min_bytes = QtWidgets.QSpinBox()
        self.min_bytes.setRange(0, 10_000_000)
        self.min_bytes.setSingleStep(1024)
        self.min_bytes.setSuffix(" bytes")
        self.min_bytes.setToolTip("Files smaller than this are ignored (icons, sprites).")

        layout.addWidget(
            card(
                make_label("SCANNING", "StatLabel"),
                self.include_raw,
                self.follow_symlinks,
                _widget(
                    form(
                        ("Parallel workers", self.threads),
                        ("Analysis resolution", self.analysis_edge),
                        ("Ignore files under", self.min_bytes),
                    )
                ),
            )
        )

        # --- behaviour ----------------------------------------------------
        self.theme = QtWidgets.QComboBox()
        self.theme.addItem("Dark", "dark")
        self.theme.addItem("Light", "light")
        self.theme.currentIndexChanged.connect(self._on_theme)
        self.trash = QtWidgets.QCheckBox("Send deleted photos to the desktop trash")
        self.confirm = QtWidgets.QCheckBox("Ask before deleting anything")
        self.database_label = make_label("", "Muted", wrap=True)
        reset_database = QtWidgets.QPushButton("Empty the index")
        reset_database.setIcon(icon("trash", palette.text, 16))
        reset_database.setToolTip(
            "Forget every indexed photo. Your photo files are not touched."
        )
        reset_database.clicked.connect(self._reset_database)
        database_row = QtWidgets.QWidget()
        database_layout = QtWidgets.QHBoxLayout(database_row)
        database_layout.setContentsMargins(0, 0, 0, 0)
        database_layout.addWidget(reset_database)
        database_layout.addStretch(1)

        layout.addWidget(
            card(
                make_label("BEHAVIOUR", "StatLabel"),
                _widget(form(("Theme", self.theme))),
                self.trash,
                self.confirm,
                self.database_label,
                database_row,
            )
        )
        layout.addStretch(1)
        scroll.setWidget(inner)
        self.body.addWidget(scroll, 1)

        # Persist whenever anything changes.
        for widget in (
            self.dhash_threshold, self.color_threshold, self.threads, self.min_bytes,
        ):
            widget.valueChanged.connect(self._save)
        for checkbox in (
            self.detect_rotations, self.group_exact, self.prefer_larger,
            self.include_raw, self.follow_symlinks, self.trash, self.confirm,
        ):
            checkbox.toggled.connect(self._save)
        self.analysis_edge.currentIndexChanged.connect(self._save)
        self.similarity.sliderReleased.connect(self._save)
        for slider in self.weight_sliders.values():
            slider.sliderReleased.connect(self._apply_weights)

        self.load()

    # ------------------------------------------------------------------
    def load(self) -> None:
        self._loading = True
        settings = self.context.settings
        self.similarity.setValue(settings.similarity_threshold)
        self._on_similarity(settings.similarity_threshold)
        self.dhash_threshold.setValue(settings.dhash_threshold)
        self.color_threshold.setValue(settings.color_threshold)
        self.detect_rotations.setChecked(settings.detect_rotations)
        self.group_exact.setChecked(settings.group_exact_duplicates)
        for name, slider in self.weight_sliders.items():
            slider.setValue(int(round(getattr(settings.weights, name) * 10)))
        self.prefer_larger.setChecked(settings.prefer_larger_on_tie)
        self.include_raw.setChecked(settings.include_raw)
        self.follow_symlinks.setChecked(settings.follow_symlinks)
        self.threads.setValue(settings.worker_threads)
        index = self.analysis_edge.findData(settings.analysis_max_edge)
        self.analysis_edge.setCurrentIndex(index if index >= 0 else 1)
        self.min_bytes.setValue(settings.min_file_bytes)
        self.theme.setCurrentIndex(1 if settings.theme == "light" else 0)
        self.trash.setChecked(settings.delete_to_trash)
        self.confirm.setChecked(settings.confirm_deletions)
        self._refresh_weight_labels()
        self._loading = False
        self.refresh()

    def refresh(self) -> None:
        path = Path(self.context.settings.database_path)
        try:
            size = path.stat().st_size
            from ...imaging import human_size

            detail = f"{human_size(size)}"
        except OSError:
            detail = "not created yet"
        self.database_label.setText(f"Index database: {path}  ({detail})")

    # ------------------------------------------------------------------
    def _on_similarity(self, value: int) -> None:
        if value <= 2:
            wording = "Only near-identical files. Fewest false matches."
        elif value <= 6:
            wording = "Strict: re-saved and resized copies of the same photo."
        elif value <= 12:
            wording = "Balanced: also catches edited and cropped versions. Recommended."
        elif value <= 18:
            wording = "Loose: catches more, but may group photos that merely resemble each other."
        else:
            wording = "Very loose: expect unrelated photos to be grouped together."
        self.similarity_label.setText(f"Threshold {value} of {HASH_BITS} bits - {wording}")
        if not self._loading:
            self.context.settings.similarity_threshold = value

    def _on_weight_changed(self) -> None:
        self._refresh_weight_labels()

    def _refresh_weight_labels(self) -> None:
        total = sum(slider.value() for slider in self.weight_sliders.values()) or 1
        for name, slider in self.weight_sliders.items():
            share = 100.0 * slider.value() / total
            self.weight_values[name].setText(f"{share:.0f}%")

    def _apply_weights(self) -> None:
        if self._loading:
            return
        weights = self.context.settings.weights
        for name, slider in self.weight_sliders.items():
            setattr(weights, name, slider.value() / 10.0)
        self._save()
        count = self.context.library.rescore_all()
        self.context.set_status(f"Re-scored {count:,} photos with the new weights")
        self.context.library_changed()

    def _reset_weights(self) -> None:
        defaults = QualityWeights()
        self.context.settings.weights = defaults
        self._loading = True
        for name, slider in self.weight_sliders.items():
            slider.setValue(int(round(getattr(defaults, name) * 10)))
        self._loading = False
        self._refresh_weight_labels()
        self._apply_weights()

    def _on_theme(self) -> None:
        if self._loading:
            return
        self.context.settings.theme = self.theme.currentData()
        self.context.apply_theme()
        self._save()

    def _save(self) -> None:
        if self._loading:
            return
        settings = self.context.settings
        settings.similarity_threshold = self.similarity.value()
        settings.dhash_threshold = self.dhash_threshold.value()
        settings.color_threshold = self.color_threshold.value()
        settings.detect_rotations = self.detect_rotations.isChecked()
        settings.group_exact_duplicates = self.group_exact.isChecked()
        settings.prefer_larger_on_tie = self.prefer_larger.isChecked()
        settings.include_raw = self.include_raw.isChecked()
        settings.follow_symlinks = self.follow_symlinks.isChecked()
        settings.worker_threads = self.threads.value()
        settings.analysis_max_edge = self.analysis_edge.currentData() or 512
        settings.min_file_bytes = self.min_bytes.value()
        settings.delete_to_trash = self.trash.isChecked()
        settings.confirm_deletions = self.confirm.isChecked()
        settings.save()

    def _reset_database(self) -> None:
        answer = QtWidgets.QMessageBox.question(
            self,
            "Empty the index?",
            "Every photo will be forgotten and the next scan will start from "
            "scratch.\n\nYour photo files are not touched.",
            QtWidgets.QMessageBox.StandardButton.Cancel
            | QtWidgets.QMessageBox.StandardButton.Yes,
            QtWidgets.QMessageBox.StandardButton.Cancel,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self.context.library.db.clear()
        self.context.set_status("Index emptied")
        self.context.library_changed()
        self.refresh()


def _widget(layout: QtWidgets.QLayout) -> QtWidgets.QWidget:
    holder = QtWidgets.QWidget()
    holder.setLayout(layout)
    return holder


def _speed_hint(size: int) -> str:
    return {
        384: "fastest, slightly coarser",
        512: "recommended",
        768: "slower, more precise",
        1024: "slowest, most precise",
    }.get(size, "")
