"""Cross-platform behaviour for openkb.locks / openkb.config.

The locking layer (#86) originally hard-imported ``fcntl`` and called
``os.fchmod`` / directory ``os.fsync`` unconditionally — all Unix-only — which
crashed OpenKB at import time on Windows (``ModuleNotFoundError: No module
named 'fcntl'``, reported in VectifyAI/OpenKB#93). These tests pin the
platform-neutral behaviour and simulate the Windows path on this host.
"""
from __future__ import annotations

import os
import subprocess
import sys
import types

import pytest

from openkb import locks


def test_config_and_locks_import_without_fcntl():
    """openkb.config / openkb.locks must import on a host without fcntl (Windows)."""
    code = (
        "import sys\n"
        "sys.modules['fcntl'] = None\n"  # make `import fcntl` raise ImportError
        "import openkb.locks, openkb.config\n"
        "assert openkb.locks.fcntl is None\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_flock_funlock_roundtrip(tmp_path):
    """flock/funlock acquire and release an advisory lock on the real platform."""
    lock_path = tmp_path / "test.lock"
    with lock_path.open("a+", encoding="utf-8") as fh:
        locks.flock(fh, exclusive=True)
        locks.funlock(fh)  # must not raise


def test_flock_uses_msvcrt_when_fcntl_absent(monkeypatch, tmp_path):
    """When fcntl is unavailable (Windows), locking is delegated to msvcrt."""
    calls = []
    fake_msvcrt = types.SimpleNamespace(
        LK_LOCK=1, LK_NBLCK=2, LK_UNLCK=0,
        locking=lambda fd, mode, nbytes: calls.append((mode, nbytes)),
    )
    monkeypatch.setattr(locks, "fcntl", None)
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)

    lock_path = tmp_path / "test.lock"
    with lock_path.open("a+", encoding="utf-8") as fh:
        locks.flock(fh, exclusive=True)
        locks.funlock(fh)

    modes = [mode for mode, _ in calls]
    assert fake_msvcrt.LK_NBLCK in modes  # acquire used the non-blocking lock
    assert fake_msvcrt.LK_UNLCK in modes  # release unlocked


def test_flock_retries_until_lock_available(monkeypatch, tmp_path):
    """The Windows fallback retries the non-blocking lock until it succeeds."""
    attempts = {"n": 0}

    def fake_locking(fd, mode, nbytes):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise OSError("locked")  # contention on the first two tries

    fake_msvcrt = types.SimpleNamespace(
        LK_LOCK=1, LK_NBLCK=2, LK_UNLCK=0, locking=fake_locking
    )
    monkeypatch.setattr(locks, "fcntl", None)
    monkeypatch.setattr(locks, "_WINDOWS_LOCK_TIMEOUT", 5.0)
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)

    with (tmp_path / "test.lock").open("a+", encoding="utf-8") as fh:
        locks.flock(fh, exclusive=True)

    assert attempts["n"] == 3  # retried twice, succeeded on the third


def test_flock_raises_after_timeout(monkeypatch, tmp_path):
    """A never-released Windows lock surfaces an error instead of hanging forever."""
    def always_locked(fd, mode, nbytes):
        raise OSError("locked")

    fake_msvcrt = types.SimpleNamespace(
        LK_LOCK=1, LK_NBLCK=2, LK_UNLCK=0, locking=always_locked
    )
    monkeypatch.setattr(locks, "fcntl", None)
    monkeypatch.setattr(locks, "_WINDOWS_LOCK_TIMEOUT", 0.2)
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)

    with (tmp_path / "test.lock").open("a+", encoding="utf-8") as fh:
        with pytest.raises(OSError):
            locks.flock(fh, exclusive=True)


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
