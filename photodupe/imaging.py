"""Loading, decoding and thumbnailing photos.

Everything that touches Pillow lives here so the rest of the code can work with
plain numpy arrays and dictionaries. Decoding is the single most expensive part
of indexing a library, so this module goes out of its way to decode *once*, at
the smallest size that is still useful, and to hand the same array to both the
hashing and the quality code.
"""

from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageFile, ImageOps

log = logging.getLogger(__name__)

# Truncated files are common in real photo dumps (interrupted card copies).
# Better to index a slightly damaged JPEG than to drop it on the floor.
ImageFile.LOAD_TRUNCATED_IMAGES = True
# Pillow's default bomb guard (~89 Mpx) rejects legitimate panoramas and
# scanner output. Raise it, but keep a ceiling so a malicious file cannot
# make us allocate unbounded memory.
Image.MAX_IMAGE_PIXELS = 500_000_000

HEIF_AVAILABLE = False
try:  # optional dependency, only needed for iPhone HEIC files
    import pillow_heif  # type: ignore

    pillow_heif.register_heif_opener()
    HEIF_AVAILABLE = True
except Exception:  # pragma: no cover - depends on the user's install
    pass


class ImageLoadError(RuntimeError):
    """Raised when a file cannot be decoded as an image."""


# ---------------------------------------------------------------------------
# EXIF
# ---------------------------------------------------------------------------

_EXIF_TAGS = {
    271: "camera_make",
    274: "orientation",
    272: "camera_model",
    306: "datetime",
    33434: "exposure_time",
    33437: "f_number",
    34855: "iso",
    36867: "datetime_original",
    37377: "shutter_speed",
    37386: "focal_length",
    41986: "exposure_mode",
    42036: "lens_model",
}


def _to_float(value: Any) -> float | None:
    try:
        if isinstance(value, tuple) and len(value) == 2:
            num, den = value
            return float(num) / float(den) if den else None
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _parse_exif_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip().rstrip("\x00")
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def read_exif(image: Image.Image) -> dict[str, Any]:
    """Pull the handful of EXIF fields we actually display or rank on."""
    out: dict[str, Any] = {}
    try:
        exif = image.getexif()
    except Exception:  # pragma: no cover - corrupt EXIF blocks
        return out
    if not exif:
        return out

    for tag, name in _EXIF_TAGS.items():
        if tag in exif:
            out[name] = exif[tag]
    try:
        ifd = exif.get_ifd(0x8769)  # ExifIFD holds the interesting capture data
        for tag, name in _EXIF_TAGS.items():
            if tag in ifd and name not in out:
                out[name] = ifd[tag]
    except Exception:  # pragma: no cover
        pass

    for key in ("exposure_time", "f_number", "focal_length", "shutter_speed"):
        if key in out:
            converted = _to_float(out[key])
            if converted is None:
                out.pop(key, None)
            else:
                out[key] = converted
    if "orientation" in out:
        try:
            out["orientation"] = int(out["orientation"])
        except (TypeError, ValueError):
            out.pop("orientation")
    if "iso" in out:
        iso = _to_float(out["iso"])
        out["iso"] = int(iso) if iso else None
        if not out["iso"]:
            out.pop("iso")

    taken = _parse_exif_datetime(out.get("datetime_original")) or _parse_exif_datetime(
        out.get("datetime")
    )
    if taken:
        out["taken_at"] = taken
    for key in ("camera_make", "camera_model", "lens_model"):
        value = out.get(key)
        if isinstance(value, bytes):
            value = value.decode("utf-8", "replace")
        if isinstance(value, str):
            out[key] = value.strip().rstrip("\x00")
    return out


def camera_label(exif: dict[str, Any]) -> str:
    make = str(exif.get("camera_make") or "").strip()
    model = str(exif.get("camera_model") or "").strip()
    if make and model and model.lower().startswith(make.lower()):
        return model
    return " ".join(part for part in (make, model) if part).strip()


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

