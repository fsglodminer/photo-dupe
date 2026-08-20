"""The index database: round trips, rescan behaviour, filtering."""

from __future__ import annotations

from datetime import datetime

import pytest

from photodupe.db import Database
from photodupe.records import MARK_DELETE, MARK_KEEP, MARK_NONE, Photo


def _photo(path="/x/a.jpg", **kwargs) -> Photo:
    base = dict(
        path=path, filename=path.rsplit("/", 1)[-1], size=1000, mtime=10.0,
        sha256="abc", width=4000, height=3000, format="JPEG", score=70.0,
        phash="ff00ff00ff00ff00", dhash="0f0f0f0f0f0f0f0f", ahash="1" * 16,
        color_sig=bytes(48), status="ok",
    )
    base.update(kwargs)
    return Photo(**base)


def test_round_trip_preserves_every_field(database):
    original = _photo(
        taken_at=datetime(2024, 6, 1, 12, 30), camera="Pixel 8", iso=400
    )
    original.metrics = {"sharpness": 0.8, "noise": 0.5}
    original.raw_metrics = {"edge_strength": 0.42}
    original.flags = ["noisy", "dark"]
    photo_id = database.upsert(original)

    stored = database.get(photo_id)
    assert stored.path == original.path
    assert stored.size == 1000
    assert stored.width == 4000 and stored.height == 3000
    assert stored.sha256 == "abc"
    assert stored.phash == original.phash
    assert stored.color_sig == bytes(48)
    assert stored.camera == "Pixel 8" and stored.iso == 400
    assert stored.taken_at == datetime(2024, 6, 1, 12, 30)
    assert stored.metrics == {"sharpness": 0.8, "noise": 0.5}
    assert stored.raw_metrics == {"edge_strength": 0.42}
    assert stored.flags == ["noisy", "dark"]


def test_upsert_on_the_same_path_updates_rather_than_duplicates(database):
    first = database.upsert(_photo(size=100))
    second = database.upsert(_photo(size=200))
    assert first == second
    assert database.count() == 1
    assert database.get(first).size == 200


def test_rescan_does_not_wipe_review_decisions(database):
    """A user's keep/remove choice and star rating must survive re-indexing."""
    photo_id = database.upsert(_photo())
    database.set_mark([photo_id], MARK_DELETE)
    database.set_rating(photo_id, 4)

    database.upsert(_photo(size=999, score=88.0))

    stored = database.get(photo_id)
    assert stored.mark == MARK_DELETE
    assert stored.rating == 4
    assert stored.size == 999          # the measured facts did update


def test_ratings_are_clamped(database):
    photo_id = database.upsert(_photo())
    database.set_rating(photo_id, 99)
    assert database.get(photo_id).rating == 5
    database.set_rating(photo_id, -3)
    assert database.get(photo_id).rating == 0


def test_index_signature_reports_size_and_mtime(database):
    database.upsert(_photo(path="/x/a.jpg", size=11, mtime=1.5))
    assert database.index_signature() == {"/x/a.jpg": (11, 1.5)}


def test_index_signature_skips_broken_files(database):
    database.upsert(_photo(path="/x/bad.jpg", status="error", error="nope"))
    assert database.index_signature() == {}
    assert database.count(only_ok=False) == 1


def test_remove_paths(database):
    database.upsert(_photo(path="/x/a.jpg"))
    database.upsert(_photo(path="/x/b.jpg"))
    assert database.remove_paths(["/x/a.jpg", "/x/missing.jpg"]) == 1
    assert database.known_paths() == {"/x/b.jpg"}


def test_remove_paths_handles_more_than_one_chunk(database):
    paths = [f"/x/{i}.jpg" for i in range(950)]
    for path in paths:
        database.upsert(_photo(path=path))
    assert database.remove_paths(paths) == 950
    assert database.count() == 0


def test_rename_path(database):
    photo_id = database.upsert(_photo(path="/x/a.jpg"))
    database.rename_path("/x/a.jpg", "/y/b.jpg")
    stored = database.get(photo_id)
    assert stored.path == "/y/b.jpg" and stored.filename == "b.jpg"


