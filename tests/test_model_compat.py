"""Tests for model-protocol compatibility adapters."""

from __future__ import annotations

from typing import Any

import pytest
from agents.models.chatcmpl_converter import Converter
from agents.run_config import CallModelData, ModelInputData

from openkb.agent.model_compat import (
    CHAT_COMPLETIONS_RUN_CONFIG,
    adapt_image_tool_outputs_for_chat_completions,
    with_chat_completions_compat,
)


def _adapt(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    data = CallModelData(
        model_data=ModelInputData(input=items, instructions="system"),
        agent=None,  # type: ignore[arg-type]
        context=None,
    )
    return adapt_image_tool_outputs_for_chat_completions(data).input


def test_moves_image_tool_output_to_following_user_message():
    items = [
        {"role": "user", "content": "Describe the image."},
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "get_image",
            "arguments": '{"image_path":"figure.png"}',
        },
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": [
                {
                    "type": "input_image",
                    "image_url": "data:image/png;base64,AAAA",
                }
            ],
        },
    ]

    messages = Converter.items_to_messages(
        _adapt(items),
        model="openai/qwen3.6-plus",
        preserve_tool_output_all_content=True,
    )

    assert messages[2]["role"] == "tool"
    assert isinstance(messages[2]["content"], str)
    assert messages[3] == {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": "Images returned by the preceding tool call(s):",
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/png;base64,AAAA",
                    "detail": "auto",
                },
            },
        ],
    }


def test_keeps_parallel_tool_outputs_before_synthetic_user_message():
    items = [
        {"role": "user", "content": "Compare the files."},
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "get_image",
            "arguments": "{}",
        },
        {
            "type": "function_call",
            "call_id": "call_2",
            "name": "read_file",
            "arguments": "{}",
        },
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": [
                {
                    "type": "input_image",
                    "image_url": "data:image/png;base64,AAAA",
                }
            ],
        },
        {
            "type": "function_call_output",
            "call_id": "call_2",
            "output": "file contents",
        },
    ]

    adapted = _adapt(items)

    assert [item.get("type") for item in adapted[3:5]] == [
        "function_call_output",
        "function_call_output",
    ]
    assert adapted[5]["role"] == "user"


def test_uses_default_compat_config_when_run_config_is_missing():
    assert with_chat_completions_compat(None) is CHAT_COMPLETIONS_RUN_CONFIG


def test_adds_compat_filter_without_replacing_existing_settings():
    from agents import RunConfig

    original = RunConfig(model="litellm/openai/qwen3.6-plus")

    merged = with_chat_completions_compat(original)

    assert merged is not original
    assert merged.model == original.model
    assert merged.call_model_input_filter is adapt_image_tool_outputs_for_chat_completions


@pytest.mark.asyncio
async def test_composes_existing_filter_before_image_compat():
    from agents import RunConfig

    def existing_filter(data: CallModelData[Any]) -> ModelInputData:
        return ModelInputData(
            input=[{"role": "user", "content": "prefixed"}, *data.model_data.input],
            instructions=data.model_data.instructions,
        )

    merged = with_chat_completions_compat(
        RunConfig(
            model="litellm/openai/qwen3.6-plus",
            call_model_input_filter=existing_filter,
        )
    )
    data = CallModelData(
        model_data=ModelInputData(
            input=[
                {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": [
                        {
                            "type": "input_image",
                            "image_url": "data:image/png;base64,AAAA",
                        }
                    ],
                }
            ],
            instructions="system",
        ),
        agent=None,  # type: ignore[arg-type]
        context=None,
    )

    result = merged.call_model_input_filter(data)
    assert result is not None
    if hasattr(result, "__await__"):
        result = await result

    assert result.input[0] == {"role": "user", "content": "prefixed"}
    assert result.input[1]["output"] == (
        "Image returned successfully; inspect the following user image."
    )
    assert result.input[2]["role"] == "user"
    assert with_chat_completions_compat(merged) is merged