@dataclass
class LoadedImage:
    """A decoded photo, reduced to what the analysis stages need."""

    path: Path
    width: int              # full-resolution pixel dimensions
    height: int
    format: str
    rgb: np.ndarray         # float32 HxWx3 in 0..1, long edge <= analysis_max_edge
    gray: np.ndarray        # float32 HxW in 0..1
    exif: dict[str, Any] = field(default_factory=dict)

    @property
    def megapixels(self) -> float:
        return (self.width * self.height) / 1_000_000.0


def _apply_orientation(image: Image.Image) -> Image.Image:
    try:
        return ImageOps.exif_transpose(image) or image
    except Exception:  # pragma: no cover - broken EXIF orientation tags
        return image


def open_image(path: Path | str) -> Image.Image:
    """Open a file as a Pillow image, normalised for orientation."""
    path = Path(path)
    try:
        image = Image.open(path)
        image.load()
    except FileNotFoundError:
        raise
    except Exception as exc:  # Pillow raises a zoo of exception types
        suffix = path.suffix.lower()
        if suffix in (".heic", ".heif") and not HEIF_AVAILABLE:
            raise ImageLoadError(
                f"{path.name}: HEIC support needs the 'pillow-heif' package "
                f"(pip install pillow-heif)"
            ) from exc
        raise ImageLoadError(f"{path.name}: {exc}") from exc
    return _apply_orientation(image)


#: EXIF orientation values 5-8 rotate by 90 degrees, swapping width and height.
_TRANSPOSED_ORIENTATIONS = frozenset({5, 6, 7, 8})


def load_for_analysis(path: Path | str, max_edge: int = 512) -> LoadedImage:
    """Decode ``path`` once, downscaled, ready for hashing and scoring.

    JPEG decoding uses Pillow's DCT scaling (``draft``) so a 24 Mpx photo is
    decoded at roughly the target size instead of in full and then thrown away.
    """
    path = Path(path)
    try:
        image = Image.open(path)
        # Image.open() only parses the header, so this is the true stored size.
        # It has to be read *before* draft(), which rewrites image.size.
        full_w, full_h = image.size
        exif = read_exif(image)
        # draft() is a no-op for formats that cannot do scaled decoding.
        try:
            image.draft("RGB", (max_edge, max_edge))
        except Exception:
            pass
        image.load()
    except FileNotFoundError:
        raise
    except Exception as exc:
        suffix = path.suffix.lower()
        if suffix in (".heic", ".heif") and not HEIF_AVAILABLE:
            raise ImageLoadError(
                f"{path.name}: HEIC support needs the 'pillow-heif' package "
                f"(pip install pillow-heif)"
            ) from exc
        raise ImageLoadError(f"{path.name}: {exc}") from exc

    fmt = (image.format or path.suffix.lstrip(".").upper() or "UNKNOWN").upper()
    orientation = exif.get("orientation")
    image = _apply_orientation(image)
    # exif_transpose may have rotated the pixels; the reported full-resolution
    # size has to follow, or a portrait phone photo reads as landscape.
    if orientation in _TRANSPOSED_ORIENTATIONS:
        full_w, full_h = full_h, full_w

    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    working = normalise_scale(image.convert("RGB"), max_edge)

    rgb = np.asarray(working, dtype=np.float32) / 255.0
    if rgb.ndim == 2:  # paranoia: a grayscale sneaking through convert("RGB")
        rgb = np.repeat(rgb[:, :, None], 3, axis=2)
    gray = luminance(rgb)

    return LoadedImage(
        path=path,
        width=int(full_w),
        height=int(full_h),
        format=fmt,
        rgb=rgb,
        gray=gray,
        exif=exif,
    )


