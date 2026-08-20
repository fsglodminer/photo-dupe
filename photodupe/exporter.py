"""Getting photos back out of the library.

Export takes a selection of photos -- the ranked top 100, the keeper from every
duplicate group, whatever the user picked in the grid -- and writes them
somewhere useful, optionally renamed by rank, resized, and accompanied by a
manifest describing what was chosen and why.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Sequence

from . import __app_name__, __version__
from .config import Settings
from .imaging import resized_copy
from .library import ProgressFn
from .quality import METRIC_NAMES
from .records import DuplicateGroup, Photo

log = logging.getLogger(__name__)

NAMING_CHOICES = {
    "original": "Keep original filenames",
    "rank": "Rank prefix (001_name.jpg)",
    "score": "Score prefix (87_name.jpg)",
    "date": "Capture date prefix (2024-06-01_name.jpg)",
}

MODE_CHOICES = {
    "copy": "Copy files (originals stay put)",
    "move": "Move files out of the library",
    "symlink": "Create symbolic links (no extra disk space)",
}


@dataclass
class ExportResult:
    exported: int = 0
    failed: int = 0
    bytes_written: int = 0
    seconds: float = 0.0
    destination: str = ""
    manifest: str = ""
    cancelled: bool = False
    errors: list[tuple[str, str]] = field(default_factory=list)

    def summary(self) -> str:
        head = "Export cancelled - " if self.cancelled else "Export complete - "
        parts = [f"{self.exported} photos"]
        if self.failed:
            parts.append(f"{self.failed} failed")
        if self.bytes_written:
            from .imaging import human_size

            parts.append(human_size(self.bytes_written))
        return head + ", ".join(parts)


def _sanitise(name: str) -> str:
    """Make a filename safe on any filesystem the user might export to."""
    cleaned = "".join(
        character if character not in '/\\:*?"<>|' else "_" for character in name
    )
    cleaned = cleaned.strip().strip(".")
    return cleaned or "photo"


def export_name(photo: Photo, rank: int, naming: str) -> str:
    original = _sanitise(photo.name)
    if naming == "rank":
        return f"{rank:04d}_{original}"
    if naming == "score":
        return f"{int(round(photo.score)):03d}_{original}"
    if naming == "date":
        stamp = photo.taken_at or datetime.fromtimestamp(photo.mtime or time.time())
        return f"{stamp.strftime('%Y-%m-%d')}_{original}"
    return original


def _unique(destination: Path) -> Path:
    if not destination.exists():
        return destination
    stem, suffix = destination.stem, destination.suffix
    for index in range(1, 10000):
        candidate = destination.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"cannot find a free name near {destination}")


def export_photos(
    photos: Sequence[Photo],
    destination: Path | str,
    settings: Settings,
    groups: Sequence[DuplicateGroup] | None = None,
    progress: ProgressFn | None = None,
) -> ExportResult:
    """Write ``photos`` to ``destination`` according to the export settings.

    ``photos`` is taken in the order given -- that order *is* the ranking, and
    it is what the ``rank`` naming scheme and the manifest record.
    """
    started = time.time()
    destination = Path(destination).expanduser()
    result = ExportResult(destination=str(destination))
    total = len(photos)
    if total == 0:
        return result

    group_of: dict[int, DuplicateGroup] = {}
    if settings.export_group_folders and groups:
        for group in groups:
            for member in group.photos:
                group_of[member.id] = group

    try:
        destination.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        result.errors.append((str(destination), str(exc)))
        result.failed = total
        return result

    records: list[dict] = []
    for rank, photo in enumerate(photos, start=1):
        if progress is not None:
            if progress(rank - 1, total, f"Exporting {photo.name}") is False:
                result.cancelled = True
                break

        folder = destination
        group = group_of.get(photo.id)
        if group is not None:
            folder = destination / f"group-{group.index:03d}"

        target = folder / export_name(photo, rank, settings.export_naming)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            target = _unique(target)
            written = _place(Path(photo.path), target, settings)
            result.exported += 1
            try:
                result.bytes_written += written.stat().st_size
            except OSError:
                pass
            records.append(_record(photo, rank, written, group))
        except (OSError, shutil.Error, ValueError) as exc:
            result.failed += 1
            result.errors.append((photo.path, str(exc)))

    if records and settings.export_manifest != "none":
        try:
            result.manifest = str(_write_manifest(destination, records, settings))
        except OSError as exc:
            result.errors.append(("manifest", str(exc)))

    if progress is not None:
        progress(total, total, "Export finished")
    result.seconds = time.time() - started
    return result


def _place(source: Path, target: Path, settings: Settings) -> Path:
    mode = settings.export_mode
    resize = settings.export_max_edge > 0
    if mode == "symlink":
        if resize:
            raise ValueError("cannot resize when exporting as symbolic links")
        os.symlink(source.resolve(), target)
        return target
    if resize:
        # Re-encoding is a copy by definition, so a "move" export still has to
        # remove the original itself once the resized file is safely written.
        written = resized_copy(
            source, target, settings.export_max_edge, settings.export_jpeg_quality
        )
        if mode == "move":
            os.remove(source)
        return written
    if mode == "move":
        shutil.move(str(source), str(target))
        return target
    shutil.copy2(source, target)
    return target


def _record(photo: Photo, rank: int, written: Path, group: DuplicateGroup | None) -> dict:
    row = {
        "rank": rank,
        "exported_as": written.name,
        "source": photo.path,
        "score": round(photo.score, 2),
        "grade": photo.grade(),
        "rating": photo.rating,
        "width": photo.width,
        "height": photo.height,
        "megapixels": round(photo.megapixels, 2),
        "bytes": photo.size,
        "format": photo.format,
        "camera": photo.camera,
        "iso": photo.iso or "",
        "taken_at": photo.taken_at.isoformat() if photo.taken_at else "",
        "flags": " ".join(photo.flags),
        "duplicate_group": group.index if group else "",
        "group_role": ("" if group is None else ("keeper" if group.best_id == photo.id else "duplicate")),
    }
    for name in METRIC_NAMES:
        row[f"metric_{name}"] = round(float(photo.metrics.get(name, 0.0)), 4)
    return row


def _write_manifest(
    destination: Path, records: Sequence[dict], settings: Settings
) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if settings.export_manifest == "json":
        path = destination / f"photo-dupe-export-{stamp}.json"
        payload = {
            "generated_by": f"{__app_name__} {__version__}",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "count": len(records),
            "naming": settings.export_naming,
            "mode": settings.export_mode,
            "photos": list(records),
        }
        path.write_text(json.dumps(payload, indent=2), "utf-8")
        return path

    path = destination / f"photo-dupe-export-{stamp}.csv"
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0].keys()))
        writer.writeheader()
        writer.writerows(records)
    return path


# ---------------------------------------------------------------------------
# selection helpers -- what the export page offers as one-click choices
# ---------------------------------------------------------------------------

def keepers_only(groups: Sequence[DuplicateGroup], everything: Sequence[Photo]) -> list[Photo]:
    """Every photo, minus the duplicates -- one representative per group."""
    duplicate_ids = {
        photo.id for group in groups for photo in group.photos if photo.id != group.best_id
    }
    return [photo for photo in everything if photo.id not in duplicate_ids]


def top_ranked(photos: Sequence[Photo], count: int) -> list[Photo]:
    ordered = sorted(photos, key=lambda p: -p.score)
    return ordered[: max(0, count)]


def above_score(photos: Sequence[Photo], minimum: float) -> list[Photo]:
    return sorted(
        (p for p in photos if p.score >= minimum), key=lambda p: -p.score
    )
