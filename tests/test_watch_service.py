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
    WatcherState,
    WatchRegistry,
    _public_event,
    _record_event,
)


def _fake_add_ok(path: Path, kb_dir: Path, bundle=None) -> AddFileResult:
    return AddFileResult("x", str(path), "added", "ok")


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
        lambda path, kb, bundle=None: AddFileResult(path.name, str(path), "added", "Added."),
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

    def fake_add(path, target_kb, bundle=None):
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
        lambda path, kb, bundle=None: called.append(path) or AddFileResult("x", None, "added", "x"),
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

    def fake_add(path, target_kb, bundle=None):
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
        lambda path, kb, bundle=None: seen.append(path.name)
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


def test_record_event_appends_inside_lock():
    """_record_event must append inside the lock so watcher_stopped ordering is preserved."""
    from openkb.watch_service import WatcherState, _record_event

    state = WatcherState(kb="t", kb_dir=None, raw_dir=None, debounce=0, started_at=0.0)
    _record_event(state, "file_done", {"path": "a.md"})
    _record_event(state, "watcher_stopped", {"kb": "t"})
    events = list(state.events)
    assert events[-1]["event"] == "watcher_stopped"
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs)


def test_stop_then_start_does_not_double_worker(kb_dir, monkeypatch):
    """After stop(), start() must not spawn a second worker."""
    monkeypatch.setattr("openkb.watch_service._add_for_api", _fake_add_ok)
    reg = WatchRegistry()
    reg.start("t", kb_dir, debounce=0.1)
    reg.stop("t")
    state2 = reg.start("t", kb_dir, debounce=0.1)
    assert state2.worker_thread is not None
    assert state2.worker_thread.is_alive()
    assert len(reg.list_active()) == 1
    reg.stop("t")


def test_start_returns_existing_if_running(kb_dir, monkeypatch):
    """start() is idempotent."""
    monkeypatch.setattr("openkb.watch_service._add_for_api", _fake_add_ok)
    reg = WatchRegistry()
    s1 = reg.start("t", kb_dir, debounce=0.1)
    s2 = reg.start("t", kb_dir, debounce=0.1)
    assert s1 is s2
    reg.stop("t")


def test_worker_exception_clears_running(kb_dir, monkeypatch):
    """When the worker exits (via stop sentinel), running must be cleared."""

    monkeypatch.setattr(
        "openkb.watch_service._add_for_api",
        lambda path, kb_dir, bundle=None: AddFileResult("x", str(path), "added", "ok"),
    )
    reg = WatchRegistry()
    state = reg.start("t", kb_dir, debounce=0.1)
    assert state.running.is_set()
    # stop() joins the worker; after it returns, running should be cleared.
    reg.stop("t")
    assert not state.running.is_set()


def test_record_event_seq_monotonic_under_burst():
    """watcher_stopped terminator must have a higher seq than preceding events."""
    from collections import deque

    from openkb.watch_service import WatcherState, _record_event

    state = WatcherState(kb="t", kb_dir=None, raw_dir=None, debounce=0, started_at=0.0, events=None)
    state.events = deque(maxlen=3)
    for i in range(5):
        _record_event(state, "file_done", {"i": i})
    _record_event(state, "watcher_stopped", {"kb": "t"})
    events = list(state.events)
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs)
    assert events[-1]["event"] == "watcher_stopped"


def test_stop_draining_when_worker_stuck(kb_dir, monkeypatch):
    """When the worker does not exit within the join timeout, stop() must leave
    a draining marker and keep the state registered so start() refuses to
    spawn a duplicate worker."""
    import threading as _t

    block = _t.Event()

    def slow_process(state, raw_path):
        block.wait(timeout=10)

    monkeypatch.setattr("openkb.watch_service._process_file", slow_process)

    reg = WatchRegistry()
    state = reg.start("t", kb_dir, debounce=0.1)
    # Put a batch so the worker enters _process_file and blocks.
    state.queue.put(["fake.md"])
    time.sleep(0.3)  # let the worker pick it up and block

    # Replace join with a no-op so the test does not wait 5 s.
    real_join = state.worker_thread.join
    state.worker_thread.join = lambda timeout=None: None

    result = reg.stop("t")
    assert result is True
    # State must still be registered (not popped) and running still set.
    assert reg.get("t") is state
    assert state.running.is_set()
    events = list(state.events)
    assert any(e["event"] == "watcher_draining" for e in events)

    # Cleanup: release the worker so it can exit.
    block.set()
    real_join(timeout=5)
