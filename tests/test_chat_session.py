"""Tests for chat session persistence."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import pytest

from openkb.agent import chat as chat_mod
from openkb.agent.chat import iter_chat_turn_events
from openkb.agent.chat_session import ChatSession, load_session


def _image_history() -> list[dict[str, object]]:
    return [
        {"role": "user", "content": "Describe the diagram."},
        {
            "type": "function_call",
            "call_id": "call_123",
            "name": "get_image",
            "arguments": '{"image_path":"sources/images/doc/figure-1.png"}',
        },
        {
            "type": "function_call_output",
            "call_id": "call_123",
            "output": [
                {
                    "type": "input_image",
                    "image_url": "data:image/png;base64,AAAA",
                }
            ],
        },
    ]


def test_record_turn_replaces_data_image_with_text_reference(tmp_path):
    session = ChatSession.new(tmp_path, "gpt-4o-mini", "en")

    session.record_turn(
        "Describe the diagram.",
        "It is a flow chart.",
        _image_history(),
    )

    saved = json.loads(session.path.read_text(encoding="utf-8"))
    output_part = saved["history"][2]["output"][0]

    assert output_part["type"] == "input_text"
    assert "data:image/png;base64,AAAA" not in session.path.read_text(encoding="utf-8")
    assert "sources/images/doc/figure-1.png" in output_part["text"]
    assert "Call get_image again" in output_part["text"]


def test_load_session_sanitizes_legacy_image_history(tmp_path):
    session = ChatSession.new(tmp_path, "gpt-4o-mini", "en")
    raw_history = _image_history()
    session.path.parent.mkdir(parents=True, exist_ok=True)
    session.path.write_text(
        json.dumps(
            {
                "id": session.id,
                "created_at": session.created_at,
                "updated_at": session.updated_at,
                "model": session.model,
                "language": session.language,
                "title": "",
                "turn_count": 1,
                "history": raw_history,
                "user_turns": ["Describe the diagram."],
                "assistant_texts": ["It is a flow chart."],
            }
        ),
        encoding="utf-8",
    )

    loaded = load_session(tmp_path, session.id)

    output_part = loaded.history[2]["output"][0]
    assert output_part["type"] == "input_text"
    assert "data:image/png;base64,AAAA" not in output_part["text"]
    assert "sources/images/doc/figure-1.png" in output_part["text"]


def test_record_turn_persists_and_roundtrips_trace(tmp_path):
    session = ChatSession.new(tmp_path, "gpt-4o-mini", "en")
    trace = [
        {"kind": "text", "text": "Let me check the index."},
        {"kind": "tool", "name": "read_file", "arguments": '{"path":"index.md"}'},
        {"kind": "text", "text": "Here is the answer."},
    ]

    session.record_turn("q", "Here is the answer.", [], trace=trace)

    saved = json.loads(session.path.read_text(encoding="utf-8"))
    assert saved["assistant_traces"] == [trace]

    loaded = load_session(tmp_path, session.id)
    assert loaded.assistant_traces == [trace]


def test_record_turn_without_trace_stores_empty_list(tmp_path):
    session = ChatSession.new(tmp_path, "gpt-4o-mini", "en")
    session.record_turn("q", "a", [])
    assert session.assistant_traces == [[]]


def test_load_session_missing_traces_then_new_turn_stays_aligned(tmp_path):
    # A session saved before traces existed: assistant_texts present, no
    # assistant_traces key at all.
    session = ChatSession.new(tmp_path, "gpt-4o-mini", "en")
    session.path.parent.mkdir(parents=True, exist_ok=True)
    session.path.write_text(
        json.dumps(
            {
                "id": session.id,
                "created_at": session.created_at,
                "updated_at": session.updated_at,
                "model": session.model,
                "language": session.language,
                "title": "old",
                "turn_count": 2,
                "history": [],
                "user_turns": ["q1", "q2"],
                "assistant_texts": ["a1", "a2"],
            }
        ),
        encoding="utf-8",
    )

    loaded = load_session(tmp_path, session.id)
    assert loaded.assistant_traces == []  # backward compat: absent -> empty, no crash

    # A new turn back-fills empty traces for the two legacy turns (so index i
    # keeps mapping to the same turn), then appends the new turn's real trace.
    new_trace = [{"kind": "text", "text": "a3"}]
    loaded.record_turn("q3", "a3", [], trace=new_trace)
    assert loaded.assistant_traces == [[], [], new_trace]
    assert len(loaded.assistant_traces) == len(loaded.assistant_texts) == 3


def _fake_event_stream(events: list[dict[str, Any]]):
    """Build a stand-in for ``iter_agent_response_events`` that replays *events*."""

    async def _stream(agent, input_data, **kwargs) -> AsyncIterator[dict[str, Any]]:
        for event in events:
            yield event

    return _stream


@pytest.mark.asyncio
async def test_whitespace_only_delta_then_final_answer_lands_in_trace(tmp_path, monkeypatch):
    """A whitespace-only streamed delta must not swallow the final answer.

    Regression: a `" "` / `"\\n"` delta creates a text step, so a
    presence-only guard treats that empty step as "the answer is already in the
    trace" and never records the real answer -> a restored turn renders empty.
    The guard now requires a text step with non-whitespace content.
    """
    session = ChatSession.new(tmp_path, "gpt-4o-mini", "en")

    events = [
        {"event": "delta", "data": {"text": " "}},
        {"event": "delta", "data": {"text": "\n"}},
        {"event": "final", "data": {"answer": "The real answer.", "history": []}},
    ]
    monkeypatch.setattr(chat_mod, "iter_agent_response_events", _fake_event_stream(events))

    collected = [event async for event in iter_chat_turn_events(object(), session, "q")]

    # The final frame still carries the answer...
    final = collected[-1]
    assert final["event"] == "final"
    assert final["data"]["answer"] == "The real answer."

    # ...and the persisted trace contains the real answer as a text step, so a
    # restored turn is not blank.
    persisted_trace = session.assistant_traces[-1]
    assert any(
        step.get("kind") == "text" and step.get("text") == "The real answer."
        for step in persisted_trace
    )


@pytest.mark.asyncio
async def test_substantive_streamed_text_is_not_duplicated_by_final(tmp_path, monkeypatch):
    """When real text was streamed, the final answer must not be appended again."""
    session = ChatSession.new(tmp_path, "gpt-4o-mini", "en")

    events = [
        {"event": "delta", "data": {"text": "The real "}},
        {"event": "delta", "data": {"text": "answer."}},
        {"event": "final", "data": {"answer": "The real answer.", "history": []}},
    ]
    monkeypatch.setattr(chat_mod, "iter_agent_response_events", _fake_event_stream(events))

    _ = [event async for event in iter_chat_turn_events(object(), session, "q")]

    persisted_trace = session.assistant_traces[-1]
    text_steps = [s for s in persisted_trace if s.get("kind") == "text"]
    assert len(text_steps) == 1
    assert text_steps[0]["text"] == "The real answer."
