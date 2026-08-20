"""Clustering: the BK-tree must be exact, and groups must be sensible."""

from __future__ import annotations

import random

import pytest

from photodupe.config import Settings
from photodupe.grouping import BKTree, UnionFind, build_groups, group_statistics, pick_best
from photodupe.hashing import hamming
from photodupe.records import Photo


def _photo(pid: int, phash: str, dhash: str = "", score: float = 50.0, **kwargs) -> Photo:
    return Photo(
        id=pid,
        path=f"/photos/{pid}.jpg",
        filename=f"{pid}.jpg",
        phash=phash,
        dhash=dhash or phash,
        color_sig=bytes(48),
        score=score,
        status="ok",
        **kwargs,
    )


# ---------------------------------------------------------------------------
# BK-tree
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("threshold", [0, 3, 8, 16, 32])
def test_bktree_matches_brute_force(threshold):
    rng = random.Random(1234)
    keys = [f"{rng.getrandbits(64):016x}" for _ in range(1500)]
    tree = BKTree()
    for index, key in enumerate(keys):
        tree.add(key, index)

    probe = keys[17]
    found = sorted(item for item, _ in tree.query(probe, threshold))
    expected = sorted(i for i, key in enumerate(keys) if hamming(probe, key) <= threshold)
    assert found == expected


def test_bktree_reports_distances():
    tree = BKTree()
    tree.add("0" * 16, 1)
    tree.add("0" * 15 + "1", 2)      # one bit away
    results = dict(tree.query("0" * 16, 4))
    assert results == {1: 0, 2: 1}


def test_bktree_groups_identical_keys():
    tree = BKTree()
    for item in (1, 2, 3):
        tree.add("abc0" * 4, item)
    assert len(tree) == 3
    assert sorted(item for item, _ in tree.query("abc0" * 4, 0)) == [1, 2, 3]


def test_empty_bktree_query_is_empty():
    assert BKTree().query("0" * 16, 5) == []


# ---------------------------------------------------------------------------
# union-find
# ---------------------------------------------------------------------------

def test_union_find_merges_chains():
    union = UnionFind()
    union.union(1, 2)
    union.union(2, 3)
    union.union(10, 11)
    union.add(20)
    groups = sorted(sorted(members) for members in union.groups().values())
    assert groups == [[1, 2, 3], [10, 11], [20]]


def test_union_find_is_idempotent():
    union = UnionFind()
    union.union(1, 2)
    union.union(1, 2)
    union.union(2, 1)
    assert len(union.groups()) == 1


# ---------------------------------------------------------------------------
# grouping
# ---------------------------------------------------------------------------

def test_identical_hashes_are_grouped():
    settings = Settings()
    photos = [_photo(1, "ff00ff00ff00ff00"), _photo(2, "ff00ff00ff00ff00")]
    groups = build_groups(photos, settings)
    assert len(groups) == 1
    assert len(groups[0].photos) == 2


def test_distant_hashes_are_not_grouped():
    settings = Settings()
    photos = [_photo(1, "0" * 16), _photo(2, "f" * 16)]
    assert build_groups(photos, settings) == []


def test_colour_check_prevents_a_false_match():
    """Same structure, wildly different colour: not the same photo."""
    settings = Settings()
    first = _photo(1, "ff00ff00ff00ff00")
    second = _photo(2, "ff00ff00ff00ff00")
    second.color_sig = bytes([255] * 48)     # first is all zeros
    assert build_groups([first, second], settings) == []


def test_dhash_check_prevents_a_false_match():
    settings = Settings(dhash_threshold=2)
    first = _photo(1, "ff00ff00ff00ff00", dhash="0" * 16)
    second = _photo(2, "ff00ff00ff00ff00", dhash="f" * 16)
    assert build_groups([first, second], settings) == []


def test_exact_duplicates_are_labelled_exact():
    settings = Settings()
    photos = [
        _photo(1, "ff00ff00ff00ff00", sha256="same"),
        _photo(2, "ff00ff00ff00ff00", sha256="same"),
    ]
    groups = build_groups(photos, settings)
    assert groups[0].kind == "exact"
    assert groups[0].tightness == 100.0


def test_a_burst_becomes_one_group_not_many_pairs():
    """A chain of near-identical frames must collapse into a single group."""
    settings = Settings(similarity_threshold=4)
    # Each step flips two bits, so 1-2, 2-3, 3-4 match but 1-4 does not.
    photos = [
        _photo(1, "0000000000000000"),
        _photo(2, "0000000000000003"),
        _photo(3, "000000000000000f"),
        _photo(4, "000000000000003f"),
    ]
    groups = build_groups(photos, settings)
    assert len(groups) == 1
    assert len(groups[0].photos) == 4


def test_photos_with_errors_are_ignored():
    settings = Settings()
    good = _photo(1, "ff00ff00ff00ff00")
    broken = _photo(2, "ff00ff00ff00ff00")
    broken.status = "error"
    assert build_groups([good, broken], settings) == []


def test_progress_callback_can_cancel():
    settings = Settings()
    photos = [_photo(i, f"{i:016x}") for i in range(400)]
    assert build_groups(photos, settings, progress=lambda done, total: False) == []


# ---------------------------------------------------------------------------
# choosing the keeper
# ---------------------------------------------------------------------------

def test_best_is_the_highest_score():
    settings = Settings()
    photos = [_photo(1, "a" * 16, score=40), _photo(2, "a" * 16, score=90)]
    assert pick_best(photos, settings) == 2


def test_ties_are_broken_by_resolution_then_file_size():
    settings = Settings()
    small = _photo(1, "a" * 16, score=70, width=800, height=600, size=100_000)
    large = _photo(2, "a" * 16, score=70, width=4000, height=3000, size=100_000)
    assert pick_best([small, large], settings) == 2

    lossy = _photo(3, "a" * 16, score=70, width=4000, height=3000, size=50_000)
    original = _photo(4, "a" * 16, score=70, width=4000, height=3000, size=900_000)
    assert pick_best([lossy, original], settings) == 4


def test_pick_best_of_nothing_is_zero():
    assert pick_best([], Settings()) == 0


def test_group_statistics_add_up():
    settings = Settings()
    photos = [
        _photo(1, "ff00ff00ff00ff00", score=90, size=1000),
        _photo(2, "ff00ff00ff00ff00", score=50, size=400),
        _photo(3, "ff00ff00ff00ff00", score=40, size=300),
    ]
    groups = build_groups(photos, settings)
    stats = group_statistics(groups)
    assert stats["groups"] == 1
    assert stats["photos_in_groups"] == 3
    assert stats["removable"] == 2
    assert stats["wasted_bytes"] == 700       # everything except the keeper


def test_groups_are_ordered_by_reclaimable_space():
    settings = Settings()
    photos = [
        _photo(1, "0000000000000000", size=10),
        _photo(2, "0000000000000000", size=10),
        _photo(3, "ffffffffffffffff", size=5000),
        _photo(4, "ffffffffffffffff", size=5000),
    ]
    groups = build_groups(photos, settings)
    assert len(groups) == 2
    assert groups[0].wasted_bytes() > groups[1].wasted_bytes()
    assert [group.index for group in groups] == [1, 2]
