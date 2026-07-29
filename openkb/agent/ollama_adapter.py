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
import os
from typing import Any

from agents import Runner
from agents.exceptions import ModelBehaviorError
from agents.items import ToolCallItem

logger = logging.getLogger(__name__)

# Propagate OPENAI_API_BASE → OLLAMA_API_BASE at import time so the
# ``ollama_chat`` LiteLLM provider picks up the correct endpoint even
# for code paths that don't go through the adapter (e.g. ``openkb add``).
# LiteLLM's ollama_chat provider reads OLLAMA_API_BASE, not OPENAI_API_BASE.
_openai_base = os.environ.get("OPENAI_API_BASE")
if _openai_base:
    if not os.environ.get("OLLAMA_API_BASE"):
        os.environ["OLLAMA_API_BASE"] = _openai_base
    # Also set litellm module-level api_base so calls without explicit
    # api_base= parameter (e.g. openkb add) reach the Ollama endpoint.
    try:
        import litellm as _litellm
        if not getattr(_litellm, "api_base", None):
            _litellm.api_base = _openai_base
    except ImportError:
        pass

# Maximum retry attempts for tool-call hallucination errors.
DEFAULT_MAX_RETRIES = 3

# Default per-request timeout for Ollama models (seconds).
# Local models on modest hardware can take 60-120s per tool-calling turn.
DEFAULT_OLLAMA_TIMEOUT = 300


def resolve_ollama_settings(config: dict) -> tuple[int, float | None]:
    """Resolve Ollama-specific timeout and retry settings from config.

    Reads the optional ``ollama:`` block from ``config.yaml``::

        ollama:
          timeout: 600            # per-request timeout (s), default 300
          tool_call_retries: 5     # max retries on hallucinated tool names, default 3

    Falls back to process-wide ``get_timeout()`` (from ``litellm.timeout``
    or top-level ``timeout:``) when ``ollama.timeout`` is not set, then to
    ``DEFAULT_OLLAMA_TIMEOUT``.

    Returns ``(max_retries, timeout)``.
    """
    ollama_cfg = config.get("ollama") if config else None
    if not isinstance(ollama_cfg, dict):
        ollama_cfg = {}

    # Resolve timeout
    timeout: float | None = None
    raw_timeout = ollama_cfg.get("timeout")
    if raw_timeout is not None:
        try:
            timeout = float(raw_timeout)
            if timeout <= 0:
                timeout = None
        except (TypeError, ValueError):
            timeout = None

    if timeout is None:
        # Fall back to process-wide timeout (from litellm.timeout or top-level timeout)
        try:
            from openkb.config import get_timeout
            timeout = get_timeout()
        except ImportError:
            pass

    if timeout is None:
        timeout = DEFAULT_OLLAMA_TIMEOUT

    # Resolve max_retries
    max_retries = DEFAULT_MAX_RETRIES
    raw_retries = ollama_cfg.get("tool_call_retries")
    if raw_retries is not None:
        try:
            max_retries = int(raw_retries)
            if max_retries < 0:
                max_retries = DEFAULT_MAX_RETRIES
        except (TypeError, ValueError):
            pass

    return max_retries, timeout


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


def _has_tool_calls(result: Any) -> bool:
    """Return True if the RunResult contains any ToolCallItem.

    Used to detect when a model returned a final answer without calling
    any tools — a common failure mode for small local Ollama models that
    answer "I cannot find relevant information" without reading wiki files.
    """
    new_items = getattr(result, "new_items", []) or []
    return any(isinstance(item, ToolCallItem) for item in new_items)


def _build_nudge_message(
    available_tools: list[str],
    attempt: int,
) -> str:
    """Build an escalating nudge message for a no-tools-called retry.

    The message gets more forceful with each attempt to push the model
    to call a tool before answering.
    """
    tool_list = ", ".join(available_tools) if available_tools else "(none)"

    if attempt <= 1:
        return (
            "You answered without reading any wiki files. You have tools "
            "available. Call read_file with path 'summaries/index.md' first "
            "to see what documents exist, then answer the question."
        )
    if attempt == 2:
        return (
            "You MUST use the read_file tool. Do NOT answer without reading "
            "files first. Start with read_file(path='summaries/index.md'). "
            f"Available tools: {tool_list}."
        )
    # Attempt 3+
    return (
        f"IMPORTANT: Your previous answer was not grounded in the wiki. "
        f"Call read_file now. The available tools are: {tool_list}. "
        f"Do not answer until you have called a tool."
    )


