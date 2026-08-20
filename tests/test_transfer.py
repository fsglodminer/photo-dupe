"""Import and export."""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path

import pytest

from photodupe.exporter import (
    above_score,
    export_name,
    export_photos,
    keepers_only,
    top_ranked,
)
from photodupe.importer import destination_for, import_photos
from photodupe.records import Photo


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------

def test_import_copies_photos_into_the_library(settings, database, tmp_path, photo_factory):
    photo_factory("card/one.jpg", seed=1)
    photo_factory("card/two.jpg", seed=2)
    settings.library_root = str(tmp_path / "library")
    settings.import_organise = "flat"

    result = import_photos([tmp_path / "card"], settings, database)
    assert result.imported == 2
    assert not result.errors
    assert sorted(p.name for p in (tmp_path / "library").glob("*.jpg")) == [
        "one.jpg", "two.jpg"
    ]
    assert (tmp_path / "card" / "one.jpg").exists()      # copy, not move


def test_import_can_move(settings, database, tmp_path, photo_factory):
    source = photo_factory("card/one.jpg", seed=1)
    settings.library_root = str(tmp_path / "library")
    settings.import_organise = "flat"
    settings.import_mode = "move"

    result = import_photos([tmp_path / "card"], settings, database)
    assert result.imported == 1
    assert not source.exists()
    assert (tmp_path / "library" / "one.jpg").exists()


def test_import_rejects_a_file_already_in_the_index(settings, database, tmp_path, photo_factory):
    """The normal workflow: import, index, then offer the same bytes again."""
    from photodupe.library import Library

    original = photo_factory("card/one.jpg", seed=1)
    settings.library_root = str(tmp_path / "library")
    settings.import_organise = "flat"
    import_photos([tmp_path / "card"], settings, database)
    Library(settings, database).scan([tmp_path / "library"])

    again = tmp_path / "card2" / "copy.jpg"      # same bytes, different name
    again.parent.mkdir()
    again.write_bytes(original.read_bytes())

    result = import_photos([tmp_path / "card2"], settings, database)
    assert result.imported == 0
    assert result.skipped_duplicate == 1
    assert result.rejected[0][1] == "identical file"


def test_importing_the_same_card_twice_is_a_no_op_even_without_indexing(
    settings, database, tmp_path, photo_factory
):
    """The destination-path check catches this without any index at all."""
    photo_factory("card/one.jpg", seed=1)
    settings.library_root = str(tmp_path / "library")
    settings.import_organise = "flat"

    first = import_photos([tmp_path / "card"], settings, database)
    second = import_photos([tmp_path / "card"], settings, database)

    assert first.imported == 1
    assert second.imported == 0
    assert second.skipped_duplicate == 1
    assert len(list((tmp_path / "library").glob("*.jpg"))) == 1


def test_import_rejects_duplicates_inside_one_batch(settings, database, tmp_path, photo_factory):
    original = photo_factory("card/one.jpg", seed=1)
    (tmp_path / "card" / "one-copy.jpg").write_bytes(original.read_bytes())
    settings.library_root = str(tmp_path / "library")
    settings.import_organise = "flat"

    result = import_photos([tmp_path / "card"], settings, database)
    assert result.imported == 1
    assert result.skipped_duplicate == 1


def test_import_can_reject_near_duplicates(settings, database, tmp_path, photo_factory):
    from PIL import Image

    original = photo_factory("card/one.jpg", seed=8, height=900, width=1200, quality=95)
    with Image.open(original) as image:
        image.resize((600, 450), Image.LANCZOS).save(
            tmp_path / "card" / "one-small.jpg", quality=60
        )
    settings.library_root = str(tmp_path / "library")
    settings.import_organise = "flat"
    settings.import_skip_similar = True

    result = import_photos([tmp_path / "card"], settings, database)
    assert result.imported == 1
    assert result.skipped_similar == 1


