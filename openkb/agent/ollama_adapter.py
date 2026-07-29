"""Resilient wrapper for Ollama models that handles tool-call issues.

When using Ollama models via LiteLLM, two problems cause the openai-agents
SDK tool-calling loop to fail:

1. **LiteLLM prompt injection fallback** — when the model is addressed as
   ``ollama/<model>``, LiteLLM treats it as a "legacy" Ollama endpoint that
   does not support native tool calling.  It strips the ``tools`` parameter,
   sets ``format: json``, and injects the tool definitions into the prompt
   text.  The model then returns tool-call JSON in the ``content`` field
   instead of the ``tool_calls`` field, and the SDK cannot execute the tool.

   **Fix**: rewrite ``ollama/<model>`` to ``ollama_chat/<model>`` so LiteLLM
   uses the native Ollama Chat API endpoint, which supports ``tools``
   natively (Ollama >= 0.4).

2. **Tool-name hallucination** — small models may call non-existent tools
   (e.g. ``get_topics`` instead of ``read_file``), causing
   ``ModelBehaviorError: Tool X not found in agent …``.

   **Fix**: wrap ``Runner.run`` / ``Runner.run_streamed`` in a retry loop
   that catches ``ModelBehaviorError`` and retries with a corrective message
   listing the actual available tools.

3. **Timeouts** — local models on modest hardware can take minutes per
   tool-calling turn.  The adapter passes a configurable timeout (default
   300 s) to LiteLLM so the loop does not abort prematurely.

Only Ollama backends use the rewrite + retry path; all other providers keep
the original bare-Runner behaviour unchanged.
"""

from __future__ import annotations

import logging
from typing import Any

from agents import Runner
from agents.exceptions import ModelBehaviorError

logger = logging.getLogger(__name__)

# Maximum retry attempts for tool-call hallucination errors.
DEFAULT_MAX_RETRIES = 3

# Default per-request timeout for Ollama models (seconds).
# Local models on modest hardware can take 60-120s per tool-calling turn.
DEFAULT_OLLAMA_TIMEOUT = 300


def is_ollama_backend(model: str) -> bool:
    """Return *True* if *model* targets an Ollama backend.

    Accepts ``ollama/...``, ``ollama_chat/...``, and ``litellm/ollama/...``
    (the Agent-layer prefix used by OpenKB).
    """
    if not model:
        return False
    lower = model.lower()
    return ("ollama/" in lower or "ollama_chat/" in lower)


def rewrite_ollama_model(model: str) -> str:
    """Rewrite ``ollama/<model>`` to ``ollama_chat/<model>`` for native tools.

    LiteLLM treats ``ollama/`` as a legacy endpoint and falls back to prompt
    injection for tool calls.  ``ollama_chat/`` uses the native Ollama Chat
    API which supports ``tools`` natively (Ollama >= 0.4).

    If *model* already uses ``ollama_chat/`` or is not an Ollama model, it is
    returned unchanged.
    """
    if not model:
        return model
    # Strip "litellm/" prefix first (Agent-layer convention)
    stripped = model
    if stripped.startswith("litellm/"):
        stripped = stripped[len("litellm/"):]

    if stripped.startswith("ollama/") and not stripped.startswith("ollama_chat/"):
        return "ollama_chat/" + stripped[len("ollama/"):]
    if stripped.startswith("ollama_chat/"):
        return stripped  # already correct
    return model


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


def _ensure_ollama_timeout(
    agent: Any,
    timeout: float | None,
) -> None:
    """Ensure the agent's model settings include an Ollama-appropriate timeout.

    If *timeout* is provided and the agent's ``model_settings`` does not
    already carry one, inject it into ``extra_args["timeout"]``.
    """
    if timeout is None:
        return
    ms = getattr(agent, "model_settings", None)
    if ms is None:
        return
    extra_args = getattr(ms, "extra_args", None) or {}
    if "timeout" not in extra_args:
        extra_args["timeout"] = timeout
        ms.extra_args = extra_args


def run_with_retry(
    agent: Any,
    input_data: str | list[dict[str, Any]],
    *,
    max_turns: int = 50,
    run_config: Any = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    timeout: float | None = DEFAULT_OLLAMA_TIMEOUT,
) -> Any:
    """Run ``Runner.run_sync`` with retry on ``ModelBehaviorError`` for Ollama.

    On each retry, a corrective user message is appended to the input so
    the model sees which tools are actually available.
    """
    _ensure_ollama_timeout(agent, timeout)
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
    timeout: float | None = DEFAULT_OLLAMA_TIMEOUT,
) -> Any:
    """Async version of :func:`run_with_retry` using ``await Runner.run``."""
    _ensure_ollama_timeout(agent, timeout)
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
    timeout: float | None = DEFAULT_OLLAMA_TIMEOUT,
) -> Any:
    """Run ``Runner.run_streamed`` with retry on ``ModelBehaviorError``.

    Returns a :class:`_RetryableStreamResult` that seamlessly re-creates
    the stream on error.
    """
    _ensure_ollama_timeout(agent, timeout)
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

    if last_error:
        raise last_error
    return result  # type: ignore[possibly-undefined]


class _RetryableStreamResult:
    """Wrapper around a streamed RunResult that retries on ModelBehaviorError."""

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