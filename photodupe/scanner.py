"""Walking folders to find candidate photo files."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator

log = logging.getLogger(__name__)

#: Directories that never contain photos worth indexing, and that would
#: otherwise fill the library with caches and thumbnails.
SKIP_DIRECTORIES = frozenset({
    ".git", ".svn", ".hg", "__pycache__", "node_modules",
    ".thumbnails", ".cache", ".Trash", ".Trash-1000", "$RECYCLE.BIN",
    "System Volume Information", ".photo-dupe", ".dtrash",
})


@dataclass(frozen=True)
class FoundFile:
    path: str
    size: int
    mtime: float


def _is_hidden(name: str) -> bool:
    return name.startswith(".")


def walk_images(
    roots: Iterable[Path | str],
    extensions: tuple[str, ...],
    follow_symlinks: bool = False,
    include_hidden: bool = False,
    min_bytes: int = 0,
    should_continue: Callable[[], bool] | None = None,
) -> Iterator[FoundFile]:
    """Yield every image file under ``roots``.

    Each physical file is yielded once even if several roots overlap or a
    symlink points back into a directory already visited -- otherwise a photo
    reachable by two paths would look like a duplicate of itself.
    """
    suffixes = {ext.lower() for ext in extensions}
    seen_files: set[tuple[int, int]] = set()   # (st_dev, st_ino)
    seen_dirs: set[tuple[int, int]] = set()

    for root in roots:
        root_path = Path(root).expanduser()
        if root_path.is_file():
            found = _describe(root_path, suffixes, min_bytes, seen_files)
            if found:
                yield found
            continue
        if not root_path.is_dir():
            log.warning("skipping %s: not a directory", root_path)
            continue

        for dirpath, dirnames, filenames in os.walk(
            root_path, followlinks=follow_symlinks
        ):
            if should_continue is not None and not should_continue():
                return
            # Prune in place so os.walk never descends into them.
            dirnames[:] = [
                d for d in dirnames
                if d not in SKIP_DIRECTORIES and (include_hidden or not _is_hidden(d))
            ]
            if follow_symlinks:
                try:
                    stat = os.stat(dirpath)
                    key = (stat.st_dev, stat.st_ino)
                    if key in seen_dirs:
                        dirnames[:] = []      # symlink loop: do not recurse again
                        continue
                    seen_dirs.add(key)
                except OSError:
                    continue

            for filename in filenames:
                if not include_hidden and _is_hidden(filename):
                    continue
                found = _describe(
                    Path(dirpath) / filename, suffixes, min_bytes, seen_files
                )
                if found:
                    yield found


def _describe(
    path: Path,
    suffixes: set[str],
    min_bytes: int,
    seen: set[tuple[int, int]],
) -> FoundFile | None:
    if path.suffix.lower() not in suffixes:
        return None
    try:
        stat = path.stat()
    except OSError as exc:
        log.debug("cannot stat %s: %s", path, exc)
        return None
    if not os.path.isfile(path):
        return None
    if stat.st_size < min_bytes:
        return None
    key = (stat.st_dev, stat.st_ino)
    if key in seen:
        return None
    seen.add(key)
    return FoundFile(path=str(path), size=stat.st_size, mtime=stat.st_mtime)


def count_images(
    roots: Iterable[Path | str], extensions: tuple[str, ...], **kwargs
) -> int:
    return sum(1 for _ in walk_images(roots, extensions, **kwargs))
