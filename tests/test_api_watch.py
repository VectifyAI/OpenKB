"""Tests for the watch REST endpoints (lifecycle + SSE), isolated per app.

Mirrors the helpers/patterns in tests/test_api.py. Ingest is mocked so no real
LLM/compilation runs.
"""
from __future__ import annotations

import json
import time
from typing import Any

from fastapi.testclient import TestClient

from openkb.api import create_app
from openkb.cli import AddFileResult


def _client(monkeypatch, token: str | None = "secret") -> TestClient:
    if token is None:
        monkeypatch.delenv("OPENKB_API_TOKEN", raising=False)
    else:
        monkeypatch.setenv("OPENKB_API_TOKEN", token)
    return TestClient(create_app())


def _auth(token: str = "secret") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _use_named_kb(monkeypatch, kb_dir, name: str = "test-kb") -> str:
    def resolve(kb):
        assert kb == name
        return kb_dir

    monkeypatch.setattr("openkb.api_helpers.resolve_kb_alias", resolve)
    return name


def _events_from_sse(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in text.strip().split("\n\n"):
        lines = block.splitlines()
        if not lines:
            continue
        event = lines[0].removeprefix("event: ")
        data = json.loads(lines[1].removeprefix("data: "))
        events.append({"event": event, "data": data})
    return events


def _mock_add(monkeypatch, status: str = "added"):
    monkeypatch.setattr(
        "openkb.watch_service._add_for_api",
        lambda path, kb, bundle=None: AddFileResult(path.name, str(path), status, "msg"),
    )


def test_watch_endpoints_require_token(monkeypatch, kb_dir):
    client = _client(monkeypatch, token=None)
    kb = _use_named_kb(monkeypatch, kb_dir)
    for method, path in (
        ("post", "/api/v1/watch/start"),
        ("post", "/api/v1/watch/status"),
    ):
        resp = getattr(client, method)(path, json={"kb": kb})
        assert resp.status_code == 500
        assert "OPENKB_API_TOKEN" in resp.json()["detail"]
    resp = client.get("/api/v1/watch/events", params={"kb": kb})
    assert resp.status_code == 500


def test_watch_start_status_stop_lifecycle(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    _mock_add(monkeypatch)

    started = client.post("/api/v1/watch/start", json={"kb": kb}, headers=_auth())
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["active"] is True
    assert body["kb"] == kb
    assert body["raw_dir"].endswith("raw")
    assert body["debounce"] == 2.0

    status = client.post("/api/v1/watch/status", json={"kb": kb}, headers=_auth())
    assert status.status_code == 200
    assert status.json()["active"] is True

    stopped = client.post("/api/v1/watch/stop", json={"kb": kb}, headers=_auth())
    assert stopped.status_code == 200
    stop_body = stopped.json()
    assert stop_body["kb"] == kb
    assert stop_body["active"] is False

    status2 = client.post("/api/v1/watch/status", json={"kb": kb}, headers=_auth())
    assert status2.status_code == 200
    assert status2.json()["active"] is False


def test_watch_start_is_idempotent(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    _mock_add(monkeypatch)

    first = client.post("/api/v1/watch/start", json={"kb": kb}, headers=_auth())
    second = client.post("/api/v1/watch/start", json={"kb": kb}, headers=_auth())
    assert first.status_code == 200 and second.status_code == 200
    assert first.json() == second.json()
    client.post("/api/v1/watch/stop", json={"kb": kb}, headers=_auth())


def test_watch_stop_unknown_returns_404(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    resp = client.post("/api/v1/watch/stop", json={"kb": kb}, headers=_auth())
    assert resp.status_code == 404
    assert "No active watcher" in resp.json()["detail"]


def test_watch_start_rejects_invalid_debounce(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    resp = client.post("/api/v1/watch/start", json={"kb": kb, "debounce": 0}, headers=_auth())
    assert resp.status_code == 422


def _wait_for_added(client, kb: str, timeout: float = 6.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = client.post("/api/v1/watch/status", json={"kb": kb}, headers=_auth())
        if st.json().get("counters", {}).get("added", 0) >= 1:
            return True
        time.sleep(0.1)
    return False


def test_watch_events_streams_file_done_after_drop(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    _mock_add(monkeypatch)

    started = client.post("/api/v1/watch/start", json={"kb": kb, "debounce": 0.1}, headers=_auth())
    assert started.status_code == 200
    try:
        (kb_dir / "raw" / "dropped.md").write_text("# hi", encoding="utf-8")
        assert _wait_for_added(client, kb), "file was not processed in time"

        resp = client.get(
            "/api/v1/watch/events",
            params={"kb": kb, "max_events": 2, "timeout_seconds": 5},
            headers=_auth(),
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        events = _events_from_sse(resp.text)
        names = [e["event"] for e in events]
        assert names[0] == "start"
        assert names[-1] == "done"
        assert "file_start" in names and "file_done" in names
    finally:
        client.post("/api/v1/watch/stop", json={"kb": kb}, headers=_auth())


def test_watch_events_inactive_returns_error(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    resp = client.get("/api/v1/watch/events", params={"kb": kb, "max_events": 1}, headers=_auth())
    events = _events_from_sse(resp.text)
    assert events[0]["event"] == "start"
    assert events[0]["data"]["active"] is False
    assert any(e["event"] == "error" for e in events)
    assert events[-1]["event"] == "done"


def test_watch_events_disconnect_terminates(monkeypatch, kb_dir):
    """The SSE stream must terminate when the client disconnects."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from openkb.api_helpers import _stream_watch_events
    from openkb.watch_service import WatchRegistry

    reg = WatchRegistry()
    reg.start("t", kb_dir, debounce=0.1)
    try:
        fake_request = MagicMock()
        fake_request.is_disconnected = AsyncMock(return_value=True)

        async def collect():
            events = []
            async for chunk in _stream_watch_events(reg, "t", None, None, fake_request):
                events.append(chunk)
            return events

        result = asyncio.run(collect())
        # With is_disconnected=True, the stream yields start then immediately
        # returns (done is NOT emitted because we return before the final yield,
        # but the generator is exhausted so collect() finishes).
        assert len(result) >= 1  # at least the start event
        reg.stop("t")
    finally:
        reg.stop("t")



def test_watch_events_default_timeout_terminates(monkeypatch, kb_dir):
    """With max_events and timeout_seconds both None, the stream must use the
    default _WATCH_SSE_TIMEOUT cap instead of running indefinitely."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from openkb.api_helpers import _stream_watch_events
    from openkb.watch_service import WatchRegistry

    monkeypatch.setattr("openkb.api_helpers._WATCH_SSE_TIMEOUT", 0.05)

    reg = WatchRegistry()
    reg.start("t", kb_dir, debounce=0.1)
    try:
        fake_request = MagicMock()
        fake_request.is_disconnected = AsyncMock(return_value=False)

        async def collect():
            events = []
            async for chunk in _stream_watch_events(reg, "t", None, None, fake_request):
                events.append(chunk)
            return events

        result = asyncio.run(collect())
        # Stream must terminate on its own (not hang) and emit done.
        joined = "\n".join(result)
        assert "event: done" in joined
    finally:
        reg.stop("t")
