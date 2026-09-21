"""Unit tests for openkb.responses (no network — pure translation logic)."""

from types import SimpleNamespace

from openkb.responses import (
    adapt_response,
    build_responses_params,
    extract_output_text,
    is_responses_model,
    messages_to_responses_input,
    response_format_to_text,
    split_responses_model,
)


def test_prefix_detection():
    assert is_responses_model("openai-responses/muse-spark-1.3-contributor")
    assert not is_responses_model("openai/gpt-5.4")
    assert not is_responses_model("gemini/gemini-2.5-flash")


def test_split():
    litellm_model, bare = split_responses_model("openai-responses/muse-spark-1.3-contributor")
    assert litellm_model == "openai/muse-spark-1.3-contributor"
    assert bare == "muse-spark-1.3-contributor"


def test_messages_passthrough():
    out = messages_to_responses_input(
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
    )
    assert out == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


def test_messages_tool_round_trip():
    out = messages_to_responses_input(
        [
            {
                "role": "assistant",
                "content": "calling",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read", "arguments": '{"p": "x"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "file text"},
        ]
    )
    assert {"role": "assistant", "content": "calling"} in out
    assert {
        "type": "function_call",
        "call_id": "call_1",
        "name": "read",
        "arguments": '{"p": "x"}',
    } in out
    assert {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "file text",
    } in out


def test_messages_skip_none_content():
    assert messages_to_responses_input([{"role": "assistant", "content": None}]) == []


def test_assistant_blocks_with_tool_calls_translated():
    out = messages_to_responses_input(
        [
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "calling"}],
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "f", "arguments": "{}"},
                    }
                ],
            }
        ]
    )
    assert {
        "role": "assistant",
        "content": [{"type": "output_text", "text": "calling"}],
    } in out
    assert {"type": "function_call", "call_id": "c1", "name": "f", "arguments": "{}"} in out


def test_content_block_translation():
    out = messages_to_responses_input(
        [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "look",
                        "cache_control": {"type": "ephemeral"},
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,xx", "detail": "high"},
                    },
                    {"type": "mystery", "cache_control": {"type": "ephemeral"}, "x": 1},
                ],
            },
            {"role": "assistant", "content": [{"type": "text", "text": "prior"}]},
        ]
    )
    assert out == [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "look"},
                {
                    "type": "input_image",
                    "image_url": "data:image/png;base64,xx",
                    "detail": "high",
                },
                {"type": "mystery", "x": 1},
            ],
        },
        {
            "role": "assistant",
            "content": [{"type": "output_text", "text": "prior"}],
        },
    ]


def test_build_agents_responses_model_wiring():
    from openkb.responses import build_agents_responses_model

    model = build_agents_responses_model(
        "openai-responses/muse-spark-1.3-contributor",
        api_key="k",
        base_url="https://example.test/v1",
        extra_headers={"x-opencode-session": "s"},
        timeout=120,
    )
    assert model.model == "muse-spark-1.3-contributor"
    client = model._client
    assert str(client.base_url) == "https://example.test/v1/"
    assert client._custom_headers.get("x-opencode-session") == "s"


def test_build_agents_responses_model_defaults_omit_timeout():
    import os

    from openkb.responses import build_agents_responses_model

    os.environ["LLM_API_KEY"] = "env-key"
    os.environ["OPENAI_API_BASE"] = "https://env.test/v1"
    try:
        model = build_agents_responses_model("openai-responses/m")
    finally:
        del os.environ["LLM_API_KEY"]
        del os.environ["OPENAI_API_BASE"]
    assert str(model._client.base_url) == "https://env.test/v1/"
    assert model._client.timeout is not None


def test_response_format_mapping():
    assert response_format_to_text({"type": "json_object"}) == {"format": {"type": "json_object"}}
    mapped = response_format_to_text(
        {"type": "json_schema", "json_schema": {"name": "r", "schema": {"type": "object"}}}
    )
    assert mapped["format"]["type"] == "json_schema"
    assert mapped["format"]["name"] == "r"
    assert response_format_to_text({"type": "text"}) is None
    assert response_format_to_text(None) is None


def _fake_responses_output(text="done", incomplete=False):
    return SimpleNamespace(
        output=[
            {"type": "message", "content": [{"type": "output_text", "text": text}]},
            {"type": "reasoning", "summary": []},
        ],
        status="incomplete" if incomplete else "completed",
        usage=SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=15),
    )


def test_extract_text():
    text, incomplete = extract_output_text(_fake_responses_output("hello"))
    assert text == "hello"
    assert incomplete is False
    _, incomplete = extract_output_text(_fake_responses_output(incomplete=True))
    assert incomplete is True


def test_adapt_response_shape():
    adapted = adapt_response(_fake_responses_output("hi"))
    assert adapted.choices[0].message.content == "hi"
    assert adapted.choices[0].finish_reason == "stop"
    assert adapted.usage.prompt_tokens == 10
    assert adapted.usage.completion_tokens == 5
    adapted = adapt_response(_fake_responses_output(incomplete=True))
    assert adapted.choices[0].finish_reason == "length"


def test_build_params_mapping():
    model, params = build_responses_params(
        "openai-responses/muse-spark-1.3-contributor",
        [{"role": "user", "content": "hi"}],
        max_tokens=100,
        response_format={"type": "json_object"},
        temperature=0.5,
        stop=["x"],
        api_key="k",
        base_url="b",
    )
    assert model == "openai/muse-spark-1.3-contributor"
    assert params["max_output_tokens"] == 100
    assert params["text"] == {"format": {"type": "json_object"}}
    assert params["temperature"] == 0.5
    assert "stop" not in params  # dropped, unsupported
    assert params["api_key"] == "k"
    assert params["base_url"] == "b"
    assert params["input"] == [{"role": "user", "content": "hi"}]
