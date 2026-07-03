"""The configurable LLM request retry count is forwarded to LiteLLM.

`num_retries:` in config.yaml is resolved into a process-wide stash (see
test_config.py) and read at the LiteLLM call sites in openkb.agent.compiler.
These tests pin the call-site behavior: a configured retry count is forwarded to
`litellm.(a)completion`, and nothing is forwarded when it is unset (so LiteLLM
keeps applying its own default).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from openkb.agent.compiler import _llm_call, _llm_call_async
from openkb.config import set_num_retries


def _fake_response():
    choice = MagicMock()
    choice.message.content = "ok"
    choice.finish_reason = "stop"
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def test_llm_call_forwards_configured_num_retries():
    set_num_retries(3)
    with patch(
        "openkb.agent.compiler.litellm.completion", return_value=_fake_response()
    ) as completion:
        _llm_call("gpt-4o", [{"role": "user", "content": "hi"}], "step")
    assert completion.call_args.kwargs["num_retries"] == 3


def test_llm_call_omits_num_retries_when_unset():
    set_num_retries(None)
    with patch(
        "openkb.agent.compiler.litellm.completion", return_value=_fake_response()
    ) as completion:
        _llm_call("gpt-4o", [{"role": "user", "content": "hi"}], "step")
    assert "num_retries" not in completion.call_args.kwargs


def test_llm_call_does_not_override_explicit_num_retries():
    # An explicit per-call num_retries kwarg wins over the configured default.
    set_num_retries(3)
    with patch(
        "openkb.agent.compiler.litellm.completion", return_value=_fake_response()
    ) as completion:
        _llm_call("gpt-4o", [{"role": "user", "content": "hi"}], "step", num_retries=5)
    assert completion.call_args.kwargs["num_retries"] == 5


def test_llm_call_async_forwards_configured_num_retries():
    set_num_retries(2)
    with patch(
        "openkb.agent.compiler.litellm.acompletion",
        new_callable=AsyncMock,
        return_value=_fake_response(),
    ) as acompletion:
        asyncio.run(_llm_call_async("gpt-4o", [{"role": "user", "content": "hi"}], "step"))
    assert acompletion.call_args.kwargs["num_retries"] == 2


def test_llm_call_async_omits_num_retries_when_unset():
    set_num_retries(None)
    with patch(
        "openkb.agent.compiler.litellm.acompletion",
        new_callable=AsyncMock,
        return_value=_fake_response(),
    ) as acompletion:
        asyncio.run(_llm_call_async("gpt-4o", [{"role": "user", "content": "hi"}], "step"))
    assert "num_retries" not in acompletion.call_args.kwargs


def test_llm_call_async_does_not_override_explicit_num_retries():
    set_num_retries(2)
    with patch(
        "openkb.agent.compiler.litellm.acompletion",
        new_callable=AsyncMock,
        return_value=_fake_response(),
    ) as acompletion:
        asyncio.run(
            _llm_call_async("gpt-4o", [{"role": "user", "content": "hi"}], "step", num_retries=5)
        )
    assert acompletion.call_args.kwargs["num_retries"] == 5
