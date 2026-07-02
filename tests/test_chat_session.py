"""Tests for chat session persistence."""

from __future__ import annotations

import json

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
