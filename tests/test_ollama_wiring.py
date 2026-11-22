"""Tests for Ollama wiring in openkb.agent.query (commits 9f1e1013/06d875ec).

run_query / iter_agent_response_events route Ollama models through the retry
adapter (arun_with_retry / run_streamed_with_retry) and rewrite
``ollama/`` -> ``ollama_chat/`` at the model entry points.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


async def _empty_events() -> Any:
    """Async generator that yields nothing."""
    return
    yield  # pragma: no cover — makes this an async generator


class TestBuildQueryAgentModelRewrite:
    def test_ollama_rewritten(self, tmp_path: Any) -> None:
        from openkb.agent.query import build_query_agent

        agent = build_query_agent(str(tmp_path), "ollama/gemma4:12b")
        assert agent.model == "litellm/ollama_chat/gemma4:12b"

    def test_ollama_chat_unchanged(self, tmp_path: Any) -> None:
        from openkb.agent.query import build_query_agent

        agent = build_query_agent(str(tmp_path), "ollama_chat/gemma4:12b")
        assert agent.model == "litellm/ollama_chat/gemma4:12b"

    def test_non_ollama_unchanged(self, tmp_path: Any) -> None:
        from openkb.agent.query import build_query_agent

        agent = build_query_agent(str(tmp_path), "openai/gpt-4o")
        assert agent.model == "litellm/openai/gpt-4o"


class TestBuildRunConfigFromBundle:
    def test_bundle_none_returns_none(self) -> None:
        from openkb.agent.query import build_run_config_from_bundle

        assert build_run_config_from_bundle("ollama/gemma4:12b", None) is None

    def test_ollama_model_rewritten(self) -> None:
        from openkb.agent.query import build_run_config_from_bundle

        bundle = MagicMock()
        bundle.api_key = "test-key"
        bundle.base_url = "http://kb-llm:8000"
        with patch("agents.extensions.models.litellm_model.LitellmModel") as mock_lm:
            rc = build_run_config_from_bundle("ollama/gemma4:12b", bundle)

        kwargs = mock_lm.call_args.kwargs
        assert kwargs["model"] == "ollama_chat/gemma4:12b"
        assert kwargs["base_url"] == "http://kb-llm:8000"
        assert kwargs["api_key"] == "test-key"
        assert rc.model is mock_lm.return_value

    def test_non_ollama_model_verbatim(self) -> None:
        from openkb.agent.query import build_run_config_from_bundle

        bundle = MagicMock()
        bundle.api_key = "test-key"
        bundle.base_url = "http://kb-llm:8000"
        with patch("agents.extensions.models.litellm_model.LitellmModel") as mock_lm:
            build_run_config_from_bundle("openai/gpt-4o", bundle)

        assert mock_lm.call_args.kwargs["model"] == "openai/gpt-4o"


class TestRunQueryRouting:
    @staticmethod
    def _prep_dirs(tmp_path: Any) -> None:
        (tmp_path / "wiki").mkdir()
        (tmp_path / ".openkb").mkdir()

    @pytest.mark.asyncio
    async def test_ollama_uses_arun_with_retry(self, tmp_path: Any) -> None:
        from openkb.agent.query import run_query

        self._prep_dirs(tmp_path)
        mock_result = MagicMock()
        mock_result.final_output = "grounded answer"

        with (
            patch("openkb.agent.query.arun_with_retry", new_callable=AsyncMock) as mock_arun,
            patch("openkb.agent.query.Runner.run", new_callable=AsyncMock) as mock_bare,
        ):
            mock_arun.return_value = mock_result
            answer = await run_query("Q?", tmp_path, "ollama/gemma4:12b")

        assert answer == "grounded answer"
        mock_arun.assert_awaited_once()
        mock_bare.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_ollama_uses_bare_runner(self, tmp_path: Any) -> None:
        from openkb.agent.query import run_query

        self._prep_dirs(tmp_path)
        mock_result = MagicMock()
        mock_result.final_output = "plain answer"

        with (
            patch("openkb.agent.query.arun_with_retry", new_callable=AsyncMock) as mock_arun,
            patch("openkb.agent.query.Runner.run", new_callable=AsyncMock) as mock_bare,
        ):
            mock_bare.return_value = mock_result
            answer = await run_query("Q?", tmp_path, "gpt-4o-mini")

        assert answer == "plain answer"
        mock_bare.assert_awaited_once()
        mock_arun.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ollama_stream_uses_run_streamed_with_retry(self, tmp_path: Any) -> None:
        from openkb.agent.query import run_query

        self._prep_dirs(tmp_path)
        mock_stream = MagicMock()
        mock_stream.final_output = "streamed answer"
        mock_stream.stream_events = lambda: _empty_events()

        with (
            patch(
                "openkb.agent.query.run_streamed_with_retry",
                return_value=mock_stream,
            ) as mock_swr,
            patch("openkb.agent.query.Runner.run_streamed") as mock_bare,
        ):
            answer = await run_query("Q?", tmp_path, "ollama/gemma4:12b", stream=True)

        assert answer == "streamed answer"
        mock_swr.assert_called_once()
        mock_bare.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_ollama_stream_uses_bare_runner(self, tmp_path: Any) -> None:
        from openkb.agent.query import run_query

        self._prep_dirs(tmp_path)
        mock_stream = MagicMock()
        mock_stream.final_output = "streamed answer"
        mock_stream.stream_events = lambda: _empty_events()

        with (
            patch(
                "openkb.agent.query.run_streamed_with_retry",
                new_callable=MagicMock,
            ) as mock_swr,
            patch("openkb.agent.query.Runner.run_streamed", return_value=mock_stream) as mock_bare,
        ):
            answer = await run_query("Q?", tmp_path, "gpt-4o-mini", stream=True)

        assert answer == "streamed answer"
        mock_bare.assert_called_once()
        mock_swr.assert_not_called()

    @pytest.mark.asyncio
    async def test_iter_events_uses_run_streamed_with_retry_for_ollama(
        self,
        tmp_path: Any,
    ) -> None:
        from openkb.agent.query import build_query_agent, iter_agent_response_events

        self._prep_dirs(tmp_path)
        agent = build_query_agent(str(tmp_path / "wiki"), "ollama/gemma4:12b")
        mock_stream = MagicMock()
        mock_stream.final_output = "ans"
        mock_stream.stream_events = lambda: _empty_events()

        with patch(
            "openkb.agent.query.run_streamed_with_retry",
            return_value=mock_stream,
        ) as mock_swr:
            events = [event async for event in iter_agent_response_events(agent, "q")]

        mock_swr.assert_called_once()
        assert events
        assert events[-1]["event"] == "final"
