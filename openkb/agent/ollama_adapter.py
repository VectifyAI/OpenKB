"""Resilient wrapper for Ollama models that handles tool-call hallucination.

When using Ollama models via LiteLLM, small models often hallucinate tool
names instead of using the registered tools. The openai-agents SDK then
raises ``ModelBehaviorError: Tool X not found in agent …`` and the entire
query/chat run aborts with no recovery.

This module provides:

1. :func:`is_ollama_backend` — detect whether a model string targets Ollama.
2. :func:`run_with_retry` — wrap ``Runner.run`` in a try/except for
   ``ModelBehaviorError``, retrying with a corrective system message that
   tells the model which tools actually exist.
3. :func:`run_streamed_with_retry` — same but for ``Runner.run_streamed``.

The corrective message is injected as a **user** message appended to the
input, so the model sees its mistake and the available tools on the next
turn.  Only Ollama backends use the retry path; all other providers keep the
original bare-Runner behaviour unchanged.
"""

from __future__ import annotations

import logging
from typing import Any

from agents import Runner
from agents.exceptions import ModelBehaviorError

logger = logging.getLogger(__name__)

# Maximum retry attempts for tool-call hallucination errors.
DEFAULT_MAX_RETRIES = 3


def is_ollama_backend(model: str) -> bool:
    """Return *True* if *model* targets an Ollama backend.

    Accepts both ``ollama/llama3.2:1b`` (LiteLLM prefix) and
    ``litellm/ollama/llama3.2:1b`` (Agent-layer prefix used by OpenKB).
    """
    if not model:
        return False
    lower = model.lower()
    return lower.startswith("ollama/") or "/ollama/" in lower


def _extract_tool_names(agent: Any) -> list[str]:
    """Extract the list of registered tool names from an Agent instance."""
    names: list[str] = []
    for tool in getattr(agent, "tools", []) or []:
        # function_tool objects expose ``name``; raw functions expose ``__name__``
        name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
        if name:
            names.append(name)
    return names


def _build_correction_message(
    bad_tool_name: str,
    available_tools: list[str],
    attempt: int,
) -> str:
    """Build a corrective user message for a hallucinated tool call.

    Parameters
    ----------
    bad_tool_name:
        The tool name the model tried to use (extracted from the error).
    available_tools:
        The list of tool names actually registered on the agent.
    attempt:
        The retry attempt number (1-based), for escalation messaging.
    """
    tool_list = ", ".join(available_tools) if available_tools else "(none)"
    return (
        f"[SYSTEM CORRECTION {attempt}] You tried to call a tool named "
        f"'{bad_tool_name}', but that tool does not exist. "
        f"The only available tools are: {tool_list}. "
        f"Please answer the original question using ONLY these tools. "
        f"Do not invent tool names."
    )


def _extract_bad_tool_name(error: ModelBehaviorError) -> str:
    """Extract the hallucinated tool name from a ModelBehaviorError message.

    The SDK raises errors like::

        Tool search_strategy not found in agent wiki-query

    We extract ``search_strategy`` from that pattern.
    """
    msg = str(error)
    # Pattern: "Tool <name> not found in agent <agent_name>"
    prefix = "Tool "
    suffix = " not found in agent"
    if prefix in msg and suffix in msg:
        start = msg.index(prefix) + len(prefix)
        end = msg.index(suffix, start)
        return msg[start:end].strip()
    return "unknown"