def normalise_scale(image: Image.Image, max_edge: int) -> Image.Image:
    """Resample so the long edge is exactly ``max_edge`` pixels.

    Both directions matter. Downscaling keeps analysis cheap and comparable;
    *upscaling* small images is what makes sharpness meaningful across mixed
    resolutions -- a 320 px web copy blown up to the common size really is
    softer than a 24 Mpx original reduced to it, which is exactly the judgement
    a person makes when comparing the two on screen.
    """
    width, height = image.size
    longest = max(width, height)
    if longest == max_edge or longest <= 0:
        return image
    ratio = max_edge / float(longest)
    target = (max(1, round(width * ratio)), max(1, round(height * ratio)))
    resample = (
        Image.Resampling.BILINEAR if ratio < 1.0 else Image.Resampling.BICUBIC
    )
    return image.resize(target, resample)


def luminance(rgb: np.ndarray) -> np.ndarray:
    """Rec. 709 luma of an HxWx3 float array."""
    return (
        0.2126 * rgb[:, :, 0] + 0.7152 * rgb[:, :, 1] + 0.0722 * rgb[:, :, 2]
    ).astype(np.float32)


# ---------------------------------------------------------------------------
# Hashing of file content
# ---------------------------------------------------------------------------

def file_digest(path: Path | str, chunk_size: int = 1 << 20) -> str:
    """SHA-256 of the file's bytes -- identifies byte-identical duplicates."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Thumbnails
# ---------------------------------------------------------------------------

def thumbnail_key(path: Path | str, size: int, mtime: float) -> str:
    raw = f"{Path(path).resolve()}|{size}|{int(mtime)}".encode("utf-8", "replace")
    return hashlib.sha1(raw).hexdigest()


def write_thumbnail(
    source: Path | str,
    destination: Path,
    size: int = 256,
    image: Image.Image | None = None,
) -> Path:
    """Render a cached JPEG thumbnail, letterbox-free (aspect preserved)."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    owns_image = image is None
    if image is None:
        img = Image.open(source)
        try:
            img.draft("RGB", (size * 2, size * 2))
        except Exception:
            pass
        img.load()
        img = _apply_orientation(img)
    else:
        img = image
    try:
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        thumb = img.convert("RGB")
        thumb.thumbnail((size, size), Image.Resampling.LANCZOS)
        tmp = destination.with_suffix(".part")
        thumb.save(tmp, "JPEG", quality=85, optimize=True)
        tmp.replace(destination)
    finally:
        if owns_image:
            img.close()
    return destination


def resized_copy(
    source: Path | str, destination: Path, max_edge: int, jpeg_quality: int = 92
) -> Path:
    """Write a downscaled copy of ``source``; used by the exporter."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as img:
        img = _apply_orientation(img)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        if max_edge > 0 and max(img.size) > max_edge:
            img.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        suffix = destination.suffix.lower()
        if suffix in (".jpg", ".jpeg"):
            img.convert("RGB").save(
                destination, "JPEG", quality=jpeg_quality, optimize=True, subsampling=0
            )
        elif suffix == ".png":
            img.save(destination, "PNG", optimize=True)
        elif suffix == ".webp":
            img.save(destination, "WEBP", quality=jpeg_quality)
        else:
            img.convert("RGB").save(
                destination.with_suffix(".jpg"), "JPEG", quality=jpeg_quality
            )
            destination = destination.with_suffix(".jpg")
    return destination


def human_size(num_bytes: float) -> str:
    if num_bytes < 1024:
        return f"{int(num_bytes)} B"
    for unit in ("KB", "MB", "GB", "TB"):
        num_bytes /= 1024.0
        if num_bytes < 1024 or unit == "TB":
            precision = 0 if num_bytes >= 100 else 1
            return f"{num_bytes:.{precision}f} {unit}"
    return f"{num_bytes:.1f} TB"  # pragma: no cover


def aspect_label(width: int, height: int) -> str:
    if not width or not height:
        return "-"
    divisor = math.gcd(width, height)
    w, h = width // divisor, height // divisor
    if w > 30 or h > 30:  # unusual ratio: show the decimal instead of 1997:1331
        return f"{width / height:.2f}:1"
    return f"{w}:{h}"
