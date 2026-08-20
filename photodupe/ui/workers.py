"""Background work, kept strictly off the GUI thread.

Scanning tens of thousands of photos takes minutes; doing any of it on the GUI
thread would freeze the window. Every long operation therefore runs in a
:class:`Worker` thread and reports back through signals, which Qt delivers to
the GUI thread safely.
"""

from __future__ import annotations

import logging
import traceback
from typing import Any, Callable

from PySide6 import QtCore

log = logging.getLogger(__name__)


class Worker(QtCore.QThread):
    """Runs one callable on a background thread with progress and cancellation.

    The callable is handed a ``progress(done, total, message) -> bool`` function
    and must stop when it returns ``False``. That single convention is what
    makes Cancel work everywhere in the app.
    """

    progressed = QtCore.Signal(int, int, str)
    succeeded = QtCore.Signal(object)
    failed = QtCore.Signal(str)
    done = QtCore.Signal()

    def __init__(
        self,
        function: Callable[..., Any],
        *args: Any,
        parent: QtCore.QObject | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(parent)
        self._function = function
        self._args = args
        self._kwargs = kwargs
        self._cancelled = False

    # ------------------------------------------------------------------
    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    # ------------------------------------------------------------------
    def _progress(self, done: int, total: int, message: str = "") -> bool:
        self.progressed.emit(int(done), int(total), str(message))
        return not self._cancelled

    def run(self) -> None:  # noqa: D102 - Qt entry point
        try:
            result = self._function(*self._args, progress=self._progress, **self._kwargs)
        except Exception as exc:  # noqa: BLE001 - report, never crash the app
            log.error("worker failed: %s\n%s", exc, traceback.format_exc())
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(result)
        finally:
            self.done.emit()


class ThumbnailLoader(QtCore.QObject):
    """Loads thumbnails off the GUI thread and caches the resulting pixmaps.

    A grid of a few thousand photos would stutter badly if every cell decoded
    its own JPEG during painting. Instead the delegate asks for a pixmap, gets
    a placeholder if it is not ready, and the view repaints that one cell when
    the real image arrives.
    """

    loaded = QtCore.Signal(int, QtCore.QByteArray)

    def __init__(self, max_workers: int = 4, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QtCore.QThreadPool(self)
        self._pool.setMaxThreadCount(max(1, max_workers))
        self._pending: set[int] = set()
        self._lock = QtCore.QMutex()

    def request(self, photo_id: int, path: str, size: int) -> bool:
        """Queue a thumbnail load. Returns ``False`` if it is already queued."""
        with QtCore.QMutexLocker(self._lock):
            if photo_id in self._pending:
                return False
            self._pending.add(photo_id)
        self._pool.start(_ThumbnailTask(self, photo_id, path, size))
        return True

    def _finish(self, photo_id: int, data: QtCore.QByteArray) -> None:
        with QtCore.QMutexLocker(self._lock):
            self._pending.discard(photo_id)
        self.loaded.emit(photo_id, data)

    def clear(self) -> None:
        self._pool.clear()
        with QtCore.QMutexLocker(self._lock):
            self._pending.clear()

    def shutdown(self) -> None:
        self._pool.clear()
        self._pool.waitForDone(3000)


class _ThumbnailTask(QtCore.QRunnable):
    """Reads image bytes on a pool thread.

    Only the *bytes* are read here; the QPixmap itself is built on the GUI
    thread, because QPixmap is not safe to construct off it.
    """

    def __init__(self, loader: ThumbnailLoader, photo_id: int, path: str, size: int) -> None:
        super().__init__()
        self._loader = loader
        self._photo_id = photo_id
        self._path = path
        self._size = size
        self.setAutoDelete(True)

    def run(self) -> None:  # noqa: D102 - Qt entry point
        data = QtCore.QByteArray()
        try:
            from pathlib import Path

            from ..config import thumbnail_dir
            from ..imaging import thumbnail_key, write_thumbnail

            source = Path(self._path)
            if source.exists():
                mtime = source.stat().st_mtime
                cached = thumbnail_dir() / f"{thumbnail_key(source, self._size, mtime)}.jpg"
                if not cached.exists():
                    write_thumbnail(source, cached, self._size)
                data = QtCore.QByteArray(cached.read_bytes())
        except Exception as exc:  # noqa: BLE001 - a broken thumbnail is not fatal
            log.debug("thumbnail load failed for %s: %s", self._path, exc)
        finally:
            self._loader._finish(self._photo_id, data)
