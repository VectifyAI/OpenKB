"""Cross-platform behaviour for openkb.locks / openkb.config.

File locking is delegated to :mod:`portalocker` (fcntl on POSIX, msvcrt/Win32
on Windows), so OpenKB no longer hard-imports the Unix-only ``fcntl``. The
atomic-write path still special-cases the Unix-only ``os.fchmod`` and directory
``os.fsync``. These tests pin the platform-neutral behaviour verifiable on
POSIX; portalocker carries its own Windows test coverage.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

from openkb import locks


def _module_level_imports_fcntl(path: Path) -> bool:
    """True if the module has a top-level ``import fcntl`` / ``from fcntl import``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:  # module-level statements only (import-time crash risk)
        if isinstance(node, ast.Import) and any(a.name == "fcntl" for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.module == "fcntl":
            return True
    return False


def test_openkb_modules_do_not_hard_import_fcntl():
    """Guards issue #93: OpenKB's own modules must import on Windows (no bare fcntl)."""
    pkg_dir = Path(locks.__file__).parent  # locks.py lives in the openkb package
    offenders = [
        str(py.relative_to(pkg_dir))
        for py in pkg_dir.rglob("*.py")
        if _module_level_imports_fcntl(py)
    ]
    assert not offenders, f"Unix-only fcntl hard-imported at module level in: {offenders}"


def test_flock_funlock_roundtrip(tmp_path):
    """flock/funlock acquire and release both exclusive and shared locks."""
    lock_path = tmp_path / "test.lock"
    with lock_path.open("a+", encoding="utf-8") as fh:
        locks.flock(fh, exclusive=True)
        locks.funlock(fh)
        locks.flock(fh, exclusive=False)
        locks.funlock(fh)  # must not raise


def test_flock_exclusive_excludes_other_process(tmp_path):
    """An exclusive flock is a real OS lock: it excludes another process while
    held, and the lock is acquirable again once released."""
    lock_path = tmp_path / "test.lock"
    probe = (
        "import portalocker\n"
        f"fh = open({str(lock_path)!r}, 'a+')\n"
        "try:\n"
        "    portalocker.lock(fh, portalocker.LOCK_EX | portalocker.LOCK_NB)\n"
        "    print('ACQUIRED')\n"
        "except portalocker.LockException:\n"
        "    print('BLOCKED')\n"
    )

    def run_probe() -> str:
        result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr  # probe itself ran cleanly
        return result.stdout.strip()

    fh = lock_path.open("a+", encoding="utf-8")
    locks.flock(fh, exclusive=True)
    try:
        assert run_probe() == "BLOCKED"  # held → other process is excluded
    finally:
        locks.funlock(fh)
        fh.close()
    assert run_probe() == "ACQUIRED"  # released → other process can acquire


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
