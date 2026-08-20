"""Perceptual hashes and colour signatures.

Three complementary 64-bit fingerprints are computed for every photo:

``ahash``
    Average hash. Cheap, tolerant of compression, prone to collisions on
    low-contrast images -- used only as a tie-breaker.
``dhash``
    Difference (gradient) hash. Robust to brightness shifts, sensitive to
    structure. Used to verify pHash candidates.
``phash``
    DCT-based hash. The primary signal: it survives resizing, re-compression,
    watermarks and moderate colour grading, which is exactly the "same photo,
    different file" case people care about.

Plus a small 4x4 mean-colour signature that stops two structurally similar but
differently coloured photos (a sunset and the same frame in daylight) from
being merged.

Hashes are handled as 16-character hex strings so they survive a round trip
through SQLite without the signed-64-bit overflow that plain integers hit.
"""

from __future__ import annotations

import numpy as np

from .imaging import luminance

HASH_BITS = 64
_HASH_SIDE = 8          # 8x8 = 64 bits
_PHASH_SIDE = 32        # DCT is computed on 32x32 and the low 8x8 kept
_COLOR_GRID = 4         # 4x4 colour signature cells

_DCT_CACHE: dict[int, np.ndarray] = {}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _dct_matrix(n: int) -> np.ndarray:
    """Orthonormal DCT-II matrix, cached (numpy only -- no scipy dependency)."""
    cached = _DCT_CACHE.get(n)
    if cached is not None:
        return cached
    k = np.arange(n, dtype=np.float64).reshape(-1, 1)
    x = np.arange(n, dtype=np.float64).reshape(1, -1)
    matrix = np.cos(np.pi * (2.0 * x + 1.0) * k / (2.0 * n))
    matrix *= np.sqrt(2.0 / n)
    matrix[0] *= 1.0 / np.sqrt(2.0)
    _DCT_CACHE[n] = matrix
    return matrix


