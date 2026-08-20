"""Scanning, indexing and grouping a real folder of files end to end."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from photodupe.config import Settings
from photodupe.imaging import ImageLoadError, file_digest, human_size, load_for_analysis
from photodupe.library import Library, analyse_file
from photodupe.scanner import FoundFile, walk_images


# ---------------------------------------------------------------------------
# scanning the filesystem
# ---------------------------------------------------------------------------

def test_walk_finds_images_and_ignores_other_files(tmp_path, photo_factory):
    photo_factory("holiday/one.jpg", seed=1)
    photo_factory("holiday/two.png", seed=2)
    (tmp_path / "holiday" / "notes.txt").write_text("not a photo")

    found = {Path(item.path).name for item in walk_images([tmp_path], (".jpg", ".png"))}
    assert found == {"one.jpg", "two.png"}


def test_walk_skips_cache_directories(tmp_path, photo_factory):
    photo_factory("keep.jpg", seed=1)
    photo_factory(".thumbnails/skip.jpg", seed=2)
    photo_factory("node_modules/skip.jpg", seed=3)
    found = {Path(item.path).name for item in walk_images([tmp_path], (".jpg",))}
    assert found == {"keep.jpg"}


def test_walk_skips_hidden_files_by_default(tmp_path, photo_factory):
    photo_factory("visible.jpg", seed=1)
    photo_factory(".hidden.jpg", seed=2)
    assert {Path(f.path).name for f in walk_images([tmp_path], (".jpg",))} == {"visible.jpg"}


def test_walk_respects_the_minimum_size(tmp_path, photo_factory):
    photo_factory("real.jpg", seed=1, height=600, width=800)
    (tmp_path / "tiny.jpg").write_bytes(b"x" * 10)
    found = {Path(f.path).name for f in walk_images([tmp_path], (".jpg",), min_bytes=4096)}
    assert found == {"real.jpg"}


def test_each_file_is_yielded_once_even_from_overlapping_roots(tmp_path, photo_factory):
    photo_factory("a/one.jpg", seed=1)
    paths = [f.path for f in walk_images([tmp_path, tmp_path / "a"], (".jpg",))]
    assert len(paths) == 1


def test_hard_links_to_the_same_file_are_yielded_once(tmp_path, photo_factory):
    original = photo_factory("one.jpg", seed=1)
    os.link(original, tmp_path / "same.jpg")
    assert len(list(walk_images([tmp_path], (".jpg",)))) == 1


def test_walk_accepts_a_single_file(tmp_path, photo_factory):
    one = photo_factory("solo.jpg", seed=1)
    assert [f.path for f in walk_images([one], (".jpg",))] == [str(one)]


def test_walk_can_be_stopped(tmp_path, photo_factory):
    for index in range(5):
        photo_factory(f"p{index}.jpg", seed=index)
    stopped = list(walk_images([tmp_path], (".jpg",), should_continue=lambda: False))
    assert stopped == []


# ---------------------------------------------------------------------------
# analysing one file
# ---------------------------------------------------------------------------

def test_analyse_file_populates_a_photo(settings, photo_factory):
    path = photo_factory("one.jpg", seed=4, height=900, width=1200, quality=92)
    stat = path.stat()
    photo, rotations = analyse_file(
        FoundFile(str(path), stat.st_size, stat.st_mtime), settings
    )
    assert photo.status == "ok"
    assert photo.width == 1200 and photo.height == 900
    assert photo.format == "JPEG"
    assert len(photo.sha256) == 64
    assert photo.phash and photo.dhash and photo.ahash
    assert 0 < photo.score <= 100
    assert photo.metrics and photo.flags is not None
    assert rotations is None                      # not requested
    assert Path(photo.thumbnail).exists()


def test_analyse_file_reports_a_broken_file_instead_of_raising(settings, tmp_path):
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"definitely not a JPEG" * 100)
    stat = broken.stat()
    photo, _ = analyse_file(FoundFile(str(broken), stat.st_size, stat.st_mtime), settings)
    assert photo.status == "error"
    assert photo.error
    assert photo.score == 0


def test_analyse_file_can_compute_rotations(settings, photo_factory):
    settings.detect_rotations = True
    path = photo_factory("one.jpg", seed=4)
    stat = path.stat()
    _, rotations = analyse_file(FoundFile(str(path), stat.st_size, stat.st_mtime), settings)
    assert rotations is not None and len(rotations) == 4


def test_load_for_analysis_reports_full_resolution_not_decoded_size(photo_factory):
    """draft() decodes a JPEG small; the recorded size must still be the real one."""
    path = photo_factory("big.jpg", seed=6, height=1500, width=2000, quality=90)
    loaded = load_for_analysis(path, max_edge=256)
    assert (loaded.width, loaded.height) == (2000, 1500)
    assert max(loaded.gray.shape) == 256


def test_load_for_analysis_rejects_a_non_image(tmp_path):
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"nope" * 500)
    with pytest.raises(ImageLoadError):
        load_for_analysis(bad)


def test_file_digest_matches_hashlib(tmp_path):
    import hashlib

    path = tmp_path / "data.bin"
    payload = b"photo bytes" * 5000
    path.write_bytes(payload)
    assert file_digest(path) == hashlib.sha256(payload).hexdigest()


def test_human_size_reads_naturally():
    assert human_size(512) == "512 B"
    assert human_size(1536) == "1.5 KB"
    assert human_size(5 * 1024 * 1024) == "5.0 MB"
    assert human_size(3 * 1024**3) == "3.0 GB"


# ---------------------------------------------------------------------------
# the full scan
# ---------------------------------------------------------------------------

def test_scan_indexes_a_folder(library, photo_set):
    root, _expected = photo_set
    result = library.scan([root])
    assert result.indexed == 10           # ten readable photos
    assert result.failed == 1             # plus the deliberately broken one
    assert library.db.count() == 10
    assert "analysed" in result.summary()


def test_second_scan_skips_unchanged_files(library, photo_set):
    root, _ = photo_set
    library.scan([root])
    second = library.scan([root])
    assert second.indexed == 0
    assert second.skipped == 10


def test_forced_scan_reanalyses_everything(library, photo_set):
    root, _ = photo_set
    library.scan([root])
    forced = library.scan([root], force=True)
    assert forced.indexed == 10
    assert forced.skipped == 0


def test_scan_notices_a_changed_file(library, photo_set, photo_factory):
    root, _ = photo_set
    library.scan([root])
    target = root / "trip" / "beach.jpg"
    target.write_bytes((root / "trip" / "sunset.jpg").read_bytes())
    os.utime(target, (target.stat().st_atime, target.stat().st_mtime + 100))
    result = library.scan([root])
    assert result.indexed == 1


def test_scan_prunes_deleted_files(library, photo_set):
    root, _ = photo_set
    library.scan([root])
    (root / "trip" / "unique-1.jpg").unlink()
    result = library.scan([root])
    assert result.removed == 1
    assert library.db.count() == 9


def test_scan_can_be_cancelled(library, photo_set):
    root, _ = photo_set
    result = library.scan([root], progress=lambda done, total, message: False)
    assert result.cancelled
    assert "cancelled" in result.summary().lower()


def test_scan_with_no_folders_does_nothing(library):
    assert library.scan([]).scanned == 0


def test_scan_reports_progress(library, photo_set):
    root, _ = photo_set
    seen: list[tuple[int, int, str]] = []
    library.scan([root], progress=lambda *args: (seen.append(args), True)[1])
    assert seen
    assert any(total > 0 for _done, total, _message in seen)


# ---------------------------------------------------------------------------
# grouping the scanned library
# ---------------------------------------------------------------------------

def test_duplicates_are_found_as_designed(library, photo_set):
    root, expected = photo_set
    library.scan([root])
    groups = library.duplicate_groups()

    by_name = [
        {photo.name for photo in group.photos} for group in groups
    ]
    for names in expected.values():
        wanted = {Path(name).name for name in names}
        assert wanted in by_name, f"{wanted} was not grouped together"

    grouped = {name for names in by_name for name in names}
    for singleton in ("unique-1.jpg", "unique-2.jpg", "unique-3.jpg"):
        assert singleton not in grouped, f"{singleton} was grouped by mistake"


def test_the_sharp_frame_wins_its_burst(library, photo_set):
    root, _ = photo_set
    library.scan([root])
    for group in library.duplicate_groups():
        names = {photo.name for photo in group.photos}
        if names == {"burst-sharp.jpg", "burst-blurred.jpg"}:
            assert group.best.name == "burst-sharp.jpg"
            return
    pytest.fail("the burst group was not found")


def test_lossless_original_wins_over_the_shrunken_copy(library, photo_set):
    root, _ = photo_set
    library.scan([root])
    for group in library.duplicate_groups():
        names = {photo.name for photo in group.photos}
        if "beach-small.jpg" in names:
            assert group.best.name != "beach-small.jpg"
            return
    pytest.fail("the beach group was not found")


def test_rescore_applies_new_weights(library, photo_set):
    root, _ = photo_set
    library.scan([root])
    before = {photo.id: photo.score for photo in library.ranked()}

    library.settings.weights.resolution = 5.0
    library.settings.weights.sharpness = 0.1
    assert library.rescore_all() == len(before)

    after = {photo.id: photo.score for photo in library.ranked()}
    assert after != before
    assert all(0 <= score <= 100 for score in after.values())


def test_percentile_of(library):
    scores = [10.0, 20.0, 30.0, 40.0]
    assert library.percentile_of(10.0, scores) == 0.0
    assert library.percentile_of(30.0, scores) == 50.0
    assert library.percentile_of(99.0, scores) == 100.0
    assert library.percentile_of(5.0, []) == 0.0


def test_delete_photos_removes_files_and_rows(library, photo_set):
    root, _ = photo_set
    library.scan([root])
    victim = library.ranked()[0]
    deleted, failures = library.delete_photos([victim], to_trash=False)
    assert deleted == 1
    assert not failures
    assert not Path(victim.path).exists()
    assert library.db.get(victim.id) is None


def test_delete_photos_survives_an_already_missing_file(library, photo_set):
    root, _ = photo_set
    library.scan([root])
    victim = library.ranked()[0]
    Path(victim.path).unlink()
    deleted, failures = library.delete_photos([victim], to_trash=False)
    assert deleted == 0 and not failures
    assert library.db.get(victim.id) is None      # the stale row is still cleaned up
