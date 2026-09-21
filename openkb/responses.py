"""Native OpenAI Responses API transport for Responses-only models.

Some gateways serve certain models exclusively on the ``/v1/responses``
endpoint and answer ``/v1/chat/completions`` with ``503 Endpoint is
unavailable``. OpenKB talks to LLMs through LiteLLM Chat Completions, so
those models are unreachable — unless the KB opts in here by prefixing
the model id::

    model: openai-responses/muse-spark-1.3-contributor

The prefix is an OpenKB-only convention (LiteLLM never sees it). It is
recognized at the two places a model string is consumed:

- :func:`openkb.agent.compiler._llm_call` / ``_llm_call_async`` — routed
  to :func:`litellm.responses` / :func:`litellm.aresponses`, with the
  result adapted back to the Chat Completions shape the compiler reads.
- :func:`openkb.agent.query.build_run_config_from_bundle` — builds an
  agents-SDK ``OpenAIResponsesModel`` over a custom ``AsyncOpenAI``
  client (per-KB ``api_key`` / ``base_url`` / ``extra_headers``), so
  query/chat/lint/skills — including ``run_streamed`` — work unchanged.

Only ``openai-responses/`` (OpenAI-compatible bases) is supported.
Long-PDF PageIndex indexing still requires a Chat Completions model
(see :mod:`openkb.indexer` guard); short documents, query, chat, lint,
skills, and decks all work.
"""

from __future__ import annotations

import logging
import os
from types import SimpleNamespace
from typing import Any

import litellm
from agents.models.openai_responses import OpenAIResponsesModel
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

RESPONSES_PREFIX = "openai-responses/"

#: Chat Completions kwargs with no Responses API equivalent — dropped
#: (debug-logged) instead of failing the call.
_DROPPED_PARAMS = frozenset(
    {
        "stop",
        "frequency_penalty",
        "presence_penalty",
        "seed",
        "logprobs",
        "top_logprobs",
        "logit_bias",
        "n",
        "stream_options",
        "service_tier",
        "prompt_cache_key",
    }
)


def is_responses_model(model: str) -> bool:
    """Return True when *model* opts into the Responses API transport."""
    return model.startswith(RESPONSES_PREFIX)


def split_responses_model(model: str) -> tuple[str, str]:
    """Split ``openai-responses/<id>`` into (litellm_model, bare_id).

    ``litellm_model`` (``openai/<id>``) is what LiteLLM's ``responses()``
    needs for provider routing; ``bare_id`` is what the Agents SDK and
    the upstream endpoint expect as the model name.
    """
    bare_id = model[len(RESPONSES_PREFIX) :]
    return f"openai/{bare_id}", bare_id


def messages_to_responses_input(messages: list[dict]) -> list[dict]:
    """Translate Chat Completions ``messages`` to Responses ``input`` items.

    Plain user/assistant/system/developer messages pass through. Assistant
    messages carrying ``tool_calls`` become ``function_call`` items (plus a
    message item when they also have text); ``tool`` messages become
    ``function_call_output`` items. Content blocks (``type: text`` /
    ``image_url``) are preserved as-is.
    """
    items: list[dict] = []
    for msg in messages:
        role = msg.get("role")
        if role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": msg.get("tool_call_id", ""),
                    "output": _stringify_content(msg.get("content", "")),
                }
            )
            continue
        if role == "assistant" and msg.get("tool_calls"):
            if msg.get("content"):
                items.append({"role": "assistant", "content": msg["content"]})
            for tc in msg["tool_calls"]:
                fn = tc.get("function", {})
                items.append(
                    {
                        "type": "function_call",
                        "call_id": tc.get("id", ""),
                        "name": fn.get("name", ""),
                        "arguments": fn.get("arguments", "{}"),
                    }
                )
            continue
        content = msg.get("content")
        if content is None:
            continue  # e.g. reasoning-only placeholder, nothing to send
        if isinstance(content, list):
            content = [
                _translate_content_block(b, for_assistant=(role == "assistant")) for b in content
            ]
        items.append({"role": role, "content": content})
    return items


