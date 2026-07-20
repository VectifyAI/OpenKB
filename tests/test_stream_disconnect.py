"""Regression: recompile/deck/skill SSE streams must cooperatively stop on
client disconnect and release the per-KB mutation lock.

Before the fix these three helpers took no ``Request`` and never polled
``is_disconnected()``, so an aborting client (frontend Stop / navigate-away /
sheet-close) left the server running the whole LLM generation while still
holding ``_kb_mutation_lock``. ``_stream_chat`` / ``_stream_query`` already
poll the request between events; these tests confirm the three mutation
streams now do the same.

Each test drives a helper directly with a fake ``Request`` whose
``is_disconnected()`` returns True and asserts the stream stops early (does not
forward the terminal event) and that the ``asyncio.Lock`` it holds is released
when the generator exits. Mirrors ``test_watch_events_disconnect_terminates``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from openkb import api_helpers
from openkb.api_helpers import _stream_deck, _stream_recompile, _stream_skill
from openkb.api_models import DeckRequest, RecompileRequest, SkillRequest


def _fake_request(disconnected: bool) -> MagicMock:
    req = MagicMock()
    req.is_disconnected = AsyncMock(return_value=disconnected)
    return req


def _drain(agen) -> list[str]:
    async def collect() -> list[str]:
        return [chunk async for chunk in agen]

    return asyncio.run(collect())


def _event_names(frames: list[str]) -> list[str]:
    """Pull the ``event:`` name out of each SSE frame (one event per frame)."""
    names: list[str] = []
    for frame in frames:
        for line in frame.splitlines():
            if line.startswith("event: "):
                names.append(line[len("event: ") :])
    return names


def test_recompile_stream_stops_and_releases_lock_on_disconnect(monkeypatch):
    consumed = 0

    async def fake_iter_recompile(kb_dir, doc_name, **kwargs):
        nonlocal consumed
        for ev in (
            {"event": "plan", "targets": []},
            {"event": "doc", "name": "a", "status": "ok"},
            {"event": "final", "recompiled": 1, "skipped": 0},
        ):
            consumed += 1
            yield ev

    monkeypatch.setattr(api_helpers, "iter_recompile", fake_iter_recompile)

    lock = asyncio.Lock()
    request = RecompileRequest(kb="k", all_docs=True)
    frames = _drain(_stream_recompile(request, Path("."), lock, _fake_request(True), bundle=None))

    names = _event_names(frames)
    # Breaks at the top of the loop, so no plan/doc/final is forwarded.
    assert names == ["start", "done"]
    # The disconnect is caught before draining the recompile generator.
    assert consumed == 1
    # The generator exited, so ``async with mutation_lock`` released the lock.
    assert not lock.locked()


def test_recompile_stream_completes_and_releases_lock_when_connected(monkeypatch):
    """Control: the added poll must not break a normal (connected) run."""

    async def fake_iter_recompile(kb_dir, doc_name, **kwargs):
        yield {"event": "plan", "targets": []}
        yield {"event": "doc", "name": "a", "status": "ok"}
        yield {"event": "final", "recompiled": 1, "skipped": 0}

    monkeypatch.setattr(api_helpers, "iter_recompile", fake_iter_recompile)

    lock = asyncio.Lock()
    request = RecompileRequest(kb="k", all_docs=True)
    frames = _drain(_stream_recompile(request, Path("."), lock, _fake_request(False), bundle=None))

    names = _event_names(frames)
    assert names == ["start", "plan", "doc", "final", "done"]
    assert not lock.locked()


def test_deck_stream_stops_and_releases_lock_on_disconnect(monkeypatch):
    async def fake_iter_deck(request, kb_dir, **kwargs):
        yield {"event": "start", "endpoint": "deck"}
        yield {"event": "final", "name": "d", "status": "done", "path": "/x"}

    monkeypatch.setattr(api_helpers, "_iter_deck", fake_iter_deck)

    lock = asyncio.Lock()
    request = DeckRequest(kb="k", name="d", intent="i")
    frames = _drain(_stream_deck(request, Path("."), lock, _fake_request(True), bundle=None))

    names = _event_names(frames)
    assert "final" not in names
    assert not lock.locked()


def test_skill_stream_stops_and_releases_lock_on_disconnect(monkeypatch):
    async def fake_iter_skill(request, kb_dir, **kwargs):
        yield {"event": "start", "endpoint": "skill"}
        yield {"event": "final", "name": "s", "status": "done", "path": "/x"}

    monkeypatch.setattr(api_helpers, "_iter_skill", fake_iter_skill)

    lock = asyncio.Lock()
    request = SkillRequest(kb="k", name="s", intent="i")
    frames = _drain(_stream_skill(request, Path("."), lock, _fake_request(True), bundle=None))

    names = _event_names(frames)
    assert "final" not in names
    assert not lock.locked()
