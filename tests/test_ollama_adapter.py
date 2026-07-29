"""Tests for the Ollama tool-call resilient adapter."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from openkb.agent.ollama_adapter import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_OLLAMA_TIMEOUT,
    _append_correction,
    _build_correction_message,
    _ensure_ollama_timeout,
    _extract_bad_tool_name,
    _extract_tool_names,
    arun_with_retry,
    is_ollama_backend,
    rewrite_ollama_model,
    run_with_retry,
)


class TestIsOllamaBackend:
    """Tests for is_ollama_backend()."""

    def test_litellm_prefix(self) -> None:
        assert is_ollama_backend("ollama/llama3.2:1b") is True

    def test_ollama_chat_prefix(self) -> None:
        assert is_ollama_backend("ollama_chat/gemma4:12b") is True

    def test_agent_layer_prefix(self) -> None:
        assert is_ollama_backend("litellm/ollama/llama3.2:1b") is True

    def test_agent_layer_ollama_chat(self) -> None:
        assert is_ollama_backend("litellm/ollama_chat/gemma4:12b") is True

    def test_non_ollama(self) -> None:
        assert is_ollama_backend("openai/gpt-4o") is False

    def test_empty(self) -> None:
        assert is_ollama_backend("") is False

    def test_none_safe(self) -> None:
        assert is_ollama_backend(None) is False  # type: ignore[arg-type]

    def test_case_insensitive(self) -> None:
        assert is_ollama_backend("OLLAMA/Llama3.2:1b") is True


class TestRewriteOllamaModel:
    """Tests for rewrite_ollama_model()."""

    def test_ollama_to_ollama_chat(self) -> None:
        assert rewrite_ollama_model("ollama/gemma4:12b") == "ollama_chat/gemma4:12b"

    def test_ollama_with_colon_tag(self) -> None:
        assert rewrite_ollama_model("ollama/llama3.1:8b-instruct-q8_0") == "ollama_chat/llama3.1:8b-instruct-q8_0"

    def test_already_ollama_chat(self) -> None:
        assert rewrite_ollama_model("ollama_chat/gemma4:12b") == "ollama_chat/gemma4:12b"

    def test_litellm_ollama_prefix(self) -> None:
        assert rewrite_ollama_model("litellm/ollama/gemma4:12b") == "ollama_chat/gemma4:12b"

    def test_litellm_ollama_chat_prefix(self) -> None:
        assert rewrite_ollama_model("litellm/ollama_chat/gemma4:12b") == "ollama_chat/gemma4:12b"

    def test_non_ollama_unchanged(self) -> None:
        assert rewrite_ollama_model("openai/gpt-4o") == "openai/gpt-4o"

    def test_empty_unchanged(self) -> None:
        assert rewrite_ollama_model("") == ""

    def test_none_safe(self) -> None:
        assert rewrite_ollama_model(None) is None  # type: ignore[arg-type]


class TestExtractToolNames:
    """Tests for _extract_tool_names()."""

    def test_with_function_tools(self) -> None:
        tool1 = MagicMock()
        tool1.name = "read_file"
        tool2 = MagicMock()
        tool2.name = "get_page_content"
        agent = MagicMock()
        agent.tools = [tool1, tool2]
        assert _extract_tool_names(agent) == ["read_file", "get_page_content"]

    def test_empty_tools(self) -> None:
        agent = MagicMock()
        agent.tools = []
        assert _extract_tool_names(agent) == []

    def test_no_tools_attr(self) -> None:
        agent = MagicMock(spec=[])  # no tools attribute
        assert _extract_tool_names(agent) == []

    def test_raw_functions(self) -> None:
        def my_tool() -> str:
            """A tool."""

        agent = MagicMock()
        agent.tools = [my_tool]
        assert _extract_tool_names(agent) == ["my_tool"]


class TestExtractBadToolName:
    """Tests for _extract_bad_tool_name()."""

    def test_standard_pattern(self) -> None:
        exc = Exception("Tool search_strategy not found in agent wiki-query")
        assert _extract_bad_tool_name(exc) == "search_strategy"  # type: ignore[arg-type]

    def test_no_match(self) -> None:
        exc = Exception("Some other error message")
        assert _extract_bad_tool_name(exc) == "unknown"  # type: ignore[arg-type]

    def test_empty(self) -> None:
        exc = Exception("")
        assert _extract_bad_tool_name(exc) == "unknown"  # type: ignore[arg-type]


class TestBuildCorrectionMessage:
    """Tests for _build_correction_message()."""

    def test_contains_bad_name(self) -> None:
        msg = _build_correction_message("get_topics", ["read_file", "get_image"], 1)
        assert "get_topics" in msg

    def test_contains_available_tools(self) -> None:
        msg = _build_correction_message("get_topics", ["read_file", "get_image"], 1)
        assert "read_file" in msg
        assert "get_image" in msg

    def test_contains_attempt_number(self) -> None:
        msg = _build_correction_message("get_topics", ["read_file"], 2)
        assert "2" in msg

    def test_empty_tools(self) -> None:
        msg = _build_correction_message("bad_tool", [], 1)
        assert "(none)" in msg


class TestAppendCorrection:
    """Tests for _append_correction()."""

    def test_string_input(self) -> None:
        result = _append_correction("original question", "correction")
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["content"] == "original question"
        assert result[1]["content"] == "correction"

    def test_list_input(self) -> None:
        original = [
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "answer"},
        ]
        result = _append_correction(original, "correction")
        assert len(result) == 3
        assert result[2]["content"] == "correction"
        assert result[2]["role"] == "user"

    def test_preserves_original(self) -> None:
        original = [{"role": "user", "content": "question"}]
        _append_correction(original, "correction")
        assert len(original) == 1  # not modified in place


class TestEnsureOllamaTimeout:
    """Tests for _ensure_ollama_timeout()."""

    def test_injects_timeout_when_missing(self) -> None:
        ms = MagicMock()
        ms.extra_args = {}
        agent = MagicMock()
        agent.model_settings = ms
        _ensure_ollama_timeout(agent, 300)
        assert ms.extra_args["timeout"] == 300

    def test_preserves_existing_timeout(self) -> None:
        ms = MagicMock()
        ms.extra_args = {"timeout": 600}
        agent = MagicMock()
        agent.model_settings = ms
        _ensure_ollama_timeout(agent, 300)
        assert ms.extra_args["timeout"] == 600  # not overwritten

    def test_no_timeout_none(self) -> None:
        ms = MagicMock()
        ms.extra_args = {}
        agent = MagicMock()
        agent.model_settings = ms
        _ensure_ollama_timeout(agent, None)
        assert "timeout" not in ms.extra_args

    def test_no_model_settings(self) -> None:
        agent = MagicMock(spec=[])  # no model_settings
        _ensure_ollama_timeout(agent, 300)  # should not raise


class TestRunWithRetry:
    """Tests for run_with_retry() and arun_with_retry()."""

    def test_succeeds_first_try(self) -> None:
        """If Runner.run_sync succeeds, no retry needed."""
        mock_result = MagicMock()
        mock_result.final_output = "answer"

        ms = MagicMock()
        ms.extra_args = {}
        agent = MagicMock()
        agent.tools = [MagicMock(name="read_file")]
        agent.model_settings = ms

        with patch("openkb.agent.ollama_adapter.Runner.run_sync", return_value=mock_result):
            result = run_with_retry(agent, "question", max_turns=5)
        assert result == mock_result

    def test_retries_on_model_behavior_error(self) -> None:
        """If Runner.run_sync raises ModelBehaviorError, retry."""
        from agents.exceptions import ModelBehaviorError

        mock_result = MagicMock()
        mock_result.final_output = "recovered answer"

        ms = MagicMock()
        ms.extra_args = {}
        tool = MagicMock()
        tool.name = "read_file"
        agent = MagicMock()
        agent.tools = [tool]
        agent.model_settings = ms

        call_count = [0]

        def mock_run(*args: Any, **kwargs: Any) -> Any:
            call_count[0] += 1
            if call_count[0] == 1:
                raise ModelBehaviorError("Tool get_topics not found in agent wiki-query")
            return mock_result

        with patch("openkb.agent.ollama_adapter.Runner.run_sync", side_effect=mock_run):
            result = run_with_retry(agent, "question", max_turns=5, max_retries=3)
        assert call_count[0] == 2
        assert result == mock_result

    def test_raises_after_max_retries(self) -> None:
        """If retries exhausted, the last error is re-raised."""
        from agents.exceptions import ModelBehaviorError

        ms = MagicMock()
        ms.extra_args = {}
        tool = MagicMock()
        tool.name = "read_file"
        agent = MagicMock()
        agent.tools = [tool]
        agent.model_settings = ms

        with patch(
            "openkb.agent.ollama_adapter.Runner.run_sync",
            side_effect=ModelBehaviorError("Tool bad not found in agent wiki-query"),
        ):
            with pytest.raises(ModelBehaviorError, match="Tool bad not found"):
                run_with_retry(agent, "question", max_turns=5, max_retries=2)

    @pytest.mark.asyncio
    async def test_arun_succeeds_first_try(self) -> None:
        """Async version: if Runner.run succeeds, no retry needed."""
        mock_result = MagicMock()
        mock_result.final_output = "answer"

        ms = MagicMock()
        ms.extra_args = {}
        tool = MagicMock()
        tool.name = "read_file"
        agent = MagicMock()
        agent.tools = [tool]
        agent.model_settings = ms

        async def mock_run(*args: Any, **kwargs: Any) -> Any:
            return mock_result

        with patch("openkb.agent.ollama_adapter.Runner.run", side_effect=mock_run):
            result = await arun_with_retry(agent, "question", max_turns=5)
        assert result == mock_result

    @pytest.mark.asyncio
    async def test_arun_retries_on_error(self) -> None:
        """Async version: retry on ModelBehaviorError."""
        from agents.exceptions import ModelBehaviorError

        mock_result = MagicMock()
        mock_result.final_output = "recovered"

        ms = MagicMock()
        ms.extra_args = {}
        tool = MagicMock()
        tool.name = "read_file"
        agent = MagicMock()
        agent.tools = [tool]
        agent.model_settings = ms

        call_count = [0]

        async def mock_run(*args: Any, **kwargs: Any) -> Any:
            call_count[0] += 1
            if call_count[0] == 1:
                raise ModelBehaviorError("Tool search_strategy not found in agent wiki-query")
            return mock_result

        with patch("openkb.agent.ollama_adapter.Runner.run", side_effect=mock_run):
            result = await arun_with_retry(agent, "question", max_turns=5, max_retries=3)
        assert call_count[0] == 2
        assert result == mock_result

    def test_correction_appended_to_input(self) -> None:
        """Verify that the correction message is added to the input on retry."""
        from agents.exceptions import ModelBehaviorError

        mock_result = MagicMock()
        ms = MagicMock()
        ms.extra_args = {}
        tool = MagicMock()
        tool.name = "read_file"
        agent = MagicMock()
        agent.tools = [tool]
        agent.model_settings = ms

        captured_inputs: list[Any] = []

        def mock_run(agent: Any, input_data: Any, **kwargs: Any) -> Any:
            captured_inputs.append(input_data)
            if len(captured_inputs) == 1:
                raise ModelBehaviorError("Tool hallucinated not found in agent wiki-query")
            return mock_result

        with patch("openkb.agent.ollama_adapter.Runner.run_sync", side_effect=mock_run):
            run_with_retry(agent, "original question", max_turns=5, max_retries=3)

        # First call gets the string, second gets a list with correction
        assert captured_inputs[0] == "original question"
        assert isinstance(captured_inputs[1], list)
        assert len(captured_inputs[1]) == 2
        assert "hallucinated" in captured_inputs[1][1]["content"]
        assert "read_file" in captured_inputs[1][1]["content"]

    def test_timeout_injected(self) -> None:
        """Verify that timeout is injected into model_settings.extra_args."""
        ms = MagicMock()
        ms.extra_args = {}
        tool = MagicMock()
        tool.name = "read_file"
        agent = MagicMock()
        agent.tools = [tool]
        agent.model_settings = ms

        mock_result = MagicMock()
        with patch("openkb.agent.ollama_adapter.Runner.run_sync", return_value=mock_result):
            run_with_retry(agent, "question", max_turns=5, timeout=600)

        assert ms.extra_args["timeout"] == 600