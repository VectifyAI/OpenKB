"""Cross-platform behaviour for openkb.locks / openkb.config.

File locking is delegated to :mod:`portalocker` (fcntl on POSIX, msvcrt/Win32
on Windows), so OpenKB no longer hard-imports the Unix-only ``fcntl``. The
atomic-write path still special-cases the Unix-only ``os.fchmod`` and directory
``os.fsync``. These tests pin the platform-neutral behaviour that is verifiable
on POSIX; portalocker carries its own Windows test coverage.
"""
from __future__ import annotations

import os
import subprocess
import sys

import portalocker
import pytest

from openkb import locks


def test_flock_funlock_roundtrip(tmp_path):
    """flock/funlock acquire and release both exclusive and shared locks."""
    lock_path = tmp_path / "test.lock"
    with lock_path.open("a+", encoding="utf-8") as fh:
        locks.flock(fh, exclusive=True)
        locks.funlock(fh)
        locks.flock(fh, exclusive=False)
        locks.funlock(fh)  # must not raise


def test_flock_exclusive_blocks_other_process(tmp_path):
    """An exclusive flock is a real OS lock that excludes another process."""
    lock_path = tmp_path / "test.lock"
    fh = lock_path.open("a+", encoding="utf-8")
    locks.flock(fh, exclusive=True)
    try:
        probe = (
            "import portalocker\n"
            f"fh = open({str(lock_path)!r}, 'a+')\n"
            "try:\n"
            "    portalocker.lock(fh, portalocker.LOCK_EX | portalocker.LOCK_NB)\n"
            "    print('ACQUIRED')\n"
            "except portalocker.LockException:\n"
            "    print('BLOCKED')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True
        )
        assert "BLOCKED" in result.stdout, result.stdout + result.stderr
    finally:
        locks.funlock(fh)
        fh.close()


def test_atomic_write_bytes_without_fchmod(monkeypatch, tmp_path):
    """atomic_write_bytes must still work where os.fchmod is missing (Windows)."""
    monkeypatch.delattr(os, "fchmod", raising=False)
    target = tmp_path / "data.bin"
    locks.atomic_write_bytes(target, b"hello")
    assert target.read_bytes() == b"hello"


def test_fsync_directory_skipped_on_windows(monkeypatch, tmp_path):
    """Directory fsync (unsupported on Windows) must be skipped, not attempted."""
    monkeypatch.setattr(os, "name", "nt")

    def _no_open(*args, **kwargs):
        raise AssertionError("os.open must not be called for dir fsync on Windows")

    monkeypatch.setattr(os, "open", _no_open)
    locks._fsync_directory(tmp_path)  # must return without touching os.open
