"""Tests for OpenKB KB locks and atomic writes."""

from __future__ import annotations

import json
import os
import stat
import threading
from pathlib import Path

import pytest

from openkb.locks import (
    atomic_write_json,
    atomic_write_text,
    kb_ingest_lock,
    kb_ingest_lock_held,
    kb_read_lock,
)


def test_write_lock_is_reentrant(tmp_path):
    openkb_dir = tmp_path / ".openkb"

    with kb_ingest_lock(openkb_dir):
        with kb_ingest_lock(openkb_dir):
            assert (openkb_dir / "ingest.lock").exists()


def test_read_lock_is_reentrant(tmp_path):
    openkb_dir = tmp_path / ".openkb"

    with kb_read_lock(openkb_dir):
        with kb_read_lock(openkb_dir):
            assert (openkb_dir / "ingest.lock").exists()


def test_read_locks_do_not_block_each_other_in_process(tmp_path):
    openkb_dir = tmp_path / ".openkb"
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()

    def first_reader():
        with kb_read_lock(openkb_dir):
            first_entered.set()
            assert release_first.wait(timeout=2)

    def second_reader():
        assert first_entered.wait(timeout=2)
        with kb_read_lock(openkb_dir):
            second_entered.set()

    first = threading.Thread(target=first_reader)
    second = threading.Thread(target=second_reader)
    first.start()
    second.start()
    assert second_entered.wait(timeout=2)
    release_first.set()
    first.join(timeout=2)
    second.join(timeout=2)
    assert not first.is_alive()
    assert not second.is_alive()


def test_read_to_write_upgrade_fails(tmp_path):
    openkb_dir = tmp_path / ".openkb"

    with kb_read_lock(openkb_dir):
        with pytest.raises(RuntimeError, match="Cannot upgrade"):
            with kb_ingest_lock(openkb_dir):
                pass


def test_write_lock_can_take_nested_read(tmp_path):
    openkb_dir = tmp_path / ".openkb"

    with kb_ingest_lock(openkb_dir):
        with kb_read_lock(openkb_dir):
            assert (openkb_dir / "ingest.lock").exists()


def test_kb_ingest_lock_held_is_exclusive_and_thread_local(tmp_path):
    openkb_dir = tmp_path / ".openkb"
    worker_seen = []

    assert not kb_ingest_lock_held(openkb_dir)

    with kb_read_lock(openkb_dir):
        assert not kb_ingest_lock_held(openkb_dir)

    with kb_ingest_lock(openkb_dir):
        assert kb_ingest_lock_held(openkb_dir)

        worker = threading.Thread(
            target=lambda: worker_seen.append(kb_ingest_lock_held(openkb_dir))
        )
        worker.start()
        worker.join(timeout=2)

    assert not worker.is_alive()
    assert worker_seen == [False]
    assert not kb_ingest_lock_held(openkb_dir)


def test_atomic_write_text_replaces_file(tmp_path):
    target = tmp_path / "nested" / "file.txt"
    atomic_write_text(target, "first")
    atomic_write_text(target, "second")

    assert target.read_text(encoding="utf-8") == "second"
    assert list(target.parent.glob("*.tmp")) == []


def test_atomic_write_text_preserves_existing_mode(tmp_path):
    target = tmp_path / "file.txt"
    target.write_text("first", encoding="utf-8")
    target.chmod(0o640)

    atomic_write_text(target, "second")

    assert target.read_text(encoding="utf-8") == "second"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640


def test_atomic_write_json_replaces_file(tmp_path):
    target = tmp_path / "hashes.json"

    atomic_write_json(target, {"a": {"name": "doc.pdf"}}, ensure_ascii=False)

    assert json.loads(target.read_text(encoding="utf-8")) == {"a": {"name": "doc.pdf"}}


def test_kb_ingest_lock_held_is_per_thread(tmp_path):
    from openkb.locks import kb_ingest_lock_held

    openkb_dir = tmp_path / ".openkb"
    assert kb_ingest_lock_held(openkb_dir) is False

    holder_seen = {}
    worker_seen = {}

    def worker():
        worker_seen["held"] = kb_ingest_lock_held(openkb_dir)

    with kb_ingest_lock(openkb_dir):
        holder_seen["held"] = kb_ingest_lock_held(openkb_dir)
        t = threading.Thread(target=worker)
        t.start()
        t.join()

    assert holder_seen["held"] is True
    # Per-thread (threading.local): a worker does not see the main thread's lock.
    assert worker_seen["held"] is False
    assert kb_ingest_lock_held(openkb_dir) is False


def test_first_exclusive_lock_reaps_orphaned_prepare_staging(tmp_path):
    openkb_dir = tmp_path / ".openkb"
    prepare_root = openkb_dir / "staging" / "prepare"
    prepare_root.mkdir(parents=True)
    orphan = prepare_root / "000001-doc-abcdef12"
    orphan.mkdir()
    (orphan / "artifact.md").write_text("orphan", encoding="utf-8")

    # A non-prepare staging dir must NOT be touched (reaper is scoped to prepare/).
    other = openkb_dir / "staging" / "rollback-deadbeef"
    other.mkdir(parents=True)
    (other / "backup").write_text("keep", encoding="utf-8")

    with kb_ingest_lock(openkb_dir):
        pass

    assert not orphan.exists()
    assert not any(prepare_root.iterdir())
    assert other.exists()  # scope: only staging/prepare/ is reaped


