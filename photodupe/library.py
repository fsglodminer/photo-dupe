"""The analysis pipeline: disk -> decoded pixels -> fingerprints -> database.

This is the layer the GUI and the CLI both drive. It knows nothing about Qt, so
everything here is testable without a display, and long operations report
through plain callbacks rather than signals.
"""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

from . import config as config_module
from .config import Settings
from .db import Database
from .grouping import build_groups
from .hashing import fingerprint
from .imaging import (
    ImageLoadError,
    camera_label,
    file_digest,
    load_for_analysis,
    thumbnail_key,
    write_thumbnail,
)
from .quality import analyse, rescore
from .records import DuplicateGroup, Photo
from .scanner import FoundFile, walk_images

log = logging.getLogger(__name__)

#: Callback signature: (done, total, message) -> keep going?
ProgressFn = Callable[[int, int, str], bool]


@dataclass
class ScanResult:
    scanned: int = 0
    indexed: int = 0
    skipped: int = 0
    failed: int = 0
    removed: int = 0
    seconds: float = 0.0
    errors: list[tuple[str, str]] = field(default_factory=list)
    cancelled: bool = False

    def summary(self) -> str:
        if self.cancelled:
            return (
                f"Scan cancelled - {self.indexed} new, {self.skipped} unchanged, "
                f"{self.failed} failed"
            )
        parts = [f"{self.indexed} analysed", f"{self.skipped} unchanged"]
        if self.removed:
            parts.append(f"{self.removed} gone")
        if self.failed:
            parts.append(f"{self.failed} failed")
        return f"{self.scanned} photos: " + ", ".join(parts) + f" in {self.seconds:.1f}s"


# ---------------------------------------------------------------------------
# single-file analysis (runs on worker threads)
# ---------------------------------------------------------------------------

def analyse_file(
    found: FoundFile,
    settings: Settings,
    make_thumbnail: bool = True,
) -> tuple[Photo, list[str] | None]:
    """Decode one file and produce a fully populated :class:`Photo`.

    Pure with respect to the database, so it is safe to call from a pool of
    worker threads. Failures come back as a photo with ``status='error'``
    rather than an exception: one unreadable file must not abort a scan of
    twenty thousand.
    """
    path = Path(found.path)
    photo = Photo(
        path=str(path),
        filename=path.name,
        size=found.size,
        mtime=found.mtime,
        indexed_at=time.time(),
    )
    try:
        loaded = load_for_analysis(path, settings.analysis_max_edge)
    except (ImageLoadError, OSError, ValueError) as exc:
        photo.status = "error"
        photo.error = str(exc)
        return photo, None

    try:
        photo.sha256 = file_digest(path)
    except OSError as exc:  # unreadable mid-scan; keep the analysis we have
        log.debug("digest failed for %s: %s", path, exc)

    photo.width = loaded.width
    photo.height = loaded.height
    photo.format = loaded.format
    photo.camera = camera_label(loaded.exif)
    photo.iso = loaded.exif.get("iso")
    photo.taken_at = loaded.exif.get("taken_at")

    prints = fingerprint(loaded.gray, loaded.rgb, with_rotations=settings.detect_rotations)
    photo.ahash = prints["ahash"]
    photo.dhash = prints["dhash"]
    photo.phash = prints["phash"]
    photo.color_sig = prints["color_sig"]

    report = analyse(
        loaded.gray,
        loaded.rgb,
        loaded.megapixels,
        weights=settings.weights,
        exif=loaded.exif,
    )
    photo.score = report.score
    photo.metrics = report.metrics
    photo.raw_metrics = report.raw
    photo.flags = report.flags

    if make_thumbnail:
        try:
            key = thumbnail_key(path, settings.thumbnail_size, found.mtime)
            destination = config_module.thumbnail_dir() / f"{key}.jpg"
            if not destination.exists():
                write_thumbnail(path, destination, settings.thumbnail_size)
            photo.thumbnail = str(destination)
        except Exception as exc:  # a missing thumbnail must not fail the photo
            log.debug("thumbnail failed for %s: %s", path, exc)

    return photo, prints.get("rotations")


# ---------------------------------------------------------------------------
# the library
# ---------------------------------------------------------------------------