def _translate_content_block(block: Any, *, for_assistant: bool = False) -> Any:
    """Map one Chat Completions content block to Responses input shape.

    ``text`` -> ``input_text`` (or ``output_text`` inside ``assistant``
    messages — the endpoint rejects ``input_text`` there), ``image_url``
    -> ``input_image`` (lifting ``url``/``detail``); any residual provider
    markers such as ``cache_control`` are stripped. Unknown block types
    pass through untouched rather than being silently dropped.
    """
    if not isinstance(block, dict):
        return block
    kind = block.get("type")
    if kind == "text":
        return {
            "type": "output_text" if for_assistant else "input_text",
            "text": block.get("text", ""),
        }
    if kind == "image_url":
        image = block.get("image_url", {})
        if isinstance(image, str):
            return {"type": "input_image", "image_url": image}
        converted: dict[str, Any] = {"type": "input_image"}
        if image.get("url") is not None:
            converted["image_url"] = image["url"]
        if image.get("detail") is not None:
            converted["detail"] = image["detail"]
        return converted
    return {k: v for k, v in block.items() if k != "cache_control"}


def _stringify_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        ]
        return "".join(parts)
    return str(content)


def response_format_to_text(response_format: Any) -> dict | None:
    """Map a Chat Completions ``response_format`` to Responses ``text``."""
    if not isinstance(response_format, dict):
        return None
    kind = response_format.get("type")
    if kind == "json_object":
        return {"format": {"type": "json_object"}}
    if kind == "json_schema":
        schema = response_format.get("json_schema", {})
        return {
            "format": {
                "type": "json_schema",
                "name": schema.get("name", "response"),
                "schema": schema.get("schema", {"type": "object"}),
                "strict": schema.get("strict", False),
            }
        }
    return None


def extract_output_text(response: Any) -> tuple[str, bool]:
    """Pull assistant text out of a Responses API object.

    Returns ``(text, is_incomplete)``; ``is_incomplete`` mirrors the
    Chat Completions ``finish_reason == "length"`` signal.
    """
    output = getattr(response, "output", None) or []
    if isinstance(output, dict):
        output = [output]
    parts: list[str] = []
    for item in output:
        as_dict = item if isinstance(item, dict) else _to_dict(item)
        if as_dict.get("type") != "message":
            continue
        for block in as_dict.get("content", []) or []:
            b = block if isinstance(block, dict) else _to_dict(block)
            if b.get("type") == "output_text":
                parts.append(b.get("text", ""))
    status = getattr(response, "status", "completed")
    return "".join(parts), status == "incomplete"


def _to_dict(obj: Any) -> dict:
    if isinstance(obj, dict):
        return obj
    for attr in ("model_dump", "to_dict"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                return fn()
            except Exception:  # noqa: BLE001
                pass
    return getattr(obj, "__dict__", {})


def adapt_response(response: Any) -> SimpleNamespace:
    """Adapt a Responses API object to the Chat Completions shape.

    The compiler reads ``response.choices[0].message.content``,
    ``response.choices[0].finish_reason``, and
    ``response.usage.{prompt_tokens,completion_tokens}`` — this adapter
    provides exactly that surface so call sites need no further changes.
    """
    text, incomplete = extract_output_text(response)
    usage = getattr(response, "usage", None) or SimpleNamespace()
    adapted_usage = SimpleNamespace(
        prompt_tokens=getattr(usage, "input_tokens", 0),
        completion_tokens=getattr(usage, "output_tokens", 0),
        total_tokens=getattr(usage, "total_tokens", 0),
        prompt_tokens_details=None,
    )
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=text or ""),
                finish_reason="length" if incomplete else "stop",
            )
        ],
        usage=adapted_usage,
    )


