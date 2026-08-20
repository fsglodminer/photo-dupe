"""Bringing photos into the library.

Import copies (or moves) files from a camera, card or scratch folder into the
library root, optionally filing them by capture date, and optionally refusing
duplicates on the way in -- which is the cheapest possible moment to refuse
them.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from .config import Settings
from .db import Database
from .hashing import color_distance, hamming
from .imaging import file_digest
from .library import ProgressFn, analyse_file
from .records import Photo
from .scanner import FoundFile, walk_images

log = logging.getLogger(__name__)


@dataclass
class ImportResult:
    imported: int = 0
    skipped_duplicate: int = 0
    skipped_similar: int = 0
    failed: int = 0
    moved: bool = False
    bytes_copied: int = 0
    seconds: float = 0.0
    cancelled: bool = False
    errors: list[tuple[str, str]] = field(default_factory=list)
    #: ``(source, destination)`` for everything that landed in the library.
    files: list[tuple[str, str]] = field(default_factory=list)
    #: ``(source, reason, existing_path)`` for everything turned away.
    rejected: list[tuple[str, str, str]] = field(default_factory=list)

    def summary(self) -> str:
        verb = "moved" if self.moved else "copied"
        parts = [f"{self.imported} {verb}"]
        if self.skipped_duplicate:
            parts.append(f"{self.skipped_duplicate} already in library")
        if self.skipped_similar:
            parts.append(f"{self.skipped_similar} near-duplicates skipped")
        if self.failed:
            parts.append(f"{self.failed} failed")
        head = "Import cancelled - " if self.cancelled else "Import complete - "
        return head + ", ".join(parts)


def _unique_destination(destination: Path) -> Path:
    """Return a free filename, appending ``-1``, ``-2``... on collision."""
    if not destination.exists():
        return destination
    stem, suffix = destination.stem, destination.suffix
    for index in range(1, 10000):
        candidate = destination.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"cannot find a free name near {destination}")


def destination_for(
    photo: Photo,
    source: Path,
    library_root: Path,
    organise: str,
    source_root: Path | None = None,
) -> Path:
    """Where a photo should land inside the library."""
    if organise == "date":
        taken = photo.taken_at
        if taken is None:
            taken = _mtime_datetime(source)
        folder = library_root / f"{taken.year:04d}" / f"{taken.year:04d}-{taken.month:02d}"
    elif organise == "source" and source_root is not None:
        try:
            relative = source.parent.relative_to(source_root)
        except ValueError:
            relative = Path()
        folder = library_root / source_root.name / relative
    else:
        folder = library_root
    return folder / source.name


def _mtime_datetime(path: Path):
    from datetime import datetime

    try:
        return datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return datetime.now()


def import_photos(
    sources: Sequence[Path | str],
    settings: Settings,
    database: Database,
    progress: ProgressFn | None = None,
    dry_run: bool = False,
) -> ImportResult:
    """Copy or move every photo under ``sources`` into the library.

    Duplicate rejection happens against the *index*, against the files earlier
    in this same batch, and against whatever already sits at the destination
    path -- so importing a card that contains its own duplicates, or importing
    the same card twice, both do the right thing in one pass.

    Note that rejection can only see photos the index knows about, which is why
    both the GUI and the CLI index the library right after importing into it.
    """
    started = time.time()
    result = ImportResult(moved=settings.import_mode == "move")
    library_root = Path(settings.library_root).expanduser()
    cancelled = {"flag": False}

    def report(done: int, total: int, message: str) -> bool:
        if progress is None:
            return True
        if progress(done, total, message) is False:
            cancelled["flag"] = True
            return False
        return True

    report(0, 0, "Looking for photos to import...")
    found: list[FoundFile] = []
    for item in walk_images(
        sources,
        settings.extensions(),
        follow_symlinks=settings.follow_symlinks,
        min_bytes=settings.min_file_bytes,
        should_continue=lambda: not cancelled["flag"],
    ):
        # Never import a file that is already inside the library.
        if _is_within(Path(item.path), library_root):
            continue
        found.append(item)
    if cancelled["flag"]:
        result.cancelled = True
        return result

    total = len(found)
    known_digests = database.digests() if settings.import_skip_exact else {}
    existing: list[Photo] = (
        database.photos(sort="score") if settings.import_skip_similar else []
    )
    batch_digests: dict[str, str] = {}
    batch_photos: list[Photo] = []
    source_roots = [Path(s).expanduser() for s in sources]

    for index, item in enumerate(found, start=1):
        if cancelled["flag"]:
            result.cancelled = True
            break
        source = Path(item.path)
        if not report(index - 1, total, f"Importing {source.name}"):
            result.cancelled = True
            break

        # --- exact duplicate check (cheap: hash the bytes) --------------
        digest = ""
        if settings.import_skip_exact:
            try:
                digest = file_digest(source)
            except OSError as exc:
                result.failed += 1
                result.errors.append((str(source), str(exc)))
                continue
            existing_path = known_digests.get(digest) or batch_digests.get(digest)
            if existing_path:
                result.skipped_duplicate += 1
                result.rejected.append((str(source), "identical file", existing_path))
                continue

        # --- near-duplicate check (needs the pixels) --------------------
        photo: Photo | None = None
        if settings.import_skip_similar:
            photo, _ = analyse_file(item, settings, make_thumbnail=False)
            if photo.status != "ok":
                result.failed += 1
                result.errors.append((str(source), photo.error))
                continue
            match = _find_similar(photo, existing, batch_photos, settings)
            if match is not None:
                result.skipped_similar += 1
                result.rejected.append((str(source), "looks the same as", match.path))
                continue

        if photo is None:
            photo = Photo(path=str(source), filename=source.name)

        # --- place the file --------------------------------------------
        source_root = next(
            (root for root in source_roots if _is_within(source, root)), None
        )
        destination = destination_for(
            photo, source, library_root, settings.import_organise, source_root
        )
        # Safety net for the commonest mistake: importing the same card twice
        # into a library that has not been indexed yet. The index cannot help
        # there, but the file sitting at the destination can.
        if digest and destination.exists() and not dry_run:
            try:
                if file_digest(destination) == digest:
                    result.skipped_duplicate += 1
                    result.rejected.append(
                        (str(source), "identical file", str(destination))
                    )
                    continue
            except OSError:
                pass
        try:
            if not dry_run:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination = _unique_destination(destination)
                if settings.import_mode == "move":
                    _move(source, destination)
                else:
                    shutil.copy2(source, destination)
            else:
                destination = _unique_destination(destination)
        except (OSError, shutil.Error) as exc:
            result.failed += 1
            result.errors.append((str(source), str(exc)))
            continue

        result.imported += 1
        result.bytes_copied += item.size
        result.files.append((str(source), str(destination)))
        if digest:
            batch_digests[digest] = str(destination)
        if settings.import_skip_similar and photo is not None:
            batch_photos.append(photo)

    report(total, total, "Import finished")
    result.seconds = time.time() - started
    return result


def _move(source: Path, destination: Path) -> None:
    """Move across filesystems safely (cards are rarely on the same device)."""
    try:
        os.replace(source, destination)
    except OSError:
        shutil.move(str(source), str(destination))


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _find_similar(
    photo: Photo,
    existing: Sequence[Photo],
    batch: Sequence[Photo],
    settings: Settings,
) -> Photo | None:
    threshold = settings.similarity_threshold
    for candidate in list(existing) + list(batch):
        if not candidate.phash:
            continue
        if hamming(photo.phash, candidate.phash) > threshold:
            continue
        if hamming(photo.dhash, candidate.dhash) > settings.dhash_threshold:
            continue
        if color_distance(photo.color_sig, candidate.color_sig) > settings.color_threshold:
            continue
        return candidate
    return None
