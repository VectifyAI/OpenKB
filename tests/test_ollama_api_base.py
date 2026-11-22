"""Tests for Ollama api_base propagation (commit 9f1e1013).

The ``ollama_chat`` LiteLLM provider does not read ``OPENAI_API_BASE``, so
``compiler._llm_call`` / ``_llm_call_async`` must pass ``api_base`` explicitly
from the environment when no credential bundle is set, and importing
``openkb.agent.ollama_adapter`` must propagate ``OPENAI_API_BASE`` to
``OLLAMA_API_BASE`` + ``litellm.api_base`` as a module-level side effect.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _fake_response(text: str = "ok") -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = text
    mock_resp.usage = MagicMock(prompt_tokens=100, completion_tokens=50)
    mock_resp.usage.prompt_tokens_details = None
    return mock_resp


MESSAGES = [{"role": "user", "content": "hi"}]


class TestLlmCallApiBase:
    def test_api_base_from_openai_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from openkb.agent.compiler import _llm_call

        monkeypatch.setenv("OPENAI_API_BASE", "http://ollama-env:11434")
        monkeypatch.delenv("OLLAMA_API_BASE", raising=False)
        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.completion = MagicMock(return_value=_fake_response())
            out = _llm_call("ollama_chat/gemma4:12b", MESSAGES, "summary")

        assert out == "ok"
        kwargs = mock_litellm.completion.call_args.kwargs
        assert kwargs["api_base"] == "http://ollama-env:11434"

    def test_api_base_falls_back_to_ollama_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from openkb.agent.compiler import _llm_call

        monkeypatch.delenv("OPENAI_API_BASE", raising=False)
        monkeypatch.setenv("OLLAMA_API_BASE", "http://ollama-only:11434")
        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.completion = MagicMock(return_value=_fake_response())
            _llm_call("ollama_chat/gemma4:12b", MESSAGES, "summary")

        kwargs = mock_litellm.completion.call_args.kwargs
        assert kwargs["api_base"] == "http://ollama-only:11434"

    def test_no_env_no_api_base(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from openkb.agent.compiler import _llm_call

        monkeypatch.delenv("OPENAI_API_BASE", raising=False)
        monkeypatch.delenv("OLLAMA_API_BASE", raising=False)
        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.completion = MagicMock(return_value=_fake_response())
            _llm_call("ollama_chat/gemma4:12b", MESSAGES, "summary")

        assert "api_base" not in mock_litellm.completion.call_args.kwargs

    def test_explicit_api_base_not_overridden(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from openkb.agent.compiler import _llm_call

        monkeypatch.setenv("OPENAI_API_BASE", "http://env:1")
        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.completion = MagicMock(return_value=_fake_response())
            _llm_call("ollama_chat/gemma4:12b", MESSAGES, "summary", api_base="http://explicit:2")

        kwargs = mock_litellm.completion.call_args.kwargs
        assert kwargs["api_base"] == "http://explicit:2"

    def test_bundle_wins_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from openkb.agent.compiler import _llm_call

        monkeypatch.setenv("OPENAI_API_BASE", "http://env:1")
        bundle = MagicMock()
        bundle.api_key = "kb-key"
        bundle.base_url = "http://kb-llm:9000"
        bundle.extra_headers = {}
        bundle.timeout = None
        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.completion = MagicMock(return_value=_fake_response())
            _llm_call("ollama_chat/gemma4:12b", MESSAGES, "summary", bundle=bundle)

        kwargs = mock_litellm.completion.call_args.kwargs
        assert kwargs["base_url"] == "http://kb-llm:9000"
        assert kwargs["api_key"] == "kb-key"
        assert "api_base" not in kwargs

    @pytest.mark.asyncio
    async def test_async_api_base_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from openkb.agent.compiler import _llm_call_async

        monkeypatch.setenv("OPENAI_API_BASE", "http://ollama-env:11434")
        monkeypatch.delenv("OLLAMA_API_BASE", raising=False)
        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.acompletion = AsyncMock(return_value=_fake_response())
            out = await _llm_call_async("ollama_chat/gemma4:12b", MESSAGES, "summary")

        assert out == "ok"
        kwargs = mock_litellm.acompletion.call_args.kwargs
        assert kwargs["api_base"] == "http://ollama-env:11434"

    @pytest.mark.asyncio
    async def test_async_bundle_wins_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from openkb.agent.compiler import _llm_call_async

        monkeypatch.setenv("OPENAI_API_BASE", "http://env:1")
        bundle = MagicMock()
        bundle.api_key = "kb-key"
        bundle.base_url = "http://kb-llm:9000"
        bundle.extra_headers = {}
        bundle.timeout = None
        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.acompletion = AsyncMock(return_value=_fake_response())
            await _llm_call_async("ollama_chat/gemma4:12b", MESSAGES, "summary", bundle=bundle)

        kwargs = mock_litellm.acompletion.call_args.kwargs
        assert kwargs["base_url"] == "http://kb-llm:9000"
        assert "api_base" not in kwargs


class TestModuleImportSideEffect:
    """Importing the adapter propagates OPENAI_API_BASE -> OLLAMA_API_BASE
    and litellm.api_base (the add path relies on this). Verified in a clean
    subprocess so module-level state can't leak between tests."""

    def test_import_propagates_openai_api_base(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        code = (
            "import json, litellm, os; "
            "import openkb.agent.ollama_adapter; "
            "print(json.dumps({'ollama': os.environ.get('OLLAMA_API_BASE'), "
            "'litellm': getattr(litellm, 'api_base', None)}))"
        )
        env = dict(os.environ)
        env["OPENAI_API_BASE"] = "http://import-test:11434"
        env.pop("OLLAMA_API_BASE", None)
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=180,
            cwd=repo_root,
            env=env,
        )
        assert proc.returncode == 0, proc.stderr
        data = json.loads(proc.stdout.strip().splitlines()[-1])
        assert data["ollama"] == "http://import-test:11434"
        assert data["litellm"] == "http://import-test:11434"