def build_responses_params(model: str, messages: list[dict], **kwargs: Any) -> tuple[str, dict]:
    """Build ``(litellm_model, params)`` for :func:`litellm.aresponses`.

    ``max_tokens``/``max_completion_tokens`` become ``max_output_tokens``,
    ``response_format`` becomes ``text``, unsupported params are dropped
    with a debug log. ``api_key``/``base_url``/``extra_headers``/``timeout``
    pass straight through to LiteLLM.
    """
    litellm_model, _ = split_responses_model(model)
    params: dict[str, Any] = {"input": messages_to_responses_input(messages)}
    for key, value in kwargs.items():
        if value is None:
            continue
        if key in _DROPPED_PARAMS:
            logger.debug("Responses API: dropping unsupported param %r", key)
            continue
        if key in ("max_tokens", "max_completion_tokens", "max_output_tokens"):
            params.setdefault("max_output_tokens", value)
        elif key == "response_format":
            text = response_format_to_text(value)
            if text is not None:
                params["text"] = text
        elif key in (
            "temperature",
            "top_p",
            "tools",
            "tool_choice",
            "parallel_tool_calls",
            "api_key",
            "base_url",
            "extra_headers",
            "timeout",
            "user",
            "metadata",
        ):
            params[key] = value
        else:
            # Unknown but possibly supported (e.g. reasoning) — forward it
            # and let the provider decide rather than failing here.
            params[key] = value
    return litellm_model, params


def responses_completion(model: str, messages: list[dict], **kwargs: Any) -> SimpleNamespace:
    """Sync Chat-Completions-shaped call over the Responses API."""
    litellm_model, params = build_responses_params(model, messages, **kwargs)
    return adapt_response(litellm.responses(model=litellm_model, **params))


async def aresponses_completion(model: str, messages: list[dict], **kwargs: Any) -> SimpleNamespace:
    """Async Chat-Completions-shaped call over the Responses API."""
    litellm_model, params = build_responses_params(model, messages, **kwargs)
    return adapt_response(await litellm.aresponses(model=litellm_model, **params))


def responses_agent_model(model: str, bundle: Any = None) -> Any:
    """Return an Agents-SDK model instance for Responses models, else None.

    Callers use it as ``model=(responses_agent_model(model, bundle) or
    <existing default>)`` so Chat Completions behavior is untouched.
    Credentials come from *bundle*, else ``LLM_API_KEY``/
    ``OPENAI_API_BASE`` env (legacy CLI path).
    """
    if not is_responses_model(model):
        return None
    if bundle is not None:
        return build_agents_responses_model(
            model,
            api_key=bundle.api_key,
            base_url=bundle.base_url,
            extra_headers=bundle.extra_headers,
            timeout=bundle.timeout,
        )
    return build_agents_responses_model(model)


def _env_credentials() -> tuple[str | None, str | None]:
    """Fall back to process env for the bundle=None (legacy CLI) path."""
    api_key = (
        os.environ.get("LLM_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("OPENCODE_API_KEY")
        or None
    )
    return api_key, os.environ.get("OPENAI_API_BASE")


def build_agents_responses_model(
    model: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    extra_headers: dict | None = None,
    timeout: float | None = None,
) -> Any:
    """Build an Agents-SDK ``OpenAIResponsesModel`` for a Responses model.

    Uses a dedicated ``AsyncOpenAI`` client so per-KB credentials never
    touch process-global state. When credentials are omitted (legacy CLI
    path), they fall back to ``LLM_API_KEY``/``OPENAI_API_BASE`` env vars.
    """
    _, bare_id = split_responses_model(model)
    if api_key is None or base_url is None:
        env_key, env_base = _env_credentials()
        api_key = api_key or env_key
        base_url = base_url or env_base
    client_kwargs: dict[str, Any] = {"api_key": api_key, "base_url": base_url}
    if extra_headers:
        client_kwargs["default_headers"] = dict(extra_headers)
    if timeout is not None:
        client_kwargs["timeout"] = timeout
    client = AsyncOpenAI(**client_kwargs)
    return OpenAIResponsesModel(model=bare_id, openai_client=client)