def _resize_gray(gray: np.ndarray, height: int, width: int) -> np.ndarray:
    """Area-average resize of a 2-D float array.

    Box averaging (rather than nearest-neighbour sampling) matters here: it is
    what makes the hash stable when the same photo arrives at two different
    resolutions.
    """
    src_h, src_w = gray.shape[:2]
    if src_h == height and src_w == width:
        return gray.astype(np.float32)
    if src_h < height or src_w < width:
        # Upscaling: nearest-neighbour is fine, we are adding no information.
        rows = (np.arange(height) * src_h // height).clip(0, src_h - 1)
        cols = (np.arange(width) * src_w // width).clip(0, src_w - 1)
        return gray[np.ix_(rows, cols)].astype(np.float32)

    row_edges = np.linspace(0, src_h, height + 1).astype(np.int64)
    col_edges = np.linspace(0, src_w, width + 1).astype(np.int64)
    # Cumulative sums turn every box average into four array lookups.
    integral = np.zeros((src_h + 1, src_w + 1), dtype=np.float64)
    integral[1:, 1:] = np.cumsum(np.cumsum(gray.astype(np.float64), axis=0), axis=1)

    r0, r1 = row_edges[:-1], row_edges[1:]
    c0, c1 = col_edges[:-1], col_edges[1:]
    r1 = np.maximum(r1, r0 + 1)
    c1 = np.maximum(c1, c0 + 1)
    total = (
        integral[np.ix_(r1, c1)]
        - integral[np.ix_(r0, c1)]
        - integral[np.ix_(r1, c0)]
        + integral[np.ix_(r0, c0)]
    )
    counts = np.outer(r1 - r0, c1 - c0).astype(np.float64)
    return (total / counts).astype(np.float32)


def _bits_to_hex(bits: np.ndarray) -> str:
    """Pack a boolean array of 64 bits, MSB first, into 16 hex characters."""
    flat = bits.reshape(-1)
    if flat.size != HASH_BITS:
        raise ValueError(f"expected {HASH_BITS} bits, got {flat.size}")
    value = 0
    for bit in flat:
        value = (value << 1) | int(bool(bit))
    return f"{value:016x}"


# ---------------------------------------------------------------------------
# the hashes
# ---------------------------------------------------------------------------

def average_hash(gray: np.ndarray) -> str:
    small = _resize_gray(gray, _HASH_SIDE, _HASH_SIDE)
    return _bits_to_hex(small > small.mean())


def difference_hash(gray: np.ndarray) -> str:
    small = _resize_gray(gray, _HASH_SIDE, _HASH_SIDE + 1)
    return _bits_to_hex(small[:, 1:] > small[:, :-1])


def perceptual_hash(gray: np.ndarray) -> str:
    small = _resize_gray(gray, _PHASH_SIDE, _PHASH_SIDE).astype(np.float64)
    matrix = _dct_matrix(_PHASH_SIDE)
    coeffs = matrix @ small @ matrix.T
    block = coeffs[:_HASH_SIDE, :_HASH_SIDE].copy()
    # The DC term encodes average brightness, which we deliberately ignore.
    dc = block[0, 0]
    block[0, 0] = 0.0
    median = np.median(np.concatenate([block.reshape(-1)[1:], [0.0]]))
    bits = block > median
    bits[0, 0] = dc > median  # keep the slot meaningful rather than always 0
    return _bits_to_hex(bits)


def color_signature(rgb: np.ndarray) -> bytes:
    """A 4x4 grid of mean RGB values, quantised to bytes (48 bytes total)."""
    cells = []
    for channel in range(3):
        plane = _resize_gray(rgb[:, :, channel], _COLOR_GRID, _COLOR_GRID)
        cells.append(plane)
    stacked = np.stack(cells, axis=-1)  # 4x4x3, values 0..1
    return np.clip(stacked * 255.0, 0, 255).astype(np.uint8).tobytes()


def color_distance(a: bytes | None, b: bytes | None) -> float:
    """Mean absolute colour difference in 0..1; 1.0 when either side is missing."""
    if not a or not b or len(a) != len(b):
        return 1.0
    left = np.frombuffer(a, dtype=np.uint8).astype(np.int16)
    right = np.frombuffer(b, dtype=np.uint8).astype(np.int16)
    return float(np.abs(left - right).mean() / 255.0)


# ---------------------------------------------------------------------------
# distances
# ---------------------------------------------------------------------------

def hamming(a: str | None, b: str | None) -> int:
    """Hamming distance between two hex hashes; ``HASH_BITS`` if either is missing."""
    if not a or not b:
        return HASH_BITS
    try:
        return (int(a, 16) ^ int(b, 16)).bit_count()
    except ValueError:
        return HASH_BITS


def similarity_percent(distance: int) -> float:
    """Turn a Hamming distance into the 0-100 figure shown in the UI."""
    return max(0.0, min(100.0, 100.0 * (1.0 - distance / HASH_BITS)))


# ---------------------------------------------------------------------------
# rotation-aware hashing
# ---------------------------------------------------------------------------

def rotated_hashes(gray: np.ndarray) -> list[str]:
    """pHashes of the image at 0/90/180/270 degrees.

    Used by the optional "also match rotated copies" mode: a photo is indexed
    under every rotation, so an upright original and its sideways copy land in
    the same bucket.
    """
    out = []
    current = gray
    for _ in range(4):
        out.append(perceptual_hash(current))
        current = np.rot90(current)
    return out


def fingerprint(gray: np.ndarray, rgb: np.ndarray, with_rotations: bool = False) -> dict:
    """Compute every fingerprint for one decoded image."""
    data = {
        "ahash": average_hash(gray),
        "dhash": difference_hash(gray),
        "phash": perceptual_hash(gray),
        "color_sig": color_signature(rgb),
    }
    if with_rotations:
        data["rotations"] = rotated_hashes(gray)
    return data
