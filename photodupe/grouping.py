"""Clustering photos into duplicate groups.

The naive approach -- compare every photo with every other -- is O(n^2) and
falls over on a real library (50k photos is 1.25 billion comparisons). Instead:

1. Byte-identical files are grouped straight from their SHA-256.
2. Near-duplicates are found with a **BK-tree** over the perceptual hashes.
   Because Hamming distance is a metric, the triangle inequality prunes most of
   the tree on every lookup, so a search is closer to O(log n) than O(n).
3. Candidate pairs are then *verified* against the difference hash and the
   colour signature before being accepted. pHash alone produces occasional
   false positives on flat or highly symmetrical images; requiring two
   independent fingerprints to agree removes nearly all of them.
4. Verified pairs are merged with **union-find**, so a chain of near-identical
   frames (a burst) ends up as one group rather than many overlapping pairs.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

from .config import Settings
from .hashing import HASH_BITS, color_distance, hamming, similarity_percent
from .records import DuplicateGroup, Photo


# ---------------------------------------------------------------------------
# union-find
# ---------------------------------------------------------------------------

class UnionFind:
    """Disjoint-set forest with path compression and union by size."""

    def __init__(self) -> None:
        self._parent: dict[int, int] = {}
        self._size: dict[int, int] = {}

    def add(self, item: int) -> None:
        if item not in self._parent:
            self._parent[item] = item
            self._size[item] = 1

    def find(self, item: int) -> int:
        self.add(item)
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[item] != root:  # path compression
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, a: int, b: int) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a == root_b:
            return
        if self._size[root_a] < self._size[root_b]:
            root_a, root_b = root_b, root_a
        self._parent[root_b] = root_a
        self._size[root_a] += self._size[root_b]

    def groups(self) -> dict[int, list[int]]:
        clusters: dict[int, list[int]] = defaultdict(list)
        for item in self._parent:
            clusters[self.find(item)].append(item)
        return clusters


# ---------------------------------------------------------------------------
# BK-tree
# ---------------------------------------------------------------------------

@dataclass
class _Node:
    key: str
    payload: list[int]
    children: dict[int, "_Node"]


class BKTree:
    """Metric tree for fast "all hashes within distance d" queries."""

    def __init__(self) -> None:
        self._root: _Node | None = None
        self._count = 0

    def __len__(self) -> int:
        return self._count

    def add(self, key: str, item: int) -> None:
        self._count += 1
        if self._root is None:
            self._root = _Node(key, [item], {})
            return
        node = self._root
        while True:
            distance = hamming(node.key, key)
            if distance == 0:
                node.payload.append(item)
                return
            child = node.children.get(distance)
            if child is None:
                node.children[distance] = _Node(key, [item], {})
                return
            node = child

    def query(self, key: str, max_distance: int) -> list[tuple[int, int]]:
        """Return ``(item, distance)`` for every entry within ``max_distance``."""
        if self._root is None:
            return []
        results: list[tuple[int, int]] = []
        stack = [self._root]
        while stack:
            node = stack.pop()
            distance = hamming(node.key, key)
            if distance <= max_distance:
                results.extend((item, distance) for item in node.payload)
            # Triangle inequality: only children whose edge length is within
            # +/- max_distance of this node's distance can possibly match.
            low, high = distance - max_distance, distance + max_distance
            for edge, child in node.children.items():
                if low <= edge <= high:
                    stack.append(child)
        return results


# ---------------------------------------------------------------------------
# grouping
# ---------------------------------------------------------------------------

def _verify(a: Photo, b: Photo, settings: Settings) -> bool:
    """Second opinion on a pHash candidate pair."""
    if hamming(a.dhash, b.dhash) > settings.dhash_threshold:
        return False
    if color_distance(a.color_sig, b.color_sig) > settings.color_threshold:
        return False
    return True


def pick_best(photos: Sequence[Photo], settings: Settings) -> int:
    """Choose which photo in a group to keep.

    Quality score decides it. Ties -- common between a file and a byte-identical
    copy -- fall through to resolution, then file size (which separates an
    original from a re-compressed copy), then the oldest modification time,
    which is usually the original rather than the duplicate.
    """
    if not photos:
        return 0
    prefer_large = settings.prefer_larger_on_tie

    def sort_key(photo: Photo) -> tuple:
        return (
            round(photo.score, 2),
            (photo.width * photo.height) if prefer_large else 0,
            photo.size if prefer_large else 0,
            -photo.mtime,
            -photo.id,
        )

    return max(photos, key=sort_key).id


def build_groups(
    photos: Iterable[Photo],
    settings: Settings,
    rotations: dict[int, list[str]] | None = None,
    progress: Callable[[int, int], bool] | None = None,
) -> list[DuplicateGroup]:
    """Cluster ``photos`` into duplicate groups, best-first.

    ``progress`` is called as ``(done, total)`` and may return ``False`` to
    abort the run, which is how the GUI implements Cancel.
    """
    usable = [p for p in photos if p.status == "ok" and p.phash]
    total = len(usable)
    if total < 2:
        return []

    by_id = {photo.id: photo for photo in usable}
    union = UnionFind()
    for photo in usable:
        union.add(photo.id)

    exact_ids: set[int] = set()

    # --- pass 1: byte-identical files ----------------------------------
    if settings.group_exact_duplicates:
        by_digest: dict[str, list[int]] = defaultdict(list)
        for photo in usable:
            if photo.sha256:
                by_digest[photo.sha256].append(photo.id)
        for ids in by_digest.values():
            if len(ids) > 1:
                exact_ids.update(ids)
                first = ids[0]
                for other in ids[1:]:
                    union.union(first, other)

    # --- pass 2: perceptual near-duplicates ----------------------------
    tree = BKTree()
    for photo in usable:
        tree.add(photo.phash, photo.id)
        if settings.detect_rotations and rotations:
            # Index the rotated variants too, all pointing back at the same
            # photo, so an upright frame finds its sideways copy.
            for variant in (rotations.get(photo.id) or [])[1:]:
                tree.add(variant, photo.id)

    threshold = max(0, min(HASH_BITS, settings.similarity_threshold))
    for done, photo in enumerate(usable, start=1):
        for other_id, _distance in tree.query(photo.phash, threshold):
            if other_id == photo.id:
                continue
            if union.find(other_id) == union.find(photo.id):
                continue  # already in the same cluster; skip the verification
            other = by_id.get(other_id)
            if other is not None and _verify(photo, other, settings):
                union.union(photo.id, other_id)
        if progress is not None and done % 64 == 0:
            if progress(done, total) is False:
                return []
    if progress is not None:
        progress(total, total)

    # --- assemble ------------------------------------------------------
    groups: list[DuplicateGroup] = []
    for members in union.groups().values():
        if len(members) < 2:
            continue
        members_photos = [by_id[i] for i in members if i in by_id]
        if len(members_photos) < 2:
            continue
        kind = (
            "exact"
            if all(p.id in exact_ids for p in members_photos)
            and len({p.sha256 for p in members_photos}) == 1
            else "similar"
        )
        members_photos.sort(key=lambda p: (-p.score, p.name))
        group = DuplicateGroup(
            photos=members_photos,
            kind=kind,
            best_id=pick_best(members_photos, settings),
            tightness=_tightness(members_photos, kind),
        )
        groups.append(group)

    # Biggest wins first: the groups that free the most space and hold the
    # most files are the ones worth a person's attention.
    groups.sort(key=lambda g: (-g.wasted_bytes(), -len(g.photos)))
    for index, group in enumerate(groups, start=1):
        group.index = index
    return groups


def _tightness(photos: Sequence[Photo], kind: str) -> float:
    """Mean pairwise similarity inside a group, as a percentage."""
    if kind == "exact":
        return 100.0
    if len(photos) < 2:
        return 100.0
    anchor = photos[0]
    distances = [hamming(anchor.phash, other.phash) for other in photos[1:]]
    if not distances:
        return 100.0
    return round(sum(similarity_percent(d) for d in distances) / len(distances), 1)


def group_statistics(groups: Sequence[DuplicateGroup]) -> dict[str, int | float]:
    duplicates = sum(len(g.photos) - 1 for g in groups)
    return {
        "groups": len(groups),
        "photos_in_groups": sum(len(g.photos) for g in groups),
        "removable": duplicates,
        "wasted_bytes": sum(g.wasted_bytes() for g in groups),
        "exact_groups": sum(1 for g in groups if g.kind == "exact"),
    }