def run_with_retry(
    agent: Any,
    input_data: str | list[dict[str, Any]],
    *,
    max_turns: int = 50,
    run_config: Any = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> Any:
    """Run ``Runner.run`` with retry on ``ModelBehaviorError`` for Ollama models.

    On each retry, a corrective user message is appended to the input so
    the model sees which tools are actually available.
    """
    available_tools = _extract_tool_names(agent)
    current_input: str | list[dict[str, Any]] = input_data

    for attempt in range(1, max_retries + 2):  # 1..max_retries+1 (first + retries)
        try:
            if run_config:
                return Runner.run_sync(
                    agent, current_input, max_turns=max_turns, run_config=run_config,
                )
            return Runner.run_sync(agent, current_input, max_turns=max_turns)
        except ModelBehaviorError as exc:
            if attempt > max_retries:
                raise
            bad_name = _extract_bad_tool_name(exc)
            logger.warning(
                "Ollama tool-call retry %d/%d: model called '%s', available: %s",
                attempt, max_retries, bad_name, available_tools,
            )
            correction = _build_correction_message(bad_name, available_tools, attempt)
            current_input = _append_correction(current_input, correction)


async def arun_with_retry(
    agent: Any,
    input_data: str | list[dict[str, Any]],
    *,
    max_turns: int = 50,
    run_config: Any = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> Any:
    """Async version of :func:`run_with_retry` using ``await Runner.run``."""
    available_tools = _extract_tool_names(agent)
    current_input: str | list[dict[str, Any]] = input_data

    for attempt in range(1, max_retries + 2):
        try:
            if run_config:
                return await Runner.run(
                    agent, current_input, max_turns=max_turns, run_config=run_config,
                )
            return await Runner.run(agent, current_input, max_turns=max_turns)
        except ModelBehaviorError as exc:
            if attempt > max_retries:
                raise
            bad_name = _extract_bad_tool_name(exc)
            logger.warning(
                "Ollama tool-call retry %d/%d: model called '%s', available: %s",
                attempt, max_retries, bad_name, available_tools,
            )
            correction = _build_correction_message(bad_name, available_tools, attempt)
            current_input = _append_correction(current_input, correction)


def run_streamed_with_retry(
    agent: Any,
    input_data: str | list[dict[str, Any]],
    *,
    max_turns: int = 50,
    run_config: Any = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> Any:
    """Run ``Runner.run_streamed`` with retry on ``ModelBehaviorError``.

    Streaming retry works by catching the error from ``stream_events()`` and
    re-creating the streamed run with a corrective message. The caller
    should iterate ``stream_events()`` on the returned object; if the run
    fails, this function will re-create the stream and return the new one.
    """
    available_tools = _extract_tool_names(agent)
    current_input: str | list[dict[str, Any]] = input_data
    last_error: ModelBehaviorError | None = None

    for attempt in range(1, max_retries + 2):
        if run_config:
            result = Runner.run_streamed(
                agent, current_input, max_turns=max_turns, run_config=run_config,
            )
        else:
            result = Runner.run_streamed(agent, current_input, max_turns=max_turns)

        # For streaming, we need to check if the run fails during iteration.
        # We use a wrapper that catches errors from stream_events().
        try:
            # Consume the stream to see if it errors.
            # If it does, we retry with a correction.
            # If it succeeds, we return the result (already consumed).
            # But we need to yield events to the caller...
            # So we return a _RetryableStreamResult that handles this.
            return _RetryableStreamResult(
                result,
                agent=agent,
                available_tools=available_tools,
                current_input=current_input,
                max_turns=max_turns,
                run_config=run_config,
                max_retries=max_retries,
                attempt=attempt,
            )
        except ModelBehaviorError as exc:
            if attempt > max_retries:
                raise
            bad_name = _extract_bad_tool_name(exc)
            logger.warning(
                "Ollama streamed tool-call retry %d/%d: model called '%s'",
                attempt, max_retries, bad_name,
            )
            correction = _build_correction_message(bad_name, available_tools, attempt)
            current_input = _append_correction(current_input, correction)
            last_error = exc

    if last_error:
        raise last_error
    return result  # type: ignore[possibly-undefined]


class _RetryableStreamResult:
    """Wrapper around a streamed RunResult that retries on ModelBehaviorError.

    The first ``stream_events()`` call consumes the underlying stream. If
    a ``ModelBehaviorError`` is raised during iteration, the wrapper catches
    it, builds a corrective message, re-creates the stream, and continues
    yielding events from the new stream. The caller sees a single seamless
    event iterator.
    """

    def __init__(
        self,
        initial_result: Any,
        *,
        agent: Any,
        available_tools: list[str],
        current_input: str | list[dict[str, Any]],
        max_turns: int,
        run_config: Any,
        max_retries: int,
        attempt: int,
    ) -> None:
        self._result = initial_result
        self._agent = agent
        self._available_tools = available_tools
        self._current_input = current_input
        self._max_turns = max_turns
        self._run_config = run_config
        self._max_retries = max_retries
        self._attempt = attempt

    @property
    def final_output(self) -> Any:
        return self._result.final_output

    def to_input_list(self) -> list[dict[str, Any]]:
        return self._result.to_input_list()

    async def stream_events(self) -> Any:
        """Yield events, retrying on ModelBehaviorError."""
        import asyncio

        result = self._result
        current_input = self._current_input

        for attempt in range(self._attempt, self._max_retries + 2):
            try:
                async for event in result.stream_events():
                    yield event
                return  # success — stop retrying
            except ModelBehaviorError as exc:
                if attempt > self._max_retries:
                    raise
                bad_name = _extract_bad_tool_name(exc)
                logger.warning(
                    "Ollama streamed retry %d/%d: model called '%s'",
                    attempt, self._max_retries, bad_name,
                )
                correction = _build_correction_message(
                    bad_name, self._available_tools, attempt,
                )
                current_input = _append_correction(current_input, correction)
                if self._run_config:
                    result = Runner.run_streamed(
                        self._agent, current_input,
                        max_turns=self._max_turns, run_config=self._run_config,
                    )
                else:
                    result = Runner.run_streamed(
                        self._agent, current_input, max_turns=self._max_turns,
                    )


def _append_correction(
    input_data: str | list[dict[str, Any]],
    correction: str,
) -> str | list[dict[str, Any]]:
    """Append a correction message to the input data.

    If *input_data* is a string, return ``[original, correction]`` as a
    list. If it's already a list, append a user message dict.
    """
    if isinstance(input_data, str):
        return [
            {"role": "user", "content": input_data},
            {"role": "user", "content": correction},
        ]
    if isinstance(input_data, list):
        return [*input_data, {"role": "user", "content": correction}]
    return input_data