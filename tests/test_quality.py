"""Quality scoring: does it rank photos the way a person would?"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image, ImageFilter

from photodupe.config import QualityWeights
from photodupe.imaging import load_for_analysis
from photodupe.quality import (
    METRIC_NAMES,
    analyse,
    edge_strength,
    entropy,
    estimate_noise,
    grain_ratio,
    rescore,
    score_grade,
)

from fixtures import synthetic_photo


def _score(path) -> float:
    loaded = load_for_analysis(path)
    return analyse(loaded.gray, loaded.rgb, loaded.megapixels).score


def _report(path):
    loaded = load_for_analysis(path)
    return analyse(loaded.gray, loaded.rgb, loaded.megapixels, exif=loaded.exif)


@pytest.fixture
def scene(tmp_path):
    """One scene saved sharp, soft, blurred, noisy, dark and washed out."""
    base = synthetic_photo(31, 900, 1200)
    paths = {}

    paths["sharp"] = tmp_path / "sharp.jpg"
    base.save(paths["sharp"], quality=95)

    paths["soft"] = tmp_path / "soft.jpg"
    base.filter(ImageFilter.GaussianBlur(1.5)).save(paths["soft"], quality=95)

    paths["blurred"] = tmp_path / "blurred.jpg"
    base.filter(ImageFilter.GaussianBlur(5)).save(paths["blurred"], quality=95)

    rng = np.random.default_rng(0)
    grain = np.clip(
        np.asarray(base, dtype=np.float64) + rng.normal(0, 24, (900, 1200, 3)), 0, 255
    )
    paths["noisy"] = tmp_path / "noisy.jpg"
    Image.fromarray(grain.astype(np.uint8)).save(paths["noisy"], quality=95)

    paths["dark"] = tmp_path / "dark.jpg"
    Image.fromarray(
        (np.asarray(base, dtype=np.float64) * 0.16).astype(np.uint8)
    ).save(paths["dark"], quality=95)

    paths["blown"] = tmp_path / "blown.jpg"
    Image.fromarray(
        np.clip(np.asarray(base, dtype=np.float64) * 2.8, 0, 255).astype(np.uint8)
    ).save(paths["blown"], quality=95)

    paths["blank"] = tmp_path / "blank.jpg"
    Image.fromarray(np.full((900, 1200, 3), 128, np.uint8)).save(paths["blank"], quality=95)
    return paths


# ---------------------------------------------------------------------------
# ordering -- the property that actually matters
# ---------------------------------------------------------------------------

def test_sharp_beats_soft_beats_blurred(scene):
    sharp, soft, blurred = (_score(scene[k]) for k in ("sharp", "soft", "blurred"))
    assert sharp > soft > blurred


def test_clean_beats_noisy(scene):
    """Grain must not buy a photo extra points by inflating high-frequency energy."""
    assert _score(scene["sharp"]) > _score(scene["noisy"])


def test_well_exposed_beats_dark_and_blown(scene):
    good = _score(scene["sharp"])
    assert good > _score(scene["dark"])
    assert good > _score(scene["blown"])


def test_real_photo_beats_blank_frame(scene):
    assert _score(scene["sharp"]) > _score(scene["blank"])


def test_higher_resolution_scores_higher(tmp_path):
    base = synthetic_photo(41, 1500, 2000)
    large = tmp_path / "large.jpg"
    base.save(large, quality=95)
    small = tmp_path / "small.jpg"
    base.resize((400, 300), Image.LANCZOS).save(small, quality=95)
    assert _score(large) > _score(small)


# ---------------------------------------------------------------------------
# individual measurements
# ---------------------------------------------------------------------------

def test_edge_strength_drops_with_blur(scene):
    sharp = edge_strength(load_for_analysis(scene["sharp"]).gray)
    blurred = edge_strength(load_for_analysis(scene["blurred"]).gray)
    assert sharp > blurred * 2


def test_noise_estimate_rises_with_grain(scene):
    clean = estimate_noise(load_for_analysis(scene["sharp"]).gray)
    noisy = estimate_noise(load_for_analysis(scene["noisy"]).gray)
    assert noisy > clean


def test_grain_ratio_is_not_fooled_by_blur(scene):
    """Blur must not be rewarded: it lowers absolute noise but not the ratio much."""
    clean = grain_ratio(load_for_analysis(scene["sharp"]).gray)
    noisy = grain_ratio(load_for_analysis(scene["noisy"]).gray)
    blurred = grain_ratio(load_for_analysis(scene["blurred"]).gray)
    assert noisy > clean
    assert blurred < noisy


def test_entropy_of_flat_image_is_zero():
    assert entropy(np.full((60, 60), 0.5, np.float32)) == 0.0


def test_metrics_are_all_present_and_bounded(scene):
    report = _report(scene["sharp"])
    assert set(report.metrics) == set(METRIC_NAMES)
    for name, value in report.metrics.items():
        assert 0.0 <= value <= 1.0, f"{name} out of range: {value}"
    assert 0.0 <= report.score <= 100.0


def test_flags_describe_the_problem(scene):
    assert "blurry" in _report(scene["blurred"]).flags
    assert "dark" in _report(scene["dark"]).flags
    assert "empty" in _report(scene["blank"]).flags
    assert "blown highlights" in _report(scene["blown"]).flags


def test_grades_span_the_range():
    assert score_grade(95) == "A"
    assert score_grade(75) == "B"
    assert score_grade(60) == "C"
    assert score_grade(45) == "D"
    assert score_grade(10) == "F"


# ---------------------------------------------------------------------------
# weights
# ---------------------------------------------------------------------------

def test_rescore_matches_a_fresh_analysis(scene):
    loaded = load_for_analysis(scene["sharp"])
    weights = QualityWeights(sharpness=4.0, noise=0.5)
    fresh = analyse(loaded.gray, loaded.rgb, loaded.megapixels, weights=weights)
    from_metrics = rescore(fresh.metrics, weights)
    assert abs(fresh.score - from_metrics) < 0.01


def test_weights_change_the_ranking(scene):
    """Caring only about resolution must rank the big file above the sharp one."""
    sharp = load_for_analysis(scene["sharp"])
    blurred = load_for_analysis(scene["blurred"])
    sharpness_first = QualityWeights(
        sharpness=5, exposure=0, contrast=0, colorfulness=0, noise=0, resolution=0, detail=0
    )
    assert (
        analyse(sharp.gray, sharp.rgb, sharp.megapixels, sharpness_first).score
        > analyse(blurred.gray, blurred.rgb, blurred.megapixels, sharpness_first).score
    )
    resolution_only = QualityWeights(
        sharpness=0, exposure=0, contrast=0, colorfulness=0, noise=0, resolution=5, detail=0
    )
    same = analyse(sharp.gray, sharp.rgb, sharp.megapixels, resolution_only).score
    assert abs(
        same - analyse(blurred.gray, blurred.rgb, blurred.megapixels, resolution_only).score
    ) < 0.01


def test_zero_weights_do_not_divide_by_zero():
    weights = QualityWeights(
        sharpness=0, exposure=0, contrast=0, colorfulness=0, noise=0, resolution=0, detail=0
    )
    shares = weights.normalised()
    assert abs(sum(shares.values()) - 1.0) < 1e-9


def test_tiny_images_do_not_crash():
    tiny_gray = np.full((2, 2), 0.5, np.float32)
    tiny_rgb = np.full((2, 2, 3), 0.5, np.float32)
    report = analyse(tiny_gray, tiny_rgb, 0.000004)
    assert 0.0 <= report.score <= 100.0
