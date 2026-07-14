"""Tests for openkb.watch_service (per-KB watcher registry + worker).

All ingest is mocked so no real LLM/compilation runs, per AGENTS.md.
Worker behavior is driven by putting batches directly on the queue to avoid
OS-watcher timing flakiness; one end-to-end test exercises the real debounce
pipeline with a small debounce.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from pathlib import Path

from openkb.cli import AddFileResult
from openkb.watch_service import (
    WatchRegistry,
    WatcherState,
    _public_event,
    _record_event,
)


def _drain_worker(state: WatcherState, timeout: float = 2.0) -> None:
    """Run the worker loop on already-queued batches, then stop it."""
    from openkb.watch_service import _run_worker

    state.queue.put(None)
    t = threading.Thread(target=_run_worker, args=(state,))
    t.start()
    t.join(timeout=timeout)
    assert not t.is_alive(), "worker did not drain in time"


def _events_of(state: WatcherState, name: str) -> list[dict]:
    return [e["data"] for e in state.events if e["event"] == name]


def _make_state(kb_dir: Path, kb: str = "test-kb") -> WatcherState:
    return WatcherState(
        kb=kb,
        kb_dir=kb_dir,
        raw_dir=kb_dir / "raw",
        debounce=0.0,
        started_at=time.time(),
    )


# registry lifecycle


def test_start_is_idempotent(kb_dir, monkeypatch):
    monkeypatch.setattr("openkb.watch_service.start_watch", lambda *a, **k: object())
    reg = WatchRegistry()
    s1 = reg.start("test-kb", kb_dir, debounce=0.0)
    s2 = reg.start("test-kb", kb_dir, debounce=0.0)
    assert s1 is s2
    assert reg.list_active() == ["test-kb"]
    reg.stop("test-kb")


def test_stop_returns_false_when_not_active():
    reg = WatchRegistry()
    assert reg.stop("nope") is False


def test_status_inactive_returns_active_false():
    reg = WatchRegistry()
    assert reg.status("missing") == {"kb": "missing", "active": False}


def test_status_active_fields_and_recent_events(kb_dir, monkeypatch):
    monkeypatch.setattr("openkb.watch_service.start_watch", lambda *a, **k: object())
    reg = WatchRegistry()
    state = reg.start("test-kb", kb_dir, debounce=0.0)
    _record_event(state, "file_done", {"original_name": "a.md", "status": "added"})
    st = reg.status("test-kb")
    assert st["active"] is True
    assert st["kb"] == "test-kb"
    assert st["raw_dir"] == str(kb_dir / "raw")
    assert st["counters"] == {"added": 0, "skipped": 0, "failed": 0}
    assert len(st["recent_events"]) == 1
    assert st["recent_events"][0]["event"] == "file_done"
    assert "seq" not in st["recent_events"][0]
    reg.stop("test-kb")


def test_stop_all_clears_registry(kb_dir, monkeypatch):
    monkeypatch.setattr("openkb.watch_service.start_watch", lambda *a, **k: object())
    reg = WatchRegistry()
    reg.start("a", kb_dir, debounce=0.0)
    reg.start("b", kb_dir, debounce=0.0)
    reg.stop_all()
    assert reg.list_active() == []


# worker file processing


def test_worker_added_records_file_start_done_and_counter(kb_dir, monkeypatch):
    state = _make_state(kb_dir)
    state.queue.put([str(kb_dir / "raw" / "paper.md")])
    monkeypatch.setattr(
        "openkb.watch_service._add_for_api",
        lambda path, kb: AddFileResult(path.name, str(path), "added", "Added."),
    )
    _drain_worker(state)
    assert _events_of(state, "file_start")[0]["original_name"] == "paper.md"
    done = _events_of(state, "file_done")[0]
    assert done["status"] == "added"
    assert done["message"] == "Added."
    assert state.counters == {"added": 1, "skipped": 0, "failed": 0}


def test_worker_skipped_and_failed_branches(kb_dir, monkeypatch):
    state = _make_state(kb_dir)
    state.queue.put([str(kb_dir / "raw" / "dup.md"), str(kb_dir / "raw" / "boom.md")])

    def fake_add(path, target_kb):
        if path.name == "dup.md":
            return AddFileResult(path.name, None, "skipped", "Already in KB.")
        raise RuntimeError("explode")

    monkeypatch.setattr("openkb.watch_service._add_for_api", fake_add)
    _drain_worker(state)
    dones = _events_of(state, "file_done")
    assert [d["status"] for d in dones] == ["skipped"]
    assert dones[0]["message"] == "Already in KB."
    errs = _events_of(state, "error")
    assert len(errs) == 1
    assert "explode" in errs[0]["message"]
    assert state.counters == {"added": 0, "skipped": 1, "failed": 1}


def test_worker_unsupported_suffix_is_skipped_without_ingest(kb_dir, monkeypatch):
    state = _make_state(kb_dir)
    state.queue.put([str(kb_dir / "raw" / "image.xyz")])
    called = []
    monkeypatch.setattr(
        "openkb.watch_service._add_for_api",
        lambda path, kb: called.append(path) or AddFileResult("x", None, "added", "x"),
    )
    _drain_worker(state)
    assert called == []
    done = _events_of(state, "file_done")[0]
    assert done["status"] == "skipped"
    assert "unsupported" in done["message"].lower()
    assert state.counters == {"added": 0, "skipped": 1, "failed": 0}


def test_worker_does_not_die_on_exception(kb_dir, monkeypatch):
    state = _make_state(kb_dir)
    state.queue.put([str(kb_dir / "raw" / "bad.md"), str(kb_dir / "raw" / "good.md")])

    def fake_add(path, target_kb):
        if path.name == "bad.md":
            raise RuntimeError("nope")
        return AddFileResult(path.name, str(path), "added", "ok")

    monkeypatch.setattr("openkb.watch_service._add_for_api", fake_add)
    _drain_worker(state)
    assert state.counters == {"added": 1, "skipped": 0, "failed": 1}


def test_ring_buffer_keeps_most_recent_ordered():
    buf = deque(maxlen=3)
    for i in range(5):
        buf.append({"seq": i, "ts": float(i), "event": "file_done", "data": {"i": i}})
    public = [_public_event(e) for e in buf]
    assert [p["data"]["i"] for p in public] == [2, 3, 4]
    assert public[0]["ts"] < public[-1]["ts"]


def test_end_to_end_debounce_processes_real_file(kb_dir, monkeypatch):
    seen = []
    monkeypatch.setattr(
        "openkb.watch_service._add_for_api",
        lambda path, kb: seen.append(path.name)
        or AddFileResult(path.name, str(path), "added", "ok"),
    )
    reg = WatchRegistry()
    reg.start("test-kb", kb_dir, debounce=0.1)
    try:
        (kb_dir / "raw" / "dropped.md").write_text("# hi", encoding="utf-8")
        for _ in range(50):
            if reg.status("test-kb")["counters"]["added"] == 1:
                break
            time.sleep(0.1)
        assert seen == ["dropped.md"]
        names = [e["event"] for e in reg.status("test-kb")["recent_events"]]
        assert "file_start" in names and "file_done" in names
    finally:
        reg.stop("test-kb")