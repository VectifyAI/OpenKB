"""Server-managed file watchers for the OpenKB REST API.

Each knowledge base may have at most one active watcher that observes its
``raw/`` directory and automatically ingests new/modified files through the
same locked pipeline used by the CLI ``add`` command and the REST ``/add``
endpoint (``openkb.cli._add_for_api``). The watcher runs entirely in
background threads; the FastAPI layer only starts/stops/reads state.

Design notes:
* One worker thread per KB consumes debounced file batches sequentially, so
  ingest is serialized per-KB (the global ingest lock would serialize it
  anyway) while different KBs proceed in parallel.
* Per-KB mutable state is held in :class:`WatcherState`; the registry guards
  only the KB->state map. Events live in a bounded ring buffer so a long-lived
  watcher never accumulates unbounded memory.
* ``_add_for_api`` and ``SUPPORTED_EXTENSIONS`` are imported into this module's
  namespace so tests can monkeypatch ``openkb.watch_service._add_for_api``.
"""

from __future__ import annotations

import os
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from watchdog.observers import Observer

from openkb.cli import SUPPORTED_EXTENSIONS, _add_for_api
from openkb.config import LlmCredentialBundle, resolve_credential_bundle
from openkb.watcher import start_watch

# How many recent events to retain per KB for status() and SSE replay.
_MAX_EVENTS = int(os.environ.get("OPENKB_WATCH_MAX_EVENTS", "200"))


@dataclass
class WatcherState:
    """Runtime state for a single KB's watcher."""

    kb: str
    kb_dir: Path
    raw_dir: Path
    debounce: float
    started_at: float
    observer: Observer | None = None
    worker_thread: threading.Thread | None = None
    queue: queue.Queue = field(default_factory=queue.Queue)
    running: threading.Event = field(default_factory=threading.Event)
    events: deque = field(default_factory=lambda: deque(maxlen=_MAX_EVENTS))
    counters: dict[str, int] = field(
        default_factory=lambda: {"added": 0, "skipped": 0, "failed": 0}
    )
    bundle: LlmCredentialBundle | None = None
    _seq: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        self.running.set()


def _record_event(state: WatcherState, event: str, data: dict[str, Any]) -> None:
    """Append a timestamped, sequenced event to the per-KB ring buffer."""
    with state._lock:
        state._seq += 1
        seq = state._seq
        state.events.append({"seq": seq, "ts": time.time(), "event": event, "data": data})


def _inc(state: WatcherState, key: str) -> None:
    with state._lock:
        state.counters[key] = state.counters.get(key, 0) + 1


def _public_event(ev: dict[str, Any]) -> dict[str, Any]:
    """Strip the internal ``seq`` before exposing an event over the API."""
    return {"ts": ev["ts"], "event": ev["event"], "data": ev.get("data", {})}


def _run_worker(state: WatcherState) -> None:
    """Consume debounced file batches and ingest each file.

    Exits when ``stop`` puts the ``None`` sentinel. Any per-file exception is
    recorded as an ``error`` event; the worker never propagates so one bad
    file can't kill the watcher.
    """
    try:
        while True:
            try:
                paths = state.queue.get()
            except Exception:
                return
            if paths is None:
                return
            for raw_path in paths:
                _process_file(state, raw_path)
    finally:
        # Ensure `running` reflects reality even on unexpected exit, so
        # status() reports inactive and start() can spawn a fresh worker.
        state.running.clear()


def _process_file(state: WatcherState, raw_path: str) -> None:
    path = Path(raw_path)
    suffix = path.suffix.lower()
    if not suffix or suffix not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        _record_event(
            state,
            "file_done",
            {
                "path": str(path),
                "original_name": path.name,
                "status": "skipped",
                "message": f"Skipping unsupported file type: {suffix}. Supported: {supported}",
            },
        )
        _inc(state, "skipped")
        return
    _record_event(
        state,
        "file_start",
        {
            "path": str(path),
            "original_name": path.name,
        },
    )
    try:
        result = _add_for_api(path, state.kb_dir, bundle=state.bundle)
    except Exception as exc:  # worker must never die
        _record_event(
            state,
            "error",
            {
                "path": str(path),
                "original_name": path.name,
                "message": f"Failed to add: {path.name}: {exc}",
            },
        )
        _inc(state, "failed")
        return
    status = result.status if result.status in ("added", "skipped", "failed") else "failed"
    _record_event(
        state,
        "file_done",
        {
            "path": str(path),
            "original_name": path.name,
            "status": status,
            "message": result.message,
        },
    )
    _inc(state, status)