def test_prepare_staging_created_under_lock_survives_then_is_reaped_next_acquire(tmp_path):
    openkb_dir = tmp_path / ".openkb"
    prepare_root = openkb_dir / "staging" / "prepare"

    with kb_ingest_lock(openkb_dir):
        prepare_root.mkdir(parents=True)
        live = prepare_root / "000002-note-12345678"
        live.mkdir()
        (live / "x.md").write_text("live", encoding="utf-8")
        # Reaper does not run on the reentrant acquire a commit would make, and
        # it already ran before this staging was created, so it survives the lock.
        assert live.exists()

    # Released and re-acquired: the staging from the prior hold is now an orphan
    # and is reaped at this new 0->1 acquisition.
    with kb_ingest_lock(openkb_dir):
        assert not live.exists()


def test_reaper_does_not_follow_symlink_in_prepare_staging(tmp_path):
    openkb_dir = tmp_path / ".openkb"
    prepare_root = openkb_dir / "staging" / "prepare"
    prepare_root.mkdir(parents=True)
    # A symlink inside prepare/ must never be followed by rmtree (its target
    # must be left intact).
    target = tmp_path / "secret"
    target.mkdir()
    (target / "keep.txt").write_text("do-not-delete", encoding="utf-8")
    link = prepare_root / "000004-link-dead0000"
    link.symlink_to(target, target_is_directory=True)

    with kb_ingest_lock(openkb_dir):
        pass

    assert (target / "keep.txt").exists()


def test_reaper_survives_denied_unlink_of_loose_file_in_prepare_staging(tmp_path, monkeypatch):
    """A loose file (not a dir) in staging/prepare/ whose unlink is denied must
    not escape kb_lock and stall the whole KB.

    On Windows an AV/indexer holding such a file makes os.unlink raise
    PermissionError; ``missing_ok=True`` only swallows FileNotFoundError, so the
    error currently propagates out of every exclusive-lock acquisition
    (add/remove/recompile/chat). The directory branch uses rmtree and is safe;
    this guards the asymmetric file branch.
    """
    openkb_dir = tmp_path / ".openkb"
    prepare_root = openkb_dir / "staging" / "prepare"
    prepare_root.mkdir(parents=True)
    loose = prepare_root / "stray.dat"
    loose.write_text("x", encoding="utf-8")

    real_unlink = Path.unlink

    def deny_unlink(self, *args, **kwargs):
        if Path(self) == loose:
            raise PermissionError(13, "Access is denied", str(self))
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_unlink)

    # Must not raise: the reap is best-effort and must not stall the lock holder.
    with kb_ingest_lock(openkb_dir):
        pass


def test_reaper_self_heals_readonly_dir_under_prepare_staging(tmp_path, monkeypatch):
    """A read-only file inside an orphaned prepare dir must be reaped, not left
    behind forever.

    shutil.copy2 preserves a read-only source's attribute into staging; on
    Windows os.unlink denies a read-only file and rmtree(ignore_errors=True)
    leaves the tree behind, so the orphan resurfaces as "Could not fully reap"
    on every lock acquisition and never self-heals. POSIX deletes read-only
    files fine, so we simulate the Windows denial: deny once, then let the
    retry (after the handler clears the read-only bit) succeed.
    """
    openkb_dir = tmp_path / ".openkb"
    prepare_root = openkb_dir / "staging" / "prepare"
    prepare_root.mkdir(parents=True)
    orphan = prepare_root / "000005-doc-11223344"
    orphan.mkdir()
    readonly = orphan / "readonly.md"
    readonly.write_text("locked", encoding="utf-8")

    real_unlink = os.unlink
    attempts = {"n": 0}

    def deny_once_then_succeed(*args, **kwargs):
        # shutil's POSIX fast path calls os.unlink(entry_name, dir_fd=topfd) — a
        # relative name — so identify the read-only file by basename. Deny the
        # first attempt (Windows Access-denied on a read-only file), then let the
        # handler's retry (after it clears the read-only bit) succeed.
        name = args[0] if args else kwargs.get("path")
        if Path(str(name)).name == readonly.name:
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise PermissionError(13, "Access is denied", str(name))
        return real_unlink(*args, **kwargs)

    # shutil.rmtree calls os.unlink internally; patching the shared os module
    # makes its first attempt on the read-only file raise (Windows behaviour).
    monkeypatch.setattr(os, "unlink", deny_once_then_succeed)

    with kb_ingest_lock(openkb_dir):
        pass

    assert not orphan.exists()  # fully reaped, no residue to warn about forever
    assert attempts["n"] == 2  # denied once, recovered on the retry
