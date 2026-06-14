"""Cooperative filesystem locks and atomic writes for OpenKB.

The lock protocol is advisory and intended for local filesystem access by
OpenKB processes. It does not guarantee cross-host coordination on networked
or synced filesystems where ``fcntl.flock`` may be unavailable or inconsistent.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Iterator

_LOCKS_GUARD = threading.Lock()
_LOCAL_LOCKS: dict[Path, "_LocalRwLock"] = {}
_HELD_LOCKS = threading.local()


class _LocalRwLock:
    def __init__(self) -> None:
        self._condition = threading.Condition(threading.Lock())
        self._readers = 0
        self._writer = False

    @contextlib.contextmanager
    def read(self) -> Iterator[None]:
        with self._condition:
            while self._writer:
                self._condition.wait()
            self._readers += 1
        try:
            yield
        finally:
            with self._condition:
                self._readers -= 1
                if self._readers == 0:
                    self._condition.notify_all()

    @contextlib.contextmanager
    def write(self) -> Iterator[None]:
        with self._condition:
            while self._writer or self._readers:
                self._condition.wait()
            self._writer = True
        try:
            yield
        finally:
            with self._condition:
                self._writer = False
                self._condition.notify_all()


def _held_locks() -> dict[Path, tuple[int, int]]:
    held = getattr(_HELD_LOCKS, "counts", None)
    if held is None:
        held = {}
        _HELD_LOCKS.counts = held
    return held


def _local_lock(lock_path: Path) -> _LocalRwLock:
    resolved = lock_path.resolve()
    with _LOCKS_GUARD:
        lock = _LOCAL_LOCKS.get(resolved)
        if lock is None:
            lock = _LocalRwLock()
            _LOCAL_LOCKS[resolved] = lock
        return lock


@contextlib.contextmanager
def kb_lock(openkb_dir: Path, *, exclusive: bool) -> Iterator[None]:
    """Hold a KB-level advisory lock."""
    lock_path = openkb_dir / "ingest.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    resolved = lock_path.resolve()
    held = _held_locks()
    exclusive_depth, shared_depth = held.get(resolved, (0, 0))

    if exclusive_depth or shared_depth:
        if exclusive and not exclusive_depth:
            raise RuntimeError("Cannot upgrade an existing KB read lock to a write lock")
        held[resolved] = (
            exclusive_depth + (1 if exclusive else 0),
            shared_depth + (0 if exclusive else 1),
        )
        try:
            yield
        finally:
            current_exclusive, current_shared = held[resolved]
            next_counts = (
                current_exclusive - (1 if exclusive else 0),
                current_shared - (0 if exclusive else 1),
            )
            if next_counts == (0, 0):
                del held[resolved]
            else:
                held[resolved] = next_counts
        return

    local_lock = _local_lock(lock_path)
    local_context = local_lock.write() if exclusive else local_lock.read()
    with local_context:
        with lock_path.open("a+", encoding="utf-8") as fh:
            mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            fcntl.flock(fh.fileno(), mode)
            held[resolved] = (1, 0) if exclusive else (0, 1)
            try:
                yield
            finally:
                held.pop(resolved, None)
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def kb_ingest_lock(openkb_dir: Path):
    """Hold an exclusive KB mutation lock."""
    return kb_lock(openkb_dir, exclusive=True)


def kb_read_lock(openkb_dir: Path):
    """Hold a shared KB read lock."""
    return kb_lock(openkb_dir, exclusive=False)


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _default_file_mode() -> int:
    current_umask = os.umask(0)
    os.umask(current_umask)
    return 0o666 & ~current_umask


def _target_mode(path: Path) -> int:
    try:
        return path.stat().st_mode & 0o777
    except FileNotFoundError:
        return _default_file_mode()


def atomic_write_bytes(path: Path, content: bytes) -> None:
    """Atomically replace *path* with binary *content*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            os.fchmod(fh.fileno(), _target_mode(path))
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
        _fsync_directory(path.parent)
    finally:
        tmp_path.unlink(missing_ok=True)


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Atomically replace *path* with text *content*."""
    atomic_write_bytes(path, content.encode(encoding))


def atomic_write_json(
    path: Path,
    data: object,
    *,
    ensure_ascii: bool = True,
    default=None,
) -> None:
    """Atomically replace *path* with formatted JSON."""
    atomic_write_text(
        path,
        json.dumps(data, indent=2, ensure_ascii=ensure_ascii, default=default) + "\n",
    )