class WatchRegistry:
    """Thread-safe registry of per-KB watchers (one app instance owns one)."""

    def __init__(self, max_events: int = _MAX_EVENTS) -> None:
        self._watchers: dict[str, WatcherState] = {}
        self._lock = threading.Lock()
        self._max_events = max_events

    def start(self, kb: str, kb_dir: Path, debounce: float = 2.0) -> WatcherState:
        """Start a watcher for *kb*, or return the existing one (idempotent).

        If a prior watcher state exists but its worker thread is no longer
        alive (the daemon died), it is purged and a fresh watcher starts.
        A still-running worker for *kb* is returned as-is.
        """
        with self._lock:
            existing = self._watchers.get(kb)
            if existing is not None:
                worker_alive = (
                    existing.worker_thread is not None and existing.worker_thread.is_alive()
                )
                if existing.running.is_set() and worker_alive:
                    return existing
                # Stale state from a dead worker: purge before starting fresh.
                self._watchers.pop(kb, None)
            raw_dir = kb_dir / "raw"
            raw_dir.mkdir(parents=True, exist_ok=True)
            # Resolve the KB's credentials once at watcher start so background
            # ingests use the same per-KB bundle as REST endpoints instead of
            # mutating process-wide env state.
            bundle = resolve_credential_bundle(kb_dir)
            state = WatcherState(
                kb=kb,
                kb_dir=kb_dir,
                raw_dir=raw_dir,
                debounce=debounce,
                bundle=bundle,
                started_at=time.time(),
                events=deque(maxlen=self._max_events),
            )

            def on_new_files(paths: list[str]) -> None:
                state.queue.put(paths)

            state.observer = start_watch(raw_dir, on_new_files, debounce=debounce)
            state.worker_thread = threading.Thread(
                target=_run_worker,
                args=(state,),
                daemon=True,
                name=f"openkb-watch-{kb}",
            )
            self._watchers[kb] = state
        state.worker_thread.start()
        return state

    def get(self, kb: str) -> WatcherState | None:
        with self._lock:
            return self._watchers.get(kb)

    def recent_events(self, kb: str) -> list[dict[str, Any]]:
        """Return public (seq-stripped) recent events for *kb* (empty if none)."""
        with self._lock:
            state = self._watchers.get(kb)
        if state is None:
            return []
        return [_public_event(e) for e in state.events]

    def status(self, kb: str) -> dict[str, Any]:
        with self._lock:
            state = self._watchers.get(kb)
            if state is None:
                return {"kb": kb, "active": False}
            with state._lock:
                counters = dict(state.counters)
            return {
                "kb": state.kb,
                "active": state.running.is_set(),
                "started_at": state.started_at,
                "raw_dir": str(state.raw_dir),
                "debounce": state.debounce,
                "counters": counters,
                "recent_events": [_public_event(e) for e in state.events],
            }

    def list_active(self) -> list[str]:
        with self._lock:
            return list(self._watchers.keys())

    def stop(self, kb: str) -> bool:
        """Stop and remove *kb*'s watcher. Return False if none was active.

        Joins the worker *before* clearing ``running`` and popping the state,
        so ``status()`` reports ``active: True`` (draining) while the old
        worker finishes. If the worker does not exit within the join timeout,
        the state is left in place with a ``watcher_draining`` event so
        ``start()`` knows not to spawn a second worker.
        """
        with self._lock:
            state = self._watchers.get(kb)
        if state is None:
            return False
        # Signal the worker and observer to stop, then wait for them.
        try:
            if state.observer is not None:
                state.observer.stop()
                state.observer.join(timeout=5.0)
        except Exception:
            pass
        state.queue.put(None)
        if state.worker_thread is not None:
            state.worker_thread.join(timeout=5.0)

        # If the worker is still alive after the join timeout, it is likely
        # mid-compile. Do not pop the state or clear running — leave a
        # draining marker so start() refuses to spawn a duplicate.
        if state.worker_thread is not None and state.worker_thread.is_alive():
            _record_event(state, "watcher_draining", {"kb": kb})
            return True

        # Worker has exited: safe to finalize.
        state.running.clear()
        _record_event(state, "watcher_stopped", {"kb": kb})
        with self._lock:
            # Only pop if it is still the same state (not replaced by start()).
            if self._watchers.get(kb) is state:
                self._watchers.pop(kb, None)
        return True

    def stop_all(self) -> None:
        for kb in self.list_active():
            self.stop(kb)
