"""SQLite storage for the photo index.

The database is a *cache*, never the source of truth -- the photos on disk are.
Its job is to make re-scanning cheap: a file whose size and mtime match the
stored row is skipped without decoding it, which turns a rescan of a 50k photo
library from minutes into a couple of seconds.

Connections are per-thread (SQLite objects cannot be shared across threads) and
the database runs in WAL mode so the GUI can read while a worker writes.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from .records import MARK_NONE, Photo

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS photos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    path        TEXT    NOT NULL UNIQUE,
    filename    TEXT    NOT NULL DEFAULT '',
    size        INTEGER NOT NULL DEFAULT 0,
    mtime       REAL    NOT NULL DEFAULT 0,
    sha256      TEXT    NOT NULL DEFAULT '',
    width       INTEGER NOT NULL DEFAULT 0,
    height      INTEGER NOT NULL DEFAULT 0,
    format      TEXT    NOT NULL DEFAULT '',
    taken_at    TEXT,
    camera      TEXT    NOT NULL DEFAULT '',
    iso         INTEGER,
    phash       TEXT    NOT NULL DEFAULT '',
    dhash       TEXT    NOT NULL DEFAULT '',
    ahash       TEXT    NOT NULL DEFAULT '',
    color_sig   BLOB,
    rotations   TEXT,
    score       REAL    NOT NULL DEFAULT 0,
    metrics     TEXT,
    mark        TEXT    NOT NULL DEFAULT 'none',
    rating      INTEGER NOT NULL DEFAULT 0,
    thumbnail   TEXT    NOT NULL DEFAULT '',
    indexed_at  REAL    NOT NULL DEFAULT 0,
    status      TEXT    NOT NULL DEFAULT 'ok',
    error       TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_photos_sha    ON photos(sha256);
CREATE INDEX IF NOT EXISTS idx_photos_score  ON photos(score DESC);
CREATE INDEX IF NOT EXISTS idx_photos_status ON photos(status);
CREATE INDEX IF NOT EXISTS idx_photos_mark   ON photos(mark);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

_COLUMNS = (
    "id", "path", "filename", "size", "mtime", "sha256", "width", "height",
    "format", "taken_at", "camera", "iso", "phash", "dhash", "ahash",
    "color_sig", "rotations", "score", "metrics", "mark", "rating",
    "thumbnail", "indexed_at", "status", "error",
)

SORT_FIELDS = {
    "score": "score DESC, filename ASC",
    "score_asc": "score ASC, filename ASC",
    "name": "filename COLLATE NOCASE ASC",
    "name_desc": "filename COLLATE NOCASE DESC",
    "date": "COALESCE(taken_at, '') DESC, mtime DESC",
    "date_asc": "COALESCE(taken_at, '') ASC, mtime ASC",
    "size": "size DESC",
    "size_asc": "size ASC",
    "resolution": "(width * height) DESC",
    "folder": "path COLLATE NOCASE ASC",
}


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def row_to_photo(row: sqlite3.Row) -> Photo:
    photo = Photo(
        id=row["id"],
        path=row["path"],
        filename=row["filename"],
        size=row["size"],
        mtime=row["mtime"],
        sha256=row["sha256"],
        width=row["width"],
        height=row["height"],
        format=row["format"],
        taken_at=_parse_datetime(row["taken_at"]),
        camera=row["camera"],
        iso=row["iso"],
        phash=row["phash"],
        dhash=row["dhash"],
        ahash=row["ahash"],
        color_sig=row["color_sig"],
        score=row["score"],
        mark=row["mark"] or MARK_NONE,
        rating=row["rating"] or 0,
        thumbnail=row["thumbnail"],
        indexed_at=row["indexed_at"],
        status=row["status"],
        error=row["error"],
    )
    photo.load_metrics_json(row["metrics"])
    return photo


class Database:
    """Thread-safe-enough wrapper around the index database."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._write_lock = threading.Lock()
        self._initialise()

    # ------------------------------------------------------------------
    @property
    def connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.path), timeout=30.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    def _initialise(self) -> None:
        with self._write_lock:
            self.connection.executescript(_SCHEMA)
            self.connection.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            self.connection.commit()

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # ------------------------------------------------------------------
    # meta
    # ------------------------------------------------------------------
    def get_meta(self, key: str, default: str = "") -> str:
        row = self.connection.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self._write_lock:
            self.connection.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self.connection.commit()

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------
    def upsert(self, photo: Photo, rotations: Sequence[str] | None = None) -> int:
        """Insert or update one photo, keyed on its path."""
        payload = {
            "path": str(photo.path),
            "filename": photo.name,
            "size": photo.size,
            "mtime": photo.mtime,
            "sha256": photo.sha256,
            "width": photo.width,
            "height": photo.height,
            "format": photo.format,
            "taken_at": photo.taken_at.isoformat() if photo.taken_at else None,
            "camera": photo.camera,
            "iso": photo.iso,
            "phash": photo.phash,
            "dhash": photo.dhash,
            "ahash": photo.ahash,
            "color_sig": photo.color_sig,
            "rotations": json.dumps(list(rotations)) if rotations else None,
            "score": photo.score,
            "metrics": photo.metrics_json(),
            "thumbnail": photo.thumbnail,
            "indexed_at": photo.indexed_at,
            "status": photo.status,
            "error": photo.error,
        }
        columns = ", ".join(payload)
        placeholders = ", ".join(f":{name}" for name in payload)
        # mark and rating are deliberately excluded from the UPDATE: a rescan
        # must never wipe out review decisions the user already made.
        updates = ", ".join(
            f"{name} = excluded.{name}" for name in payload if name != "path"
        )
        with self._write_lock:
            cursor = self.connection.execute(
                f"INSERT INTO photos ({columns}) VALUES ({placeholders}) "
                f"ON CONFLICT(path) DO UPDATE SET {updates}",
                payload,
            )
            self.connection.commit()
            if cursor.lastrowid:
                photo.id = int(cursor.lastrowid)
        if not photo.id:
            row = self.connection.execute(
                "SELECT id FROM photos WHERE path = ?", (str(photo.path),)
            ).fetchone()
            if row:
                photo.id = row["id"]
        return photo.id

    def upsert_many(self, photos: Iterable[tuple[Photo, Sequence[str] | None]]) -> int:
        count = 0
        for photo, rotations in photos:
            self.upsert(photo, rotations)
            count += 1
        return count

    def update_scores(self, scores: dict[int, float]) -> None:
        """Bulk re-score after the user changes the ranking weights."""
        if not scores:
            return
        with self._write_lock:
            self.connection.executemany(
                "UPDATE photos SET score = ? WHERE id = ?",
                [(value, key) for key, value in scores.items()],
            )
            self.connection.commit()

    def set_mark(self, photo_ids: Sequence[int], mark: str) -> None:
        if not photo_ids:
            return
        with self._write_lock:
            self.connection.executemany(
                "UPDATE photos SET mark = ? WHERE id = ?",
                [(mark, pid) for pid in photo_ids],
            )
            self.connection.commit()

    def set_rating(self, photo_id: int, rating: int) -> None:
        with self._write_lock:
            self.connection.execute(
                "UPDATE photos SET rating = ? WHERE id = ?",
                (max(0, min(5, rating)), photo_id),
            )
            self.connection.commit()

    def remove_paths(self, paths: Iterable[str]) -> int:
        paths = [str(p) for p in paths]
        if not paths:
            return 0
        removed = 0
        with self._write_lock:
            for chunk_start in range(0, len(paths), 400):
                chunk = paths[chunk_start : chunk_start + 400]
                marks = ", ".join("?" * len(chunk))
                cursor = self.connection.execute(
                    f"DELETE FROM photos WHERE path IN ({marks})", chunk
                )
                removed += cursor.rowcount
            self.connection.commit()
        return removed

    def rename_path(self, old_path: str, new_path: str) -> None:
        with self._write_lock:
            self.connection.execute(
                "UPDATE photos SET path = ?, filename = ? WHERE path = ?",
                (str(new_path), Path(new_path).name, str(old_path)),
            )
            self.connection.commit()

    def clear(self) -> None:
        with self._write_lock:
            self.connection.execute("DELETE FROM photos")
            self.connection.commit()
            self.connection.execute("VACUUM")

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------
    def get(self, photo_id: int) -> Photo | None:
        row = self.connection.execute(
            "SELECT * FROM photos WHERE id = ?", (photo_id,)
        ).fetchone()
        return row_to_photo(row) if row else None

    def get_by_path(self, path: Path | str) -> Photo | None:
        row = self.connection.execute(
            "SELECT * FROM photos WHERE path = ?", (str(path),)
        ).fetchone()
        return row_to_photo(row) if row else None

    def index_signature(self) -> dict[str, tuple[int, float]]:
        """``{path: (size, mtime)}`` for every indexed file.

        Loaded once at the start of a scan so the "has this file changed?"
        check is a dictionary lookup instead of a query per file.
        """
        return {
            row["path"]: (row["size"], row["mtime"])
            for row in self.connection.execute(
                "SELECT path, size, mtime FROM photos WHERE status = 'ok'"
            )
        }

    def known_paths(self) -> set[str]:
        return {
            row["path"] for row in self.connection.execute("SELECT path FROM photos")
        }

    def digests(self) -> dict[str, str]:
        """``{sha256: path}`` -- used by the importer to skip existing files."""
        return {
            row["sha256"]: row["path"]
            for row in self.connection.execute(
                "SELECT sha256, path FROM photos WHERE sha256 != ''"
            )
        }

    def rotations(self) -> dict[int, list[str]]:
        out: dict[int, list[str]] = {}
        for row in self.connection.execute(
            "SELECT id, rotations FROM photos WHERE rotations IS NOT NULL"
        ):
            try:
                out[row["id"]] = json.loads(row["rotations"])
            except ValueError:
                continue
        return out

    def iter_photos(
        self,
        sort: str = "score",
        only_ok: bool = True,
        search: str = "",
        mark: str = "",
        min_score: float | None = None,
        max_score: float | None = None,
        flag: str = "",
        limit: int = 0,
    ) -> Iterator[Photo]:
        where: list[str] = []
        params: list[Any] = []
        if only_ok:
            where.append("status = 'ok'")
        if search:
            where.append("(path LIKE ? OR camera LIKE ?)")
            pattern = f"%{search}%"
            params += [pattern, pattern]
        if mark:
            where.append("mark = ?")
            params.append(mark)
        if min_score is not None:
            where.append("score >= ?")
            params.append(min_score)
        if max_score is not None:
            where.append("score <= ?")
            params.append(max_score)
        if flag:
            where.append("metrics LIKE ?")
            params.append(f'%"{flag}"%')
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        order = SORT_FIELDS.get(sort, SORT_FIELDS["score"])
        tail = f"LIMIT {int(limit)}" if limit > 0 else ""
        query = f"SELECT * FROM photos {clause} ORDER BY {order} {tail}"
        for row in self.connection.execute(query, params):
            yield row_to_photo(row)

    def photos(self, **kwargs: Any) -> list[Photo]:
        return list(self.iter_photos(**kwargs))

    def count(self, only_ok: bool = True) -> int:
        clause = "WHERE status = 'ok'" if only_ok else ""
        row = self.connection.execute(
            f"SELECT COUNT(*) AS n FROM photos {clause}"
        ).fetchone()
        return int(row["n"]) if row else 0

    def statistics(self) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT COUNT(*) AS total, "
            "       COALESCE(SUM(size), 0) AS bytes, "
            "       COALESCE(AVG(score), 0) AS mean_score, "
            "       COALESCE(SUM(width * height), 0) AS pixels "
            "FROM photos WHERE status = 'ok'"
        ).fetchone()
        errors = self.connection.execute(
            "SELECT COUNT(*) AS n FROM photos WHERE status != 'ok'"
        ).fetchone()
        marked = self.connection.execute(
            "SELECT mark, COUNT(*) AS n FROM photos GROUP BY mark"
        ).fetchall()
        return {
            "total": int(row["total"]),
            "bytes": int(row["bytes"]),
            "mean_score": float(row["mean_score"]),
            "megapixels": float(row["pixels"]) / 1_000_000.0,
            "errors": int(errors["n"]) if errors else 0,
            "marks": {r["mark"]: int(r["n"]) for r in marked},
        }

    def score_percentiles(self) -> list[float]:
        """Sorted scores, so the UI can show "better than 82% of your library"."""
        return [
            float(row["score"])
            for row in self.connection.execute(
                "SELECT score FROM photos WHERE status = 'ok' ORDER BY score ASC"
            )
        ]
