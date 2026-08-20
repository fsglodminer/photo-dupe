"""Technical quality metrics used to rank photos.

Every metric returns a value in 0..1 where higher is better, so a weighted sum
gives a single 0..100 score. The metrics are deliberately *absolute* rather
than relative to the library: adding photos never silently re-scores the ones
already there. The UI additionally shows each photo's percentile within the
library, which is the relative view.

All maths is plain numpy -- no OpenCV, no scipy -- so the app installs with
three wheels on a stock Ubuntu box.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .config import QualityWeights

METRIC_NAMES = (
    "sharpness",
    "exposure",
    "contrast",
    "colorfulness",
    "noise",
    "resolution",
    "detail",
)

METRIC_LABELS = {
    "sharpness": "Sharpness",
    "exposure": "Exposure",
    "contrast": "Contrast",
    "colorfulness": "Colour",
    "noise": "Low noise",
    "resolution": "Resolution",
    "detail": "Detail",
}


# ---------------------------------------------------------------------------
# small numeric helpers
# ---------------------------------------------------------------------------

def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return float(min(high, max(low, value)))


def _log_ramp(value: float, low: float, high: float) -> float:
    """Map ``value`` onto 0..1 across a logarithmic range.

    Perceptual quantities like blur span orders of magnitude, so a linear ramp
    would put almost every photo at one end of the scale.
    """
    if value <= 0:
        return 0.0
    lo, hi = math.log10(max(low, 1e-12)), math.log10(max(high, 1e-11))
    if hi <= lo:
        return 0.0
    return _clamp((math.log10(value) - lo) / (hi - lo))


def _plateau(value: float, ideal_low: float, ideal_high: float, falloff: float) -> float:
    """1.0 inside the ideal band, tapering linearly to 0 over ``falloff``."""
    if ideal_low <= value <= ideal_high:
        return 1.0
    distance = ideal_low - value if value < ideal_low else value - ideal_high
    return _clamp(1.0 - distance / falloff)


def convolve3(plane: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    """Valid-mode 3x3 convolution via shifted views (no scipy needed)."""
    if plane.shape[0] < 3 or plane.shape[1] < 3:
        return np.zeros((0, 0), dtype=np.float32)
    out = np.zeros(
        (plane.shape[0] - 2, plane.shape[1] - 2), dtype=np.float32
    )
    for dy in range(3):
        for dx in range(3):
            weight = float(kernel[dy, dx])
            if weight:
                out += weight * plane[dy : dy + out.shape[0], dx : dx + out.shape[1]]
    return out


_LAPLACIAN = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float32)
#: Contrast below this is treated as "no contrast" when normalising sharpness.
_CONTRAST_FLOOR = 0.06


# ---------------------------------------------------------------------------
# raw measurements
# ---------------------------------------------------------------------------

def laplacian_variance(gray: np.ndarray) -> float:
    """Classic focus measure: how much high-frequency energy the image carries."""
    response = convolve3(gray, _LAPLACIAN)
    if response.size == 0:
        return 0.0
    return float(response.var())


def edge_strength(gray: np.ndarray) -> float:
    """Blur measure: RMS Laplacian response relative to the image's contrast.

    The raw Laplacian variance depends on how contrasty the scene is, so a
    correctly focused photo of a misty morning would score like a blurred one.
    Dividing by the standard deviation removes most of that dependence; the
    floor stops a nearly uniform frame (a wall, a white sky) from dividing by
    almost zero and coming out "sharp".
    """
    variance = laplacian_variance(gray)
    if variance <= 0:
        return 0.0
    contrast = max(float(gray.std()), _CONTRAST_FLOOR)
    return math.sqrt(variance) / contrast


def _haar_diagonal(gray: np.ndarray) -> np.ndarray:
    """Diagonal (HH) detail band of a single-level Haar transform."""
    height = (gray.shape[0] // 2) * 2
    width = (gray.shape[1] // 2) * 2
    if height < 2 or width < 2:
        return np.zeros(0, dtype=np.float32)
    block = gray[:height, :width]
    return (
        block[0::2, 0::2] - block[0::2, 1::2] - block[1::2, 0::2] + block[1::2, 1::2]
    ) / 2.0


def estimate_noise(gray: np.ndarray) -> float:
    """Absolute grain estimate: Donoho's MAD estimator on the finest detail band.

    ``sigma = median(|HH|) / 0.6745`` is the standard robust noise estimate;
    the median keeps edges and subject detail from dominating the way a mean
    would. Reported for display and for the "noisy" flag -- the *score* uses
    :func:`grain_ratio`, which is far less content-dependent.
    """
    band = _haar_diagonal(gray)
    if band.size == 0:
        return 0.0
    return float(np.median(np.abs(band)) / 0.6745)


def _box_downscale(gray: np.ndarray) -> np.ndarray:
    """Exact 2x box downscale."""
    height = (gray.shape[0] // 2) * 2
    width = (gray.shape[1] // 2) * 2
    if height < 2 or width < 2:
        return gray
    block = gray[:height, :width]
    return (
        block[0::2, 0::2] + block[0::2, 1::2] + block[1::2, 0::2] + block[1::2, 1::2]
    ) / 4.0


def grain_ratio(gray: np.ndarray) -> float:
    """Grain measured *relative* to the image's own structure.

    Absolute high-frequency energy is a poor noise score, because blurring an
    image lowers it -- so ranking on it hands out points for being out of
    focus. Dividing the finest-scale detail by the edge energy that survives a
    2x downscale fixes that: blur attenuates both terms together and leaves the
    ratio roughly unchanged, while sensor grain lifts only the numerator
    (independent noise does not survive averaging four pixels, real structure
    does).

    Returns roughly 0.08-0.12 for a clean frame and 0.18+ for a visibly grainy
    one. Very low-contrast scenes read high, but those are already penalised by
    the contrast metric.
    """
    fine = estimate_noise(gray)
    if fine <= 0.0:
        return 0.0
    structure = math.sqrt(laplacian_variance(_box_downscale(gray)))
    if structure <= 1e-5:
        return 0.0
    return fine / structure


def colorfulness(rgb: np.ndarray) -> float:
    """Hasler & Suesstrunk colourfulness metric, on the usual 0..~110 scale."""
    r = rgb[:, :, 0] * 255.0
    g = rgb[:, :, 1] * 255.0
    b = rgb[:, :, 2] * 255.0
    rg = r - g
    yb = 0.5 * (r + g) - b
    std = math.sqrt(float(rg.std()) ** 2 + float(yb.std()) ** 2)
    mean = math.sqrt(float(rg.mean()) ** 2 + float(yb.mean()) ** 2)
    return std + 0.3 * mean


def entropy(gray: np.ndarray, bins: int = 64) -> float:
    """Shannon entropy of the luminance histogram, in bits (0..log2(bins))."""
    hist, _ = np.histogram(gray, bins=bins, range=(0.0, 1.0))
    total = hist.sum()
    if total <= 0:
        return 0.0
    probability = hist[hist > 0] / total
    return float(-(probability * np.log2(probability)).sum())


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------

@dataclass
class QualityReport:
    """Per-photo quality assessment."""

    metrics: dict[str, float] = field(default_factory=dict)   # normalised 0..1
    raw: dict[str, float] = field(default_factory=dict)       # measured values
    score: float = 0.0                                        # 0..100
    flags: list[str] = field(default_factory=list)

    def grade(self) -> str:
        return score_grade(self.score)

    def to_json(self) -> dict[str, Any]:
        return {"metrics": self.metrics, "raw": self.raw, "flags": self.flags}


def score_grade(score: float) -> str:
    for threshold, letter in ((85, "A"), (72, "B"), (58, "C"), (42, "D"), (25, "E")):
        if score >= threshold:
            return letter
    return "F"


def analyse(
    gray: np.ndarray,
    rgb: np.ndarray,
    megapixels: float,
    weights: QualityWeights | None = None,
    exif: dict[str, Any] | None = None,
) -> QualityReport:
    """Measure a decoded photo and reduce it to a single 0..100 score."""
    weights = weights or QualityWeights()
    exif = exif or {}
    raw: dict[str, float] = {}
    metrics: dict[str, float] = {}
    flags: list[str] = []

    # --- sharpness ------------------------------------------------------
    edges = edge_strength(gray)
    raw["edge_strength"] = edges
    raw["laplacian_variance"] = laplacian_variance(gray)
    # Ramp calibrated against 1/f "natural image" statistics at the 512 px
    # analysis size: 0.10 is unmistakably out of focus and 0.62 is critically
    # sharp. Saturating there is deliberate -- past that point extra
    # high-frequency energy is grain or JPEG ringing, not detail, and an
    # uncapped ramp would let a noisy frame out-score the clean original.
    metrics["sharpness"] = _log_ramp(edges, 0.10, 0.62)

    # --- exposure -------------------------------------------------------
    mean = float(gray.mean())
    shadow_clip = float((gray < 0.02).mean())
    highlight_clip = float((gray > 0.98).mean())
    raw["mean_luma"] = mean
    raw["shadow_clip"] = shadow_clip
    raw["highlight_clip"] = highlight_clip
    # A little clipping is normal (specular highlights, night skies); only
    # penalise once it goes past ~1.5% of the frame.
    clip_penalty = _clamp(
        2.2 * max(0.0, highlight_clip - 0.015) + 1.2 * max(0.0, shadow_clip - 0.02),
        0.0,
        1.0,
    )
    balance = _plateau(mean, 0.34, 0.62, 0.34)
    metrics["exposure"] = _clamp(balance * (1.0 - clip_penalty))
    if mean < 0.16:
        flags.append("dark")
    if mean > 0.82:
        flags.append("bright")
    if highlight_clip > 0.12:
        flags.append("blown highlights")

    # --- contrast -------------------------------------------------------
    std = float(gray.std())
    raw["contrast_std"] = std
    metrics["contrast"] = _plateau(std, 0.16, 0.32, 0.20)
    if std < 0.055:
        flags.append("flat")

    # --- colour ---------------------------------------------------------
    colour = colorfulness(rgb)
    raw["colorfulness"] = colour
    metrics["colorfulness"] = _clamp(colour / 70.0)

    # --- noise ----------------------------------------------------------
    sigma = estimate_noise(gray)
    grain = grain_ratio(gray)
    raw["noise_sigma"] = sigma
    raw["grain_ratio"] = grain
    # 0.09 is a clean frame, 0.22 is unpleasant high-ISO grain.
    metrics["noise"] = 1.0 - _log_ramp(grain, 0.09, 0.22)
    if grain > 0.17 and sigma > 0.008:
        flags.append("noisy")

    # --- resolution -----------------------------------------------------
    raw["megapixels"] = megapixels
    # 0.3 Mpx (a web thumbnail) scores 0; 24 Mpx scores 1.
    metrics["resolution"] = _log_ramp(max(megapixels, 1e-6), 0.3, 24.0)

    # --- detail ---------------------------------------------------------
    bits = entropy(gray)
    raw["entropy"] = bits
    metrics["detail"] = _clamp(bits / 5.6)
    if bits < 2.0:
        flags.append("empty")

    if metrics["sharpness"] < 0.22:
        flags.append("blurry")

    # --- combine --------------------------------------------------------
    normalised = weights.normalised()
    score = sum(normalised.get(name, 0.0) * metrics.get(name, 0.0) for name in METRIC_NAMES)

    # High ISO is a reliable hint that what little detail is present is grain,
    # not subject. Small nudge only -- the pixels already had their say.
    iso = exif.get("iso")
    if isinstance(iso, int) and iso > 3200:
        score *= 0.97
        flags.append(f"ISO {iso}")

    return QualityReport(
        metrics=metrics, raw=raw, score=round(_clamp(score) * 100.0, 2), flags=flags
    )


def rescore(metrics: dict[str, float], weights: QualityWeights) -> float:
    """Recompute a score from stored metrics after the user changes weights.

    Re-ranking a whole library is then a millisecond of arithmetic instead of
    a full re-analysis.
    """
    normalised = weights.normalised()
    total = sum(
        normalised.get(name, 0.0) * float(metrics.get(name, 0.0) or 0.0)
        for name in METRIC_NAMES
    )
    return round(_clamp(total) * 100.0, 2)
