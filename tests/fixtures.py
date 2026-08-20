"""Synthetic photo sets used by the tests.

Real photographs cannot be committed to the repository, so the fixtures
generate images whose *spectrum* matches natural photographs (1/f noise) and
then apply the transformations we actually care about detecting: re-encoding,
resizing, blurring, brightening and adding grain.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter


def pink_noise(height: int, width: int, seed: int, beta: float = 1.2) -> np.ndarray:
    """A 2-D field with a 1/f^beta power spectrum, normalised to 0..1."""
    rng = np.random.default_rng(seed)
    fy = np.fft.fftfreq(height)[:, None]
    fx = np.fft.fftfreq(width)[None, :]
    radius = np.sqrt(fy**2 + fx**2)
    radius[0, 0] = 1e-6
    spectrum = np.fft.fft2(rng.normal(size=(height, width))) / radius**beta
    field = np.real(np.fft.ifft2(spectrum))
    span = field.max() - field.min()
    return (field - field.min()) / (span if span else 1.0)


def synthetic_photo(seed: int, height: int = 900, width: int = 1200) -> Image.Image:
    """A plausible-looking "photograph" with structure at every scale."""
    planes = [pink_noise(height, width, seed + offset) for offset in (0, 101, 202)]
    image = np.stack(planes, axis=-1)
    image = 0.5 + 0.8 * (image - image.mean())
    return Image.fromarray((np.clip(image, 0, 1) * 255).astype(np.uint8))


def build_photo_set(root: Path) -> dict[str, list[str]]:
    """Write a folder of photos with a known duplicate structure.

    Returns ``{"expected_group_name": [relative paths that belong together]}``
    so tests can assert on grouping without hard-coding hash values.
    """
    root = Path(root)
    (root / "trip").mkdir(parents=True, exist_ok=True)
    (root / "backup").mkdir(parents=True, exist_ok=True)
    expected: dict[str, list[str]] = {}

    # --- group A: one photo saved three ways ---------------------------
    original = synthetic_photo(1)
    original.save(root / "trip" / "beach.jpg", quality=95)
    original.resize((600, 450), Image.LANCZOS).save(
        root / "backup" / "beach-small.jpg", quality=70
    )
    original.save(root / "backup" / "beach-copy.png")
    expected["beach"] = [
        "trip/beach.jpg", "backup/beach-small.jpg", "backup/beach-copy.png",
    ]

    # --- group B: byte-identical copies --------------------------------
    sunset = synthetic_photo(2)
    sunset.save(root / "trip" / "sunset.jpg", quality=92)
    (root / "backup" / "sunset.jpg").write_bytes(
        (root / "trip" / "sunset.jpg").read_bytes()
    )
    expected["sunset"] = ["trip/sunset.jpg", "backup/sunset.jpg"]

    # --- group C: a burst, sharp frame plus a blurred one --------------
    burst = synthetic_photo(3)
    burst.save(root / "trip" / "burst-sharp.jpg", quality=95)
    burst.filter(ImageFilter.GaussianBlur(5)).save(
        root / "trip" / "burst-blurred.jpg", quality=95
    )
    expected["burst"] = ["trip/burst-sharp.jpg", "trip/burst-blurred.jpg"]

    # --- singletons: must NOT be grouped with anything -----------------
    for index, seed in enumerate((11, 12, 13), start=1):
        synthetic_photo(seed).save(root / "trip" / f"unique-{index}.jpg", quality=93)

    # --- a file that is not a real image -------------------------------
    (root / "trip" / "broken.jpg").write_bytes(b"this is not a JPEG" * 300)

    return expected
