"""Clustering: the BK-tree must be exact, and groups must be sensible."""

from __future__ import annotations

import random

import pytest

from photodupe.config import Settings
from photodupe.grouping import (
    BKTree,
    MultiIndexHash,
    UnionFind,
    build_groups,
    build_index,
    group_statistics,
    pick_best,
)
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
# multi-index hashing
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def hash_corpus():
    """Random hashes plus deliberate near-neighbours at every useful distance."""
    rng = random.Random(4242)
    keys = [f"{rng.getrandbits(64):016x}" for _ in range(2000)]
    for _ in range(400):
        base = int(keys[rng.randrange(600)], 16)
        for _ in range(rng.randrange(1, 18)):
            base ^= 1 << rng.randrange(64)
        keys.append(f"{base:016x}")
    return keys


@pytest.mark.parametrize("threshold", [0, 1, 4, 8, 10, 11, 12, 15])
def test_multi_index_hash_is_exact(hash_corpus, threshold):
    """No false negatives: it must find precisely what brute force finds."""
    index = MultiIndexHash(threshold)
    for item, key in enumerate(hash_corpus):
        index.add(key, item)

    for probe_index in (0, 7, 1200, len(hash_corpus) - 1):
        probe = hash_corpus[probe_index]
        found = sorted(item for item, _ in index.query(probe, threshold))
        expected = sorted(
            i for i, key in enumerate(hash_corpus) if hamming(probe, key) <= threshold
        )
        assert found == expected


def test_multi_index_hash_reports_distances():
    index = MultiIndexHash(4)
    index.add("0" * 16, 1)
    index.add("0" * 15 + "3", 2)      # two bits away
    assert dict(index.query("0" * 16, 4)) == {1: 0, 2: 2}


def test_multi_index_hash_ignores_unusable_keys():
    index = MultiIndexHash(4)
    index.add("not a hash", 1)
    index.add("", 2)
    assert len(index) == 0
    assert index.query("zzzz", 4) == []
    assert index.query("0" * 16, 4) == []


def test_index_choice_follows_the_threshold():
    assert isinstance(build_index(0), MultiIndexHash)
    assert isinstance(build_index(10), MultiIndexHash)
    assert isinstance(build_index(15), MultiIndexHash)
    assert isinstance(build_index(20), BKTree)      # too loose for the segments
    assert isinstance(build_index(64), BKTree)


def test_both_index_structures_group_the_same_photos():
    """The BK-tree fallback must not change any answers."""
    rng = random.Random(11)
    photos = []
    for pid in range(150):
        value = rng.getrandbits(64)
        photos.append(_photo(pid, f"{value:016x}"))
        if pid % 5 == 0:                              # a near copy of this one
            near = value ^ (1 << rng.randrange(64))
            photos.append(_photo(1000 + pid, f"{near:016x}"))

    tight = build_groups(photos, Settings(similarity_threshold=6))
    loose = build_groups(photos, Settings(similarity_threshold=20))
    assert isinstance(build_index(6), MultiIndexHash)
    assert isinstance(build_index(20), BKTree)
    # Every group the strict pass found must survive the looser one.
    tight_sets = [{p.id for p in group.photos} for group in tight]
    loose_sets = [{p.id for p in group.photos} for group in loose]
    for members in tight_sets:
        assert any(members.issubset(other) for other in loose_sets)


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
