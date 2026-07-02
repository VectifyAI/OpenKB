"""The configurable LLM request timeout is forwarded to LiteLLM.

`timeout:` in config.yaml is resolved into a process-wide stash (see
test_config.py) and read at the LiteLLM call sites in openkb.agent.compiler.
These tests pin the call-site behavior: a configured timeout is forwarded to
`litellm.(a)completion`, and nothing is forwarded when it is unset (so LiteLLM
keeps applying its own default).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from openkb.agent.compiler import _llm_call, _llm_call_async
from openkb.config import set_timeout


def _fake_response():
    choice = MagicMock()
    choice.message.content = "ok"
    choice.finish_reason = "stop"
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def test_llm_call_forwards_configured_timeout():
    set_timeout(1200.0)
    with patch(
        "openkb.agent.compiler.litellm.completion", return_value=_fake_response()
    ) as completion:
        _llm_call("gpt-4o", [{"role": "user", "content": "hi"}], "step")
    assert completion.call_args.kwargs["timeout"] == 1200.0


def test_llm_call_omits_timeout_when_unset():
    set_timeout(None)
    with patch(
        "openkb.agent.compiler.litellm.completion", return_value=_fake_response()
    ) as completion:
        _llm_call("gpt-4o", [{"role": "user", "content": "hi"}], "step")
    assert "timeout" not in completion.call_args.kwargs


def test_llm_call_does_not_override_explicit_timeout():
    # An explicit per-call timeout kwarg wins over the configured default.
    set_timeout(1200.0)
    with patch(
        "openkb.agent.compiler.litellm.completion", return_value=_fake_response()
    ) as completion:
        _llm_call("gpt-4o", [{"role": "user", "content": "hi"}], "step", timeout=30)
    assert completion.call_args.kwargs["timeout"] == 30


def test_llm_call_async_forwards_configured_timeout():
    set_timeout(900.0)
    with patch(
        "openkb.agent.compiler.litellm.acompletion",
        new_callable=AsyncMock,
        return_value=_fake_response(),
    ) as acompletion:
        asyncio.run(_llm_call_async("gpt-4o", [{"role": "user", "content": "hi"}], "step"))
    assert acompletion.call_args.kwargs["timeout"] == 900.0


def test_llm_call_async_omits_timeout_when_unset():
    set_timeout(None)
    with patch(
        "openkb.agent.compiler.litellm.acompletion",
        new_callable=AsyncMock,
        return_value=_fake_response(),
    ) as acompletion:
        asyncio.run(_llm_call_async("gpt-4o", [{"role": "user", "content": "hi"}], "step"))
    assert "timeout" not in acompletion.call_args.kwargs


def test_llm_call_async_does_not_override_explicit_timeout():
    set_timeout(900.0)
    with patch(
        "openkb.agent.compiler.litellm.acompletion",
        new_callable=AsyncMock,
        return_value=_fake_response(),
    ) as acompletion:
        asyncio.run(
            _llm_call_async("gpt-4o", [{"role": "user", "content": "hi"}], "step", timeout=30)
        )
    assert acompletion.call_args.kwargs["timeout"] == 30