class Library:
    """A photo index backed by a database and a set of watched folders."""

    def __init__(self, settings: Settings, database: Database | None = None) -> None:
        self.settings = settings
        self.db = database or Database(settings.database_path)

    # ------------------------------------------------------------------
    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------------
    def scan(
        self,
        roots: Sequence[Path | str] | None = None,
        progress: ProgressFn | None = None,
        force: bool = False,
        prune_missing: bool = True,
    ) -> ScanResult:
        """Index every photo under ``roots``, skipping files that have not changed."""
        started = time.time()
        settings = self.settings
        roots = list(roots) if roots else [Path(p) for p in settings.watched_folders]
        result = ScanResult()
        if not roots:
            return result

        cancelled = {"flag": False}

        def report(done: int, total: int, message: str) -> bool:
            if progress is None:
                return True
            if progress(done, total, message) is False:
                cancelled["flag"] = True
                return False
            return True

        report(0, 0, "Looking for photos...")
        known = self.db.index_signature()
        found_files: list[FoundFile] = []
        for found in walk_images(
            roots,
            settings.extensions(),
            follow_symlinks=settings.follow_symlinks,
            min_bytes=settings.min_file_bytes,
            should_continue=lambda: not cancelled["flag"],
        ):
            found_files.append(found)
            if len(found_files) % 200 == 0:
                if not report(0, 0, f"Found {len(found_files)} photos..."):
                    result.cancelled = True
                    return result

        result.scanned = len(found_files)
        seen_paths = {f.path for f in found_files}

        todo: list[FoundFile] = []
        for found in found_files:
            previous = known.get(found.path)
            unchanged = (
                previous is not None
                and previous[0] == found.size
                and abs(previous[1] - found.mtime) < 1e-6
            )
            if unchanged and not force:
                result.skipped += 1
            else:
                todo.append(found)

        total = len(todo)
        if total:
            workers = min(settings.thread_count(), max(1, total))
            report(0, total, f"Analysing {total} photos...")
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(analyse_file, found, settings): found for found in todo
                }
                done = 0
                for future in as_completed(futures):
                    if cancelled["flag"]:
                        for pending in futures:
                            pending.cancel()
                        result.cancelled = True
                        break
                    try:
                        photo, rotations = future.result()
                    except Exception as exc:  # pragma: no cover - defensive
                        found = futures[future]
                        log.exception("analysis crashed on %s", found.path)
                        result.failed += 1
                        result.errors.append((found.path, str(exc)))
                        continue
                    if photo.status == "ok":
                        result.indexed += 1
                    else:
                        result.failed += 1
                        result.errors.append((photo.path, photo.error))
                    self.db.upsert(photo, rotations)
                    done += 1
                    if done % 5 == 0 or done == total:
                        report(done, total, f"Analysing {photo.name}")

        if prune_missing and not result.cancelled:
            stale = [
                path
                for path in self.db.known_paths()
                if path not in seen_paths and not _still_exists(path, roots)
            ]
            if stale:
                result.removed = self.db.remove_paths(stale)

        result.seconds = time.time() - started
        return result

    # ------------------------------------------------------------------
    def duplicate_groups(
        self, progress: Callable[[int, int], bool] | None = None
    ) -> list[DuplicateGroup]:
        photos = self.db.photos(sort="score")
        rotations = self.db.rotations() if self.settings.detect_rotations else None
        return build_groups(photos, self.settings, rotations=rotations, progress=progress)

    # ------------------------------------------------------------------
    def rescore_all(self) -> int:
        """Recompute every score from stored metrics after a weight change."""
        updates: dict[int, float] = {}
        for photo in self.db.iter_photos(sort="score"):
            if photo.metrics:
                updates[photo.id] = rescore(photo.metrics, self.settings.weights)
        self.db.update_scores(updates)
        return len(updates)

    # ------------------------------------------------------------------
    def ranked(self, **kwargs) -> list[Photo]:
        return self.db.photos(**kwargs)

    def percentile_of(self, score: float, sorted_scores: Sequence[float]) -> float:
        """Where a score sits within the library, as a 0-100 percentile."""
        if not sorted_scores:
            return 0.0
        low, high = 0, len(sorted_scores)
        while low < high:  # bisect_left without importing for one call site
            middle = (low + high) // 2
            if sorted_scores[middle] < score:
                low = middle + 1
            else:
                high = middle
        return round(100.0 * low / len(sorted_scores), 1)

    # ------------------------------------------------------------------
    def delete_photos(
        self, photos: Iterable[Photo], to_trash: bool = True
    ) -> tuple[int, list[tuple[str, str]]]:
        """Remove files from disk *and* the index.

        Sending files to the desktop trash is the default because an
        irreversible delete is a terrible default for a tool whose whole job is
        deciding which photos are surplus.
        """
        deleted = 0
        failures: list[tuple[str, str]] = []
        removed_paths: list[str] = []
        trash = None
        if to_trash:
            try:
                from send2trash import send2trash as trash
            except ImportError:
                log.warning("send2trash is not installed; deleting permanently")
                trash = None

        for photo in photos:
            try:
                if trash is not None:
                    trash(photo.path)
                else:
                    os.remove(photo.path)
                deleted += 1
                removed_paths.append(photo.path)
            except FileNotFoundError:
                removed_paths.append(photo.path)  # already gone: drop the row
            except Exception as exc:
                failures.append((photo.path, str(exc)))
        if removed_paths:
            self.db.remove_paths(removed_paths)
        return deleted, failures


def _still_exists(path: str, roots: Sequence[Path | str]) -> bool:
    """Keep rows for photos that live outside the folders we just scanned."""
    candidate = Path(path)
    for root in roots:
        try:
            candidate.relative_to(Path(root).expanduser())
        except ValueError:
            continue
        return candidate.exists()
    return True