def test_import_organises_by_date(settings, database, tmp_path, photo_factory):
    source = photo_factory("card/one.jpg", seed=1)
    os.utime(source, (1719792000, 1719792000))     # 2024-07-01 UTC
    settings.library_root = str(tmp_path / "library")
    settings.import_organise = "date"

    result = import_photos([tmp_path / "card"], settings, database)
    assert result.imported == 1
    destination = Path(result.files[0][1])
    assert destination.parent.name.startswith("2024-")
    assert destination.parent.parent.name == "2024"


def test_import_mirrors_the_source_layout(settings, database, tmp_path, photo_factory):
    photo_factory("card/2023/trip/one.jpg", seed=1)
    settings.library_root = str(tmp_path / "library")
    settings.import_organise = "source"

    result = import_photos([tmp_path / "card"], settings, database)
    destination = Path(result.files[0][1])
    assert destination.relative_to(tmp_path / "library") == Path("card/2023/trip/one.jpg")


def test_import_renames_on_a_filename_collision(settings, database, tmp_path, photo_factory):
    photo_factory("card/one.jpg", seed=1)
    photo_factory("other/one.jpg", seed=2)          # same name, different picture
    settings.library_root = str(tmp_path / "library")
    settings.import_organise = "flat"

    result = import_photos([tmp_path / "card", tmp_path / "other"], settings, database)
    assert result.imported == 2
    names = sorted(p.name for p in (tmp_path / "library").glob("*.jpg"))
    assert names == ["one-1.jpg", "one.jpg"]


def test_dry_run_writes_nothing(settings, database, tmp_path, photo_factory):
    photo_factory("card/one.jpg", seed=1)
    settings.library_root = str(tmp_path / "library")

    result = import_photos([tmp_path / "card"], settings, database, dry_run=True)
    assert result.imported == 1
    assert not (tmp_path / "library").exists()
    assert (tmp_path / "card" / "one.jpg").exists()


def test_import_never_re_imports_the_library_itself(settings, database, tmp_path, photo_factory):
    photo_factory("library/one.jpg", seed=1)
    settings.library_root = str(tmp_path / "library")
    result = import_photos([tmp_path / "library"], settings, database)
    assert result.imported == 0


def test_import_can_be_cancelled(settings, database, tmp_path, photo_factory):
    for index in range(4):
        photo_factory(f"card/p{index}.jpg", seed=index)
    settings.library_root = str(tmp_path / "library")
    result = import_photos(
        [tmp_path / "card"], settings, database, progress=lambda *args: False
    )
    assert result.cancelled


def test_destination_for_flat_layout(tmp_path):
    photo = Photo(path="/card/x.jpg", filename="x.jpg")
    destination = destination_for(photo, Path("/card/x.jpg"), tmp_path, "flat")
    assert destination == tmp_path / "x.jpg"


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

@pytest.fixture
def scanned(library, photo_set):
    root, _ = photo_set
    library.scan([root])
    return library


def test_export_copies_selected_photos(scanned, settings, tmp_path):
    destination = tmp_path / "out"
    photos = scanned.ranked()[:3]
    result = export_photos(photos, destination, settings)
    assert result.exported == 3
    assert not result.errors
    assert len(list(destination.glob("*.jpg")) + list(destination.glob("*.png"))) == 3
    for photo in photos:
        assert Path(photo.path).exists()            # originals untouched


def test_export_writes_a_csv_manifest(scanned, settings, tmp_path):
    settings.export_manifest = "csv"
    result = export_photos(scanned.ranked()[:3], tmp_path / "out", settings)
    manifest = Path(result.manifest)
    assert manifest.exists()

    rows = list(csv.DictReader(manifest.open()))
    assert len(rows) == 3
    assert rows[0]["rank"] == "1"
    assert float(rows[0]["score"]) >= float(rows[-1]["score"])
    assert "metric_sharpness" in rows[0]


def test_export_writes_a_json_manifest(scanned, settings, tmp_path):
    settings.export_manifest = "json"
    result = export_photos(scanned.ranked()[:2], tmp_path / "out", settings)
    payload = json.loads(Path(result.manifest).read_text())
    assert payload["count"] == 2
    assert len(payload["photos"]) == 2


def test_export_can_skip_the_manifest(scanned, settings, tmp_path):
    settings.export_manifest = "none"
    result = export_photos(scanned.ranked()[:2], tmp_path / "out", settings)
    assert result.manifest == ""