def test_sorting(database):
    database.upsert(_photo(path="/x/low.jpg", score=10, size=50))
    database.upsert(_photo(path="/x/high.jpg", score=90, size=10))
    assert [p.name for p in database.photos(sort="score")] == ["high.jpg", "low.jpg"]
    assert [p.name for p in database.photos(sort="score_asc")] == ["low.jpg", "high.jpg"]
    assert [p.name for p in database.photos(sort="size")] == ["low.jpg", "high.jpg"]
    assert [p.name for p in database.photos(sort="name")] == ["high.jpg", "low.jpg"]


def test_unknown_sort_falls_back_to_score(database):
    database.upsert(_photo(path="/x/a.jpg", score=1))
    database.upsert(_photo(path="/x/b.jpg", score=2))
    assert [p.name for p in database.photos(sort="not-a-field")] == ["b.jpg", "a.jpg"]


def test_search_and_filters(database):
    database.upsert(_photo(path="/holiday/beach.jpg", score=80, camera="Canon"))
    database.upsert(_photo(path="/work/report.jpg", score=20))
    assert [p.name for p in database.photos(search="holiday")] == ["beach.jpg"]
    assert [p.name for p in database.photos(search="Canon")] == ["beach.jpg"]
    assert [p.name for p in database.photos(min_score=50)] == ["beach.jpg"]
    assert [p.name for p in database.photos(max_score=50)] == ["report.jpg"]
    assert database.photos(limit=1) == database.photos()[:1]


def test_flag_filter(database):
    blurry = _photo(path="/x/blurry.jpg")
    blurry.flags = ["blurry"]
    database.upsert(blurry)
    database.upsert(_photo(path="/x/fine.jpg"))
    assert [p.name for p in database.photos(flag="blurry")] == ["blurry.jpg"]


def test_mark_filter(database):
    first = database.upsert(_photo(path="/x/a.jpg"))
    database.upsert(_photo(path="/x/b.jpg"))
    database.set_mark([first], MARK_KEEP)
    assert [p.name for p in database.photos(mark=MARK_KEEP)] == ["a.jpg"]
    assert len(database.photos(mark=MARK_NONE)) == 1


def test_statistics(database):
    database.upsert(_photo(path="/x/a.jpg", size=1000, score=60))
    database.upsert(_photo(path="/x/b.jpg", size=3000, score=80))
    database.upsert(_photo(path="/x/bad.jpg", status="error"))
    stats = database.statistics()
    assert stats["total"] == 2
    assert stats["bytes"] == 4000
    assert stats["mean_score"] == 70.0
    assert stats["errors"] == 1
    assert stats["megapixels"] == pytest.approx(24.0)


def test_bulk_rescore(database):
    first = database.upsert(_photo(path="/x/a.jpg", score=10))
    second = database.upsert(_photo(path="/x/b.jpg", score=20))
    database.update_scores({first: 55.0, second: 65.0})
    assert database.get(first).score == 55.0
    assert database.get(second).score == 65.0


def test_digests_and_rotations(database):
    database.upsert(_photo(path="/x/a.jpg", sha256="deadbeef"), ["r0", "r1", "r2", "r3"])
    photo_id = database.get_by_path("/x/a.jpg").id
    assert database.digests() == {"deadbeef": "/x/a.jpg"}
    assert database.rotations() == {photo_id: ["r0", "r1", "r2", "r3"]}


def test_score_percentiles_are_sorted(database):
    for index, score in enumerate((50, 10, 90)):
        database.upsert(_photo(path=f"/x/{index}.jpg", score=score))
    assert database.score_percentiles() == [10.0, 50.0, 90.0]


def test_clear_empties_the_index(database):
    database.upsert(_photo())
    database.clear()
    assert database.count() == 0


def test_database_is_usable_from_another_thread(tmp_path):
    """Workers open their own connection; the GUI thread must still read."""
    import threading

    db = Database(tmp_path / "threads.db")
    db.upsert(_photo(path="/x/main.jpg"))

    def worker():
        db.upsert(_photo(path="/x/worker.jpg"))

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    assert db.count() == 2
    db.close()


def test_missing_photo_returns_none(database):
    assert database.get(999) is None
    assert database.get_by_path("/nope.jpg") is None


def test_meta_values(database):
    assert database.get_meta("schema_version") == "1"
    database.set_meta("last_scan", "2024-01-01")
    assert database.get_meta("last_scan") == "2024-01-01"
    assert database.get_meta("absent", "fallback") == "fallback"
