"""The plain-data types shared by the storage, analysis and UI layers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .quality import score_grade

#: Review decisions the user can attach to a photo.
MARK_NONE = "none"
MARK_KEEP = "keep"
MARK_DELETE = "delete"


@dataclass
class Photo:
    """One indexed photo. Mirrors a row of the ``photos`` table."""

    id: int = 0
    path: str = ""
    filename: str = ""
    size: int = 0
    mtime: float = 0.0
    sha256: str = ""
    width: int = 0
    height: int = 0
    format: str = ""
    taken_at: datetime | None = None
    camera: str = ""
    iso: int | None = None

    phash: str = ""
    dhash: str = ""
    ahash: str = ""
    color_sig: bytes | None = None

    score: float = 0.0
    metrics: dict[str, float] = field(default_factory=dict)
    raw_metrics: dict[str, float] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)

    mark: str = MARK_NONE
    rating: int = 0              # user star override, 0 = unset
    thumbnail: str = ""
    indexed_at: float = 0.0
    status: str = "ok"           # ok | error
    error: str = ""

    # ------------------------------------------------------------------
    @property
    def megapixels(self) -> float:
        return (self.width * self.height) / 1_000_000.0

    @property
    def name(self) -> str:
        return self.filename or Path(self.path).name

    @property
    def folder(self) -> str:
        return str(Path(self.path).parent)

    def grade(self) -> str:
        return score_grade(self.score)

    def exists(self) -> bool:
        return Path(self.path).exists()

    def bytes_per_pixel(self) -> float:
        pixels = self.width * self.height
        return (self.size / pixels) if pixels else 0.0

    def metrics_json(self) -> str:
        return json.dumps(
            {"metrics": self.metrics, "raw": self.raw_metrics, "flags": self.flags}
        )

    def load_metrics_json(self, text: str | None) -> None:
        if not text:
            return
        try:
            payload = json.loads(text)
        except ValueError:
            return
        if not isinstance(payload, dict):
            return
        self.metrics = payload.get("metrics") or {}
        self.raw_metrics = payload.get("raw") or {}
        self.flags = payload.get("flags") or []

    def summary(self) -> str:
        bits = [f"{self.width}x{self.height}"]
        if self.megapixels >= 0.1:
            bits.append(f"{self.megapixels:.1f} MP")
        if self.camera:
            bits.append(self.camera)
        if self.taken_at:
            bits.append(self.taken_at.strftime("%Y-%m-%d"))
        return "  ".join(bits)


@dataclass
class DuplicateGroup:
    """A set of photos judged to be the same picture."""

    index: int = 0
    photos: list[Photo] = field(default_factory=list)
    kind: str = "similar"          # exact | similar
    best_id: int = 0
    tightness: float = 0.0         # mean similarity within the group, 0..100

    def __len__(self) -> int:
        return len(self.photos)

    @property
    def best(self) -> Photo | None:
        for photo in self.photos:
            if photo.id == self.best_id:
                return photo
        return self.photos[0] if self.photos else None

    @property
    def others(self) -> list[Photo]:
        return [photo for photo in self.photos if photo.id != self.best_id]

    def wasted_bytes(self) -> int:
        """Bytes that would be reclaimed by keeping only the best photo."""
        return sum(photo.size for photo in self.others)

    def label(self) -> str:
        kind = "Identical" if self.kind == "exact" else "Similar"
        return f"{kind} - {len(self.photos)} photos"

    def to_json(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "kind": self.kind,
            "best_id": self.best_id,
            "tightness": self.tightness,
            "photos": [photo.id for photo in self.photos],
        }
