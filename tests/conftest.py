"""Shared fixtures. Every test runs against a throwaway XDG environment."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures import build_photo_set, synthetic_photo  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_app_dirs(tmp_path_factory, monkeypatch):
    """Keep tests away from the developer's real settings and cache."""
    root = tmp_path_factory.mktemp("xdg")
    for variable, name in (
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_DATA_HOME", "data"),
        ("XDG_CACHE_HOME", "cache"),
    ):
        monkeypatch.setenv(variable, str(root / name))
    return root


@pytest.fixture
def settings(tmp_path):
    from photodupe.config import Settings

    return Settings(
        database_path=str(tmp_path / "index.db"),
        library_root=str(tmp_path / "library"),
    )


@pytest.fixture
def database(settings):
    from photodupe.db import Database

    db = Database(settings.database_path)
    yield db
    db.close()


@pytest.fixture
def library(settings, database):
    from photodupe.library import Library

    return Library(settings, database)


@pytest.fixture
def photo_set(tmp_path):
    """A folder of photos with a known duplicate structure."""
    root = tmp_path / "photos"
    expected = build_photo_set(root)
    return root, expected


@pytest.fixture
def photo_factory(tmp_path):
    def make(name: str, seed: int = 1, height: int = 600, width: int = 800, **save):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        synthetic_photo(seed, height, width).save(path, **save)
        return path

    return make