def _append_nudge(
    input_data: str | list[dict[str, Any]],
    nudge: str,
) -> str | list[dict[str, Any]]:
    """Append a nudge message to the input data."""
    if isinstance(input_data, str):
        return [
            {"role": "user", "content": input_data},
            {"role": "user", "content": nudge},
        ]
    if isinstance(input_data, list):
        return [*input_data, {"role": "user", "content": nudge}]
    return input_data


def _sanitize_ungrounded_output(result: Any) -> Any:
    """When retries are exhausted and the model never called tools, wrap
    its raw output in a clear message so the user sees an explanation
    instead of raw tool-call JSON or an unhelpful empty string.

    Returns a new RunResult-like object with a modified final_output.
    If the output is already reasonable text, returns it unchanged.
    """
    raw = getattr(result, "final_output", None)
    if raw is None:
        return result

    text = str(raw).strip() if raw is not None else ""

    # If output looks like raw tool-call JSON (starts with { or [),
    # or is empty, replace with a clear message.
    if not text or text.startswith("{") or text.startswith("["):
        logger.warning(
            "Ollama no-tools-called: sanitizing ungrounded output "
            "(raw=%r, len=%d)",
            text[:80], len(text),
        )
        # Try to set final_output — RunResult may be frozen, so we
        # use object.__setattr__ if direct assignment fails.
        message = (
            "I could not find relevant information in the knowledge base. "
            "The available tools were not used successfully. "
            "Try rephrasing your question or adding more documents."
        )
        try:
            result.final_output = message  # type: ignore[misc]
        except (AttributeError, TypeError):
            try:
                object.__setattr__(result, "final_output", message)
            except (AttributeError, TypeError):
                pass  # best-effort; caller will see the original
    return result


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


def _ensure_ollama_settings(
    agent: Any,
    timeout: float | None,
) -> None:
    """Ensure the agent's model settings include Ollama-appropriate defaults.

    - If *timeout* is provided and the agent's ``model_settings`` does not
      already carry one, inject it into ``extra_args["timeout"]``.
    - Set ``litellm.drop_params = True`` process-wide so LiteLLM drops
      unsupported params (e.g. ``parallel_tool_calls`` for ``ollama_chat``)
      instead of raising ``UnsupportedParamsError``.
    - Propagate ``OPENAI_API_BASE`` to ``OLLAMA_API_BASE`` so the
      ``ollama_chat`` LiteLLM provider picks up the correct endpoint
      (it reads ``OLLAMA_API_BASE``, not ``OPENAI_API_BASE``).
    """
    import os

    # drop_params is critical for ollama_chat — it rejects parallel_tool_calls
    try:
        import litellm
        litellm.drop_params = True
    except ImportError:
        pass

    # ollama_chat reads OLLAMA_API_BASE, not OPENAI_API_BASE.
    # If OPENAI_API_BASE is set but OLLAMA_API_BASE is not, propagate it.
    openai_base = os.environ.get("OPENAI_API_BASE")
    if openai_base and not os.environ.get("OLLAMA_API_BASE"):
        os.environ["OLLAMA_API_BASE"] = openai_base

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
    max_retries: int | None = None,
    timeout: float | None = None,
) -> Any:
    """Run ``Runner.run_sync`` with retry on ``ModelBehaviorError`` for Ollama.

    If *max_retries* or *timeout* are ``None``, they are resolved from the
    KB config's ``ollama:`` block (or process-wide / default fallbacks).
    """
    if max_retries is None or timeout is None:
        try:
            cfg_max_retries, cfg_timeout = resolve_ollama_settings({})
        except Exception:
            cfg_max_retries, cfg_timeout = DEFAULT_MAX_RETRIES, DEFAULT_OLLAMA_TIMEOUT
        if max_retries is None:
            max_retries = cfg_max_retries
        if timeout is None:
            timeout = cfg_timeout
    _ensure_ollama_settings(agent, timeout)
    available_tools = _extract_tool_names(agent)
    current_input: str | list[dict[str, Any]] = input_data

    for attempt in range(1, max_retries + 2):  # 1..max_retries+1 (first + retries)
        try:
            if run_config:
                result = Runner.run_sync(
                    agent, current_input, max_turns=max_turns, run_config=run_config,
                )
            else:
                result = Runner.run_sync(agent, current_input, max_turns=max_turns)
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
            continue

        # Run succeeded — check if tools were actually called.
        if available_tools and not _has_tool_calls(result):
            if attempt > max_retries:
                logger.warning(
                    "Ollama no-tools-called: retries exhausted (%d), "
                    "returning ungrounded answer",
                    max_retries,
                )
                return _sanitize_ungrounded_output(result)
            logger.warning(
                "Ollama no-tools-called retry %d/%d: model answered without "
                "calling any tools, nudging",
                attempt, max_retries,
            )
            nudge = _build_nudge_message(available_tools, attempt)
            current_input = _append_nudge(current_input, nudge)
            continue

        return result

    return _sanitize_ungrounded_output(result)  # type: ignore[possibly-undefined]


