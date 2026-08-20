"""Settings persistence and the command line interface."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from photodupe.cli import build_parser, main
from photodupe.config import IMAGE_EXTENSIONS, QualityWeights, Settings


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------

def test_defaults_are_filled_in():
    settings = Settings()
    assert settings.database_path.endswith(".db")
    assert settings.library_root
    assert settings.thread_count() >= 1


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "settings.json"
    original = Settings(similarity_threshold=6, theme="light")
    original.weights.sharpness = 4.2
    original.watched_folders = ["/photos"]
    original.save(path)

    loaded = Settings.load(path)
    assert loaded.similarity_threshold == 6
    assert loaded.theme == "light"
    assert loaded.weights.sharpness == 4.2
    assert loaded.watched_folders == ["/photos"]


def test_unknown_keys_are_ignored(tmp_path):
    """A config written by a newer version must not break an older one."""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({
        "similarity_threshold": 3,
        "a_setting_from_the_future": True,
        "weights": {"sharpness": 2.0, "telepathy": 9.0},
    }))
    settings = Settings.load(path)
    assert settings.similarity_threshold == 3
    assert settings.weights.sharpness == 2.0


def test_a_corrupt_settings_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{ this is not json")
    assert Settings.load(path).similarity_threshold == Settings().similarity_threshold


def test_a_missing_settings_file_is_fine(tmp_path):
    assert Settings.load(tmp_path / "nope.json").theme == "dark"


def test_save_is_atomic(tmp_path):
    """A half-written settings file must never be left behind."""
    path = tmp_path / "settings.json"
    Settings().save(path)
    assert path.exists()
    assert not list(tmp_path.glob("*.tmp"))
    json.loads(path.read_text())


def test_weights_normalise_to_one():
    shares = QualityWeights(sharpness=3, exposure=1).normalised()
    assert abs(sum(shares.values()) - 1.0) < 1e-9
    assert shares["sharpness"] > shares["exposure"]


def test_negative_weights_are_clamped():
    shares = QualityWeights(sharpness=-5).normalised()
    assert shares["sharpness"] == 0.0


def test_raw_extensions_are_opt_in():
    assert ".cr2" not in Settings().extensions()
    assert ".cr2" in Settings(include_raw=True).extensions()
    assert set(IMAGE_EXTENSIONS).issubset(set(Settings().extensions()))


def test_explicit_thread_count_wins():
    assert Settings(worker_threads=3).thread_count() == 3


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_every_subcommand_parses():
    parser = build_parser()
    for argv in (
        ["scan", "/photos"],
        ["scan", "/photos", "--force", "--remember"],
        ["dupes", "--threshold", "6", "--limit", "5"],
        ["dupes", "--delete", "--yes"],
        ["rank", "--sort", "date", "--limit", "10"],
        ["import", "/card", "--into", "/lib", "--move", "--organise", "date"],
        ["import", "/card", "--dry-run", "--skip-similar"],
        ["export", "/out", "--best-only", "--naming", "rank", "--manifest", "json"],
        ["export", "/out", "--top", "50", "--max-edge", "2000"],
        ["stats"],
    ):
        assert parser.parse_args(argv) is not None


def test_bad_choices_are_rejected():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["export", "/out", "--mode", "teleport"])


def test_scan_rank_and_stats_end_to_end(tmp_path, photo_set, capsys, monkeypatch):
    root, _ = photo_set
    database = tmp_path / "cli.db"
    monkeypatch.setattr(
        "photodupe.cli.Settings.load",
        staticmethod(lambda *a, **k: Settings(database_path=str(database))),
    )

    assert main(["scan", str(root)]) == 0
    assert "analysed" in capsys.readouterr().out

    assert main(["rank", "--limit", "3"]) == 0
    ranked = capsys.readouterr().out
    assert "score" in ranked and ranked.count("\n") >= 4

    assert main(["dupes", "--limit", "10"]) == 0
    assert "groups" in capsys.readouterr().out

    assert main(["stats"]) == 0
    assert "photos" in capsys.readouterr().out


def test_export_via_cli(tmp_path, photo_set, capsys, monkeypatch):
    root, _ = photo_set
    database = tmp_path / "cli.db"
    monkeypatch.setattr(
        "photodupe.cli.Settings.load",
        staticmethod(lambda *a, **k: Settings(database_path=str(database))),
    )
    main(["scan", str(root)])
    capsys.readouterr()

    destination = tmp_path / "out"
    assert main(["export", str(destination), "--best-only", "--naming", "rank"]) == 0
    output = capsys.readouterr().out
    assert "Export complete" in output
    assert destination.exists()
    assert any(p.name.startswith("0001_") for p in destination.iterdir())


def test_rank_without_an_index_explains_itself(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(
        "photodupe.cli.Settings.load",
        staticmethod(lambda *a, **k: Settings(database_path=str(tmp_path / "empty.db"))),
    )
    assert main(["rank"]) == 1
    assert "scan" in capsys.readouterr().out


def test_scan_without_folders_explains_itself(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(
        "photodupe.cli.Settings.load",
        staticmethod(lambda *a, **k: Settings(database_path=str(tmp_path / "empty.db"))),
    )
    assert main(["scan"]) == 2
    assert "photo-dupe scan" in capsys.readouterr().out