def test_rank_naming_orders_the_files(scanned, settings, tmp_path):
    settings.export_naming = "rank"
    destination = tmp_path / "out"
    export_photos(scanned.ranked()[:3], destination, settings)
    names = sorted(p.name for p in destination.iterdir() if p.suffix != ".csv")
    assert names[0].startswith("0001_")
    assert names[2].startswith("0003_")


def test_score_naming(scanned, settings, tmp_path):
    settings.export_naming = "score"
    best = scanned.ranked()[0]
    assert export_name(best, 1, "score").startswith(f"{int(round(best.score)):03d}_")


def test_export_names_are_made_safe():
    photo = Photo(path="/x/a b:c*d.jpg", filename="a b:c*d.jpg")
    assert ":" not in export_name(photo, 1, "original")
    assert "*" not in export_name(photo, 1, "original")


def test_export_resizes_when_asked(scanned, settings, tmp_path):
    from PIL import Image

    settings.export_max_edge = 320
    settings.export_naming = "original"
    destination = tmp_path / "out"
    export_photos([p for p in scanned.ranked() if p.name.endswith(".jpg")][:1],
                  destination, settings)
    written = next(p for p in destination.iterdir() if p.suffix == ".jpg")
    with Image.open(written) as image:
        assert max(image.size) == 320


def test_export_as_symlink_does_not_copy_bytes(scanned, settings, tmp_path):
    settings.export_mode = "symlink"
    destination = tmp_path / "out"
    result = export_photos(scanned.ranked()[:2], destination, settings)
    assert result.exported == 2
    links = [p for p in destination.iterdir() if p.is_symlink()]
    assert len(links) == 2


def test_export_refuses_to_resize_a_symlink(scanned, settings, tmp_path):
    settings.export_mode = "symlink"
    settings.export_max_edge = 400
    result = export_photos(scanned.ranked()[:1], tmp_path / "out", settings)
    assert result.failed == 1
    assert "symbolic" in result.errors[0][1]


def test_export_can_move(scanned, settings, tmp_path):
    settings.export_mode = "move"
    victim = scanned.ranked()[0]
    export_photos([victim], tmp_path / "out", settings)
    assert not Path(victim.path).exists()


def test_export_puts_groups_in_their_own_folders(scanned, settings, tmp_path):
    settings.export_group_folders = True
    groups = scanned.duplicate_groups()
    photos = [photo for group in groups for photo in group.photos]
    destination = tmp_path / "out"
    export_photos(photos, destination, settings, groups=groups)
    folders = sorted(p.name for p in destination.iterdir() if p.is_dir())
    assert folders == [f"group-{i:03d}" for i in range(1, len(groups) + 1)]


def test_export_of_nothing_is_a_no_op(settings, tmp_path):
    result = export_photos([], tmp_path / "out", settings)
    assert result.exported == 0 and result.failed == 0


def test_export_can_be_cancelled(scanned, settings, tmp_path):
    result = export_photos(
        scanned.ranked(), tmp_path / "out", settings, progress=lambda *args: False
    )
    assert result.cancelled


def test_export_records_a_missing_source_as_a_failure(scanned, settings, tmp_path):
    victim = scanned.ranked()[0]
    Path(victim.path).unlink()
    result = export_photos([victim], tmp_path / "out", settings)
    assert result.failed == 1 and result.exported == 0


def test_keepers_only_drops_the_surplus(scanned):
    everything = scanned.ranked()
    groups = scanned.duplicate_groups()
    keepers = keepers_only(groups, everything)
    surplus = sum(len(group.photos) - 1 for group in groups)
    assert len(keepers) == len(everything) - surplus
    for group in groups:
        assert group.best_id in {photo.id for photo in keepers}


def test_top_ranked_and_above_score(scanned):
    everything = scanned.ranked()
    top = top_ranked(everything, 3)
    assert len(top) == 3
    assert top == sorted(top, key=lambda p: -p.score)
    assert top_ranked(everything, 0) == []

    cut = everything[len(everything) // 2].score
    assert all(photo.score >= cut for photo in above_score(everything, cut))
