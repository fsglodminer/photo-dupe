"""Command line interface.

The GUI is the point of this app, but everything it does is available headless
too -- useful over SSH, in a cron job, or when you want to check what the
grouping *would* do before letting it touch anything.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __app_name__, __version__
from .config import Settings, ensure_app_dirs
from .db import Database
from .exporter import above_score, export_photos, keepers_only, top_ranked
from .grouping import group_statistics
from .imaging import human_size
from .importer import import_photos
from .library import Library


def _progress(done: int, total: int, message: str) -> bool:
    if total:
        percent = 100.0 * done / total
        sys.stderr.write(f"\r  {percent:5.1f}%  {message[:60]:<60}")
    else:
        sys.stderr.write(f"\r  {message[:68]:<68}")
    sys.stderr.flush()
    return True


def _finish_progress() -> None:
    sys.stderr.write("\r" + " " * 74 + "\r")
    sys.stderr.flush()


def _library(args: argparse.Namespace) -> Library:
    settings = Settings.load()
    if getattr(args, "database", None):
        settings.database_path = str(Path(args.database).expanduser())
    if getattr(args, "threshold", None) is not None:
        settings.similarity_threshold = args.threshold
    ensure_app_dirs()
    return Library(settings, Database(settings.database_path))


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_scan(args: argparse.Namespace) -> int:
    library = _library(args)
    roots = [Path(p).expanduser() for p in args.folders] or [
        Path(p) for p in library.settings.watched_folders
    ]
    if not roots:
        print("No folders given and none configured. Try: photo-dupe scan ~/Pictures")
        return 2
    if args.remember:
        known = library.settings.watched_folders
        for root in roots:
            if str(root) not in known:
                known.append(str(root))
        library.settings.save()
    result = library.scan(roots, progress=_progress, force=args.force)
    _finish_progress()
    print(result.summary())
    for path, error in result.errors[:10]:
        print(f"  ! {Path(path).name}: {error}")
    if len(result.errors) > 10:
        print(f"  ... and {len(result.errors) - 10} more")
    return 0


def cmd_dupes(args: argparse.Namespace) -> int:
    library = _library(args)
    groups = library.duplicate_groups(progress=lambda d, t: _progress(d, t, "Comparing"))
    _finish_progress()
    stats = group_statistics(groups)
    if not groups:
        print("No duplicates found.")
        return 0
    print(
        f"{stats['groups']} groups, {stats['removable']} removable photos, "
        f"{human_size(stats['wasted_bytes'])} reclaimable\n"
    )
    limit = args.limit if args.limit > 0 else len(groups)
    for group in groups[:limit]:
        print(f"Group {group.index} ({group.kind}, {group.tightness:.0f}% alike)")
        for photo in group.photos:
            marker = "KEEP  " if photo.id == group.best_id else "dupe  "
            print(
                f"  {marker}{photo.score:5.1f}  {photo.width}x{photo.height:<5} "
                f"{human_size(photo.size):>9}  {photo.path}"
            )
        print()
    if args.delete:
        removable = [p for g in groups for p in g.others]
        if not args.yes:
            answer = input(
                f"Delete {len(removable)} duplicate files "
                f"({'to trash' if library.settings.delete_to_trash else 'PERMANENTLY'})? [y/N] "
            )
            if answer.strip().lower() not in ("y", "yes"):
                print("Nothing deleted.")
                return 0
        deleted, failures = library.delete_photos(
            removable, to_trash=library.settings.delete_to_trash
        )
        print(f"Deleted {deleted} files.")
        for path, error in failures:
            print(f"  ! {path}: {error}")
    return 0


def cmd_rank(args: argparse.Namespace) -> int:
    library = _library(args)
    photos = library.ranked(sort=args.sort, limit=args.limit)
    if not photos:
        print("Nothing indexed yet. Run: photo-dupe scan <folder>")
        return 1
    print(f"{'rank':>4}  {'score':>5}  {'gr':>2}  {'pixels':>11}  {'size':>9}  photo")
    for index, photo in enumerate(photos, start=1):
        print(
            f"{index:>4}  {photo.score:5.1f}  {photo.grade():>2}  "
            f"{photo.width:>5}x{photo.height:<5}  {human_size(photo.size):>9}  {photo.path}"
        )
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    library = _library(args)
    settings = library.settings
    if args.into:
        settings.library_root = str(Path(args.into).expanduser())
    if args.move:
        settings.import_mode = "move"
    if args.organise:
        settings.import_organise = args.organise
    settings.import_skip_similar = args.skip_similar
    result = import_photos(
        [Path(p).expanduser() for p in args.folders],
        settings,
        library.db,
        progress=_progress,
        dry_run=args.dry_run,
    )
    _finish_progress()
    if args.dry_run:
        print("(dry run -- nothing was written)")
    print(result.summary())
    for source, reason, existing in result.rejected[:10]:
        print(f"  - {Path(source).name}: {reason} {Path(existing).name}")
    for path, error in result.errors[:10]:
        print(f"  ! {Path(path).name}: {error}")
    if result.imported and not args.dry_run and not args.no_index:
        print("Indexing the imported photos...")
        scan = library.scan([Path(settings.library_root)], progress=_progress)
        _finish_progress()
        print(scan.summary())
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    library = _library(args)
    settings = library.settings
    settings.export_mode = args.mode
    settings.export_naming = args.naming
    settings.export_manifest = args.manifest
    settings.export_max_edge = args.max_edge
    settings.export_group_folders = args.group_folders

    everything = library.ranked(sort="score")
    if not everything:
        print("Nothing indexed yet. Run: photo-dupe scan <folder>")
        return 1

    groups = []
    if args.best_only or args.group_folders:
        groups = library.duplicate_groups()

    if args.best_only:
        selection = keepers_only(groups, everything)
    elif args.min_score is not None:
        selection = above_score(everything, args.min_score)
    else:
        selection = everything
    if args.top > 0:
        selection = top_ranked(selection, args.top)

    print(f"Exporting {len(selection)} photos to {args.destination}")
    result = export_photos(
        selection, args.destination, settings, groups=groups, progress=_progress
    )
    _finish_progress()
    print(result.summary())
    if result.manifest:
        print(f"Manifest: {result.manifest}")
    for path, error in result.errors[:10]:
        print(f"  ! {path}: {error}")
    return 0 if not result.failed else 1


def cmd_stats(args: argparse.Namespace) -> int:
    library = _library(args)
    stats = library.db.statistics()
    print(f"{__app_name__} library: {library.settings.database_path}")
    print(f"  photos      : {stats['total']}")
    print(f"  on disk     : {human_size(stats['bytes'])}")
    print(f"  total pixels: {stats['megapixels']:.0f} MP")
    print(f"  mean score  : {stats['mean_score']:.1f}")
    if stats["errors"]:
        print(f"  unreadable  : {stats['errors']}")
    if stats["marks"]:
        marks = ", ".join(f"{k}={v}" for k, v in sorted(stats["marks"].items()))
        print(f"  marks       : {marks}")
    folders = library.settings.watched_folders
    print(f"  watching    : {', '.join(folders) if folders else '(none configured)'}")
    return 0


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="photo-dupe",
        description=f"{__app_name__} {__version__} - find similar photos, rank them, "
        f"import and export. Run with no arguments to open the GUI.",
    )
    parser.add_argument("--version", action="version", version=f"{__app_name__} {__version__}")
    parser.add_argument("--database", help="use a specific index database file")
    parser.add_argument("-v", "--verbose", action="store_true", help="log debug detail")
    subparsers = parser.add_subparsers(dest="command")

    scan = subparsers.add_parser("scan", help="index photos in one or more folders")
    scan.add_argument("folders", nargs="*", help="folders to scan")
    scan.add_argument("--force", action="store_true", help="re-analyse unchanged files")
    scan.add_argument("--remember", action="store_true", help="add folders to the watched list")
    scan.set_defaults(func=cmd_scan)

    dupes = subparsers.add_parser("dupes", help="list duplicate and near-duplicate groups")
    dupes.add_argument("--threshold", type=int, help="similarity threshold, 0-64 (default 10)")
    dupes.add_argument("--limit", type=int, default=20, help="groups to print (0 = all)")
    dupes.add_argument("--delete", action="store_true", help="delete all but the best of each group")
    dupes.add_argument("--yes", action="store_true", help="skip the delete confirmation")
    dupes.set_defaults(func=cmd_dupes)

    rank = subparsers.add_parser("rank", help="list photos best first")
    rank.add_argument("--sort", default="score", help="score, name, date, size, resolution")
    rank.add_argument("--limit", type=int, default=25, help="how many to print (0 = all)")
    rank.set_defaults(func=cmd_rank)

    importer = subparsers.add_parser("import", help="copy photos into the library")
    importer.add_argument("folders", nargs="+", help="folders or cards to import from")
    importer.add_argument("--into", help="library root (default: from settings)")
    importer.add_argument("--move", action="store_true", help="move instead of copying")
    importer.add_argument("--organise", choices=["flat", "date", "source"], help="folder layout")
    importer.add_argument("--skip-similar", action="store_true", help="also reject near-duplicates")
    importer.add_argument("--dry-run", action="store_true", help="report without writing")
    importer.add_argument("--no-index", action="store_true", help="do not scan after importing")
    importer.set_defaults(func=cmd_import)

    export = subparsers.add_parser("export", help="write photos out of the library")
    export.add_argument("destination", help="folder to export into")
    export.add_argument("--best-only", action="store_true", help="skip duplicates, keep the best of each group")
    export.add_argument("--top", type=int, default=0, help="export only the N highest ranked")
    export.add_argument("--min-score", type=float, help="export only photos scoring at least this")
    export.add_argument("--mode", default="copy", choices=["copy", "move", "symlink"])
    export.add_argument("--naming", default="original", choices=["original", "rank", "score", "date"])
    export.add_argument("--manifest", default="csv", choices=["none", "csv", "json"])
    export.add_argument("--max-edge", type=int, default=0, help="resize so the long edge is at most N pixels")
    export.add_argument("--group-folders", action="store_true", help="one subfolder per duplicate group")
    export.set_defaults(func=cmd_export)

    stats = subparsers.add_parser("stats", help="summarise the library")
    stats.set_defaults(func=cmd_stats)

    gui = subparsers.add_parser("gui", help="open the graphical interface")
    gui.set_defaults(func=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if args.command in (None, "gui"):
        from .ui.app import run_gui

        return run_gui()
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        _finish_progress()
        print("Interrupted.")
        return 130
