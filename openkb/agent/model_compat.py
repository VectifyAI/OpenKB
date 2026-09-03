"""Model-protocol compatibility helpers for agent runs."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from agents import RunConfig
from agents.run_config import CallModelData, ModelInputData

_IMAGE_OUTPUT_ACK = "Image returned successfully; inspect the following user image."
_IMAGE_INPUT_NOTE = "Images returned by the preceding tool call(s):"


def adapt_image_tool_outputs_for_chat_completions(
    data: CallModelData[Any],
) -> ModelInputData:
    """Move image tool outputs into a following user message.

    Chat Completions accepts images in user content but restricts tool content
    to text. The Agents SDK uses Responses-style ``input_image`` parts for
    ``ToolOutputImage``, so rewrite those parts immediately before the model
    call while preserving a text result for every tool call.
    """
    adapted: list[Any] = []
    pending_images: list[dict[str, Any]] = []

    def flush_images() -> None:
        if not pending_images:
            return
        adapted.append(
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": _IMAGE_INPUT_NOTE},
                    *pending_images,
                ],
            }
        )
        pending_images.clear()

    for item in data.model_data.input:
        if not (isinstance(item, dict) and item.get("type") == "function_call_output"):
            flush_images()
            adapted.append(item)
            continue

        output = item.get("output")
        if not isinstance(output, list):
            adapted.append(item)
            continue

        images = [
            part for part in output if isinstance(part, dict) and part.get("type") == "input_image"
        ]
        if not images:
            adapted.append(item)
            continue

        text_parts = [
            part for part in output if isinstance(part, dict) and part.get("type") == "input_text"
        ]
        adapted.append({**item, "output": text_parts or _IMAGE_OUTPUT_ACK})
        pending_images.extend(images)

    flush_images()
    return ModelInputData(input=adapted, instructions=data.model_data.instructions)


class _ChatCompletionsCompatFilter:
    """Compose an existing model-input filter with the image compatibility pass."""

    def __init__(
        self,
        existing_filter: Callable[[CallModelData[Any]], Awaitable[ModelInputData] | ModelInputData],
    ) -> None:
        self._existing_filter = existing_filter

    async def __call__(self, data: CallModelData[Any]) -> ModelInputData:
        filtered = self._existing_filter(data)
        if inspect.isawaitable(filtered):
            filtered = await filtered
        return adapt_image_tool_outputs_for_chat_completions(
            CallModelData(
                model_data=filtered,
                agent=data.agent,
                context=data.context,
            )
        )


CHAT_COMPLETIONS_RUN_CONFIG = RunConfig(
    call_model_input_filter=adapt_image_tool_outputs_for_chat_completions
)


def with_chat_completions_compat(run_config: RunConfig | None) -> RunConfig:
    """Return a run config that preserves existing settings and adapts image outputs."""
    if run_config is None:
        return CHAT_COMPLETIONS_RUN_CONFIG

    existing_filter = run_config.call_model_input_filter
    if existing_filter is None:
        return replace(
            run_config,
            call_model_input_filter=adapt_image_tool_outputs_for_chat_completions,
        )
    if existing_filter is adapt_image_tool_outputs_for_chat_completions or isinstance(
        existing_filter, _ChatCompletionsCompatFilter
    ):
        return run_config

    return replace(
        run_config,
        call_model_input_filter=_ChatCompletionsCompatFilter(existing_filter),
    )
