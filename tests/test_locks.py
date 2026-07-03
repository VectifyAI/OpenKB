"""Tests for OpenKB KB locks and atomic writes."""

from __future__ import annotations

import json
import stat
import threading

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