async def arun_with_retry(
    agent: Any,
    input_data: str | list[dict[str, Any]],
    *,
    max_turns: int = 50,
    run_config: Any = None,
    max_retries: int | None = None,
    timeout: float | None = None,
) -> Any:
    """Async version of :func:`run_with_retry` using ``await Runner.run``.

    If *max_retries* or *timeout* are ``None``, they are resolved from the
    KB config's ``ollama:`` block (or process-wide / default fallbacks).
    """
    if max_retries is None or timeout is None:
        try:
            from openkb.config import resolve_effective_config
            from pathlib import Path
            # resolve_effective_config needs a kb_dir, but we may not have one.
            # Fall back to defaults if config is unavailable.
            cfg_max_retries, cfg_timeout = resolve_ollama_settings({})
        except Exception:
            cfg_max_retries, cfg_timeout = DEFAULT_MAX_RETRIES, DEFAULT_OLLAMA_TIMEOUT
        if max_retries is None:
            max_retries = cfg_max_retries
        if timeout is None:
            timeout = cfg_timeout
    _ensure_ollama_settings(agent, timeout)
    available_tools = _extract_tool_names(agent)
    current_input: str | list[dict[str, Any]] = input_data

    # Two retry loops: one for ModelBehaviorError (hallucinated tool names),
    # one for no-tools-called (model answered without calling any tools).
    # Both bounded by max_retries — no infinite loops.
    for attempt in range(1, max_retries + 2):
        try:
            if run_config:
                result = await Runner.run(
                    agent, current_input, max_turns=max_turns, run_config=run_config,
                )
            else:
                result = await Runner.run(agent, current_input, max_turns=max_turns)
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
            continue

        # Run succeeded — check if tools were actually called.
        if available_tools and not _has_tool_calls(result):
            if attempt > max_retries:
                logger.warning(
                    "Ollama no-tools-called: retries exhausted (%d), "
                    "returning ungrounded answer",
                    max_retries,
                )
                return _sanitize_ungrounded_output(result)
            logger.warning(
                "Ollama no-tools-called retry %d/%d: model answered without "
                "calling any tools, nudging",
                attempt, max_retries,
            )
            nudge = _build_nudge_message(available_tools, attempt)
            current_input = _append_nudge(current_input, nudge)
            continue

        return result

    # Should not reach here, but return last result if we do
    return _sanitize_ungrounded_output(result)  # type: ignore[possibly-undefined]


def run_streamed_with_retry(
    agent: Any,
    input_data: str | list[dict[str, Any]],
    *,
    max_turns: int = 50,
    run_config: Any = None,
    max_retries: int | None = None,
    timeout: float | None = None,
) -> Any:
    """Run ``Runner.run_streamed`` with retry on ``ModelBehaviorError``.

    If *max_retries* or *timeout* are ``None``, they are resolved from the
    KB config's ``ollama:`` block (or process-wide / default fallbacks).
    """
    if max_retries is None or timeout is None:
        try:
            cfg_max_retries, cfg_timeout = resolve_ollama_settings({})
        except Exception:
            cfg_max_retries, cfg_timeout = DEFAULT_MAX_RETRIES, DEFAULT_OLLAMA_TIMEOUT
        if max_retries is None:
            max_retries = cfg_max_retries
        if timeout is None:
            timeout = cfg_timeout
    _ensure_ollama_settings(agent, timeout)
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