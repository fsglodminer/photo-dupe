"""User settings and on-disk locations.

Settings live in ``~/.config/photo-dupe/settings.json`` (or ``$XDG_CONFIG_HOME``)
and are a flat, forward-compatible JSON object: unknown keys are ignored on load
so downgrading never destroys a config written by a newer version.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from . import __app_id__

log = logging.getLogger(__name__)

#: Extensions we attempt to read. HEIC/HEIF only work when ``pillow-heif`` is
#: installed; they stay in the list so the user gets a clear error instead of
#: silently skipped files.
IMAGE_EXTENSIONS: tuple[str, ...] = (
    ".jpg", ".jpeg", ".jpe", ".jfif",
    ".png", ".gif", ".bmp", ".webp",
    ".tif", ".tiff",
    ".heic", ".heif",
    ".avif",
    ".ppm", ".pgm", ".tga", ".ico",
)

RAW_EXTENSIONS: tuple[str, ...] = (
    ".cr2", ".cr3", ".nef", ".arw", ".dng", ".orf", ".rw2", ".raf", ".pef", ".srw",
)


def _xdg(var: str, default: str) -> Path:
    value = os.environ.get(var, "").strip()
    base = Path(value) if value else Path.home() / default
    return base


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / __app_id__


def data_dir() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share") / __app_id__


def cache_dir() -> Path:
    return _xdg("XDG_CACHE_HOME", ".cache") / __app_id__


def thumbnail_dir() -> Path:
    return cache_dir() / "thumbnails"


def default_database_path() -> Path:
    return data_dir() / "library.db"


def default_library_root() -> Path:
    return Path.home() / "Pictures" / "PhotoDupe Library"


def settings_path() -> Path:
    return config_dir() / "settings.json"


@dataclass
class QualityWeights:
    """Relative importance of each quality metric when ranking.

    Weights are normalised before use, so only their ratios matter.
    """

    sharpness: float = 3.0
    exposure: float = 2.0
    contrast: float = 1.5
    colorfulness: float = 1.0
    noise: float = 1.5
    resolution: float = 1.5
    detail: float = 1.0

    def normalised(self) -> dict[str, float]:
        raw = {f.name: max(0.0, float(getattr(self, f.name))) for f in fields(self)}
        total = sum(raw.values())
        if total <= 0:
            # Degenerate configuration: fall back to an equal split rather than
            # dividing by zero or silently ranking everything identically.
            n = len(raw)
            return {k: 1.0 / n for k in raw}
        return {k: v / total for k, v in raw.items()}


@dataclass
class Settings:
    """Everything the user can tune, persisted as JSON."""

    # --- library -------------------------------------------------------
    database_path: str = ""
    library_root: str = ""
    watched_folders: list[str] = field(default_factory=list)
    include_raw: bool = False
    follow_symlinks: bool = False
    min_file_bytes: int = 4096

    # --- similarity ----------------------------------------------------
    #: Max Hamming distance (out of 64) between perceptual hashes for two
    #: photos to be considered near-duplicates. Lower = stricter.
    similarity_threshold: int = 10
    #: Secondary check on the difference hash; guards against pHash collisions.
    dhash_threshold: int = 16
    #: Max colour-signature distance (0..1). Guards against structurally similar
    #: but differently coloured photos being grouped.
    color_threshold: float = 0.28
    detect_rotations: bool = False
    group_exact_duplicates: bool = True

    # --- ranking -------------------------------------------------------
    weights: QualityWeights = field(default_factory=QualityWeights)
    prefer_larger_on_tie: bool = True

    # --- import --------------------------------------------------------
    import_mode: str = "copy"            # copy | move
    import_organise: str = "date"        # flat | date | source
    import_skip_exact: bool = True
    import_skip_similar: bool = False

    # --- export --------------------------------------------------------
    export_mode: str = "copy"            # copy | move | symlink
    export_naming: str = "original"      # original | rank | score | date
    export_group_folders: bool = False
    export_manifest: str = "csv"         # none | csv | json
    export_max_edge: int = 0             # 0 = keep original pixels
    export_jpeg_quality: int = 92

    # --- performance / ui ----------------------------------------------
    worker_threads: int = 0              # 0 = auto (cpu_count - 1)
    thumbnail_size: int = 256
    analysis_max_edge: int = 512
    theme: str = "dark"                  # dark | light
    grid_icon_size: int = 180
    confirm_deletions: bool = True
    delete_to_trash: bool = True

    # ------------------------------------------------------------------
    def __post_init__(self) -> None:
        if not self.database_path:
            self.database_path = str(default_database_path())
        if not self.library_root:
            self.library_root = str(default_library_root())
        if isinstance(self.weights, dict):  # tolerate raw JSON
            self.weights = QualityWeights(**self.weights)

    # ------------------------------------------------------------------
    def extensions(self) -> tuple[str, ...]:
        return IMAGE_EXTENSIONS + RAW_EXTENSIONS if self.include_raw else IMAGE_EXTENSIONS

    def thread_count(self) -> int:
        if self.worker_threads > 0:
            return self.worker_threads
        return max(1, (os.cpu_count() or 2) - 1)

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Settings":
        known = {f.name for f in fields(cls)}
        kwargs: dict[str, Any] = {}
        for key, value in raw.items():
            if key not in known:
                continue  # forward compatibility: ignore unknown keys
            kwargs[key] = value
        weights = kwargs.get("weights")
        if isinstance(weights, dict):
            valid = {f.name for f in fields(QualityWeights)}
            kwargs["weights"] = QualityWeights(
                **{k: v for k, v in weights.items() if k in valid}
            )
        try:
            return cls(**kwargs)
        except TypeError:
            log.warning("settings file has an unusable shape; using defaults")
            return cls()

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or settings_path()
        try:
            raw = json.loads(path.read_text("utf-8"))
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError) as exc:
            log.warning("could not read settings from %s (%s); using defaults", path, exc)
            return cls()
        if not isinstance(raw, dict):
            return cls()
        return cls.from_dict(raw)

    def save(self, path: Path | None = None) -> None:
        path = path or settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True), "utf-8")
        tmp.replace(path)  # atomic: never leave a half-written settings file


def ensure_app_dirs() -> None:
    for directory in (config_dir(), data_dir(), cache_dir(), thumbnail_dir()):
        directory.mkdir(parents=True, exist_ok=True)
