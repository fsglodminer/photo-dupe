"""Perceptual hashing: stability under re-encoding, sensitivity to content."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from photodupe.hashing import (
    HASH_BITS,
    average_hash,
    color_distance,
    color_signature,
    difference_hash,
    fingerprint,
    hamming,
    perceptual_hash,
    rotated_hashes,
    similarity_percent,
)
from photodupe.imaging import load_for_analysis

pytest.importorskip("numpy")


def _gray(path):
    return load_for_analysis(path).gray


def test_hashes_are_16_hex_characters(photo_factory):
    gray = _gray(photo_factory("a.jpg", seed=1))
    for value in (average_hash(gray), difference_hash(gray), perceptual_hash(gray)):
        assert len(value) == 16
        int(value, 16)  # must parse as hex


def test_identical_input_gives_identical_hash(photo_factory):
    gray = _gray(photo_factory("a.jpg", seed=5))
    assert perceptual_hash(gray) == perceptual_hash(gray)


def test_phash_survives_resize_and_recompression(tmp_path, photo_factory):
    original = photo_factory("original.jpg", seed=7, height=900, width=1200, quality=95)
    with Image.open(original) as image:
        small = tmp_path / "small.jpg"
        image.resize((400, 300), Image.LANCZOS).save(small, quality=55)

    distance = hamming(perceptual_hash(_gray(original)), perceptual_hash(_gray(small)))
    assert distance <= 8, f"resized copy drifted {distance} bits"


def test_phash_separates_different_photos(photo_factory):
    first = perceptual_hash(_gray(photo_factory("one.jpg", seed=11)))
    second = perceptual_hash(_gray(photo_factory("two.jpg", seed=99)))
    assert hamming(first, second) > 15


def test_hamming_handles_missing_hashes():
    assert hamming("", "abc") == HASH_BITS
    assert hamming(None, None) == HASH_BITS
    assert hamming("not hex!!", "0" * 16) == HASH_BITS
    assert hamming("f" * 16, "0" * 16) == HASH_BITS
    assert hamming("f" * 16, "f" * 16) == 0


def test_similarity_percent_endpoints():
    assert similarity_percent(0) == 100.0
    assert similarity_percent(HASH_BITS) == 0.0
    assert 40 < similarity_percent(HASH_BITS // 2) < 60


def test_colour_signature_distinguishes_colour(photo_factory):
    red = np.zeros((80, 100, 3), dtype=np.float32)
    red[:, :, 0] = 0.9
    blue = np.zeros((80, 100, 3), dtype=np.float32)
    blue[:, :, 2] = 0.9

    assert color_distance(color_signature(red), color_signature(red)) == 0.0
    assert color_distance(color_signature(red), color_signature(blue)) > 0.3


def test_colour_distance_is_safe_with_missing_data():
    assert color_distance(None, b"abc") == 1.0
    assert color_distance(b"abc", b"") == 1.0
    assert color_distance(b"ab", b"abc") == 1.0     # mismatched lengths


def test_rotation_hashes_differ_and_round_trip(photo_factory):
    gray = _gray(photo_factory("r.jpg", seed=3))
    variants = rotated_hashes(gray)
    assert len(variants) == 4
    assert len(set(variants)) == 4, "rotations should not collide"

    rotated_gray = np.rot90(gray)
    assert rotated_hashes(rotated_gray)[0] == variants[1]


def test_fingerprint_bundles_everything(photo_factory):
    loaded = load_for_analysis(photo_factory("f.jpg", seed=2))
    prints = fingerprint(loaded.gray, loaded.rgb, with_rotations=True)
    assert set(prints) == {"ahash", "dhash", "phash", "color_sig", "rotations"}
    assert len(prints["rotations"]) == 4
    assert isinstance(prints["color_sig"], bytes)


def test_hash_is_stable_across_source_resolutions(tmp_path, photo_factory):
    """The same picture stored at two sizes must hash to (nearly) the same value."""
    big = photo_factory("big.jpg", seed=21, height=1200, width=1600, quality=95)
    with Image.open(big) as image:
        medium = tmp_path / "medium.png"
        image.resize((800, 600), Image.LANCZOS).save(medium)
    assert hamming(perceptual_hash(_gray(big)), perceptual_hash(_gray(medium))) <= 6
