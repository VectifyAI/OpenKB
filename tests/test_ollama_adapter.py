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
    _append_nudge,
    _build_correction_message,
    _build_nudge_message,
    _ensure_ollama_settings,
    _extract_bad_tool_name,
    _extract_tool_names,
    _has_tool_calls,
    _sanitize_ungrounded_output,
    arun_with_retry,
    is_ollama_backend,
    resolve_ollama_settings,
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
    """Tests for _ensure_ollama_settings()."""

    def test_injects_timeout_when_missing(self) -> None:
        ms = MagicMock()
        ms.extra_args = {}
        agent = MagicMock()
        agent.model_settings = ms
        _ensure_ollama_settings(agent, 300)
        assert ms.extra_args["timeout"] == 300

    def test_preserves_existing_timeout(self) -> None:
        ms = MagicMock()
        ms.extra_args = {"timeout": 600}
        agent = MagicMock()
        agent.model_settings = ms
        _ensure_ollama_settings(agent, 300)
        assert ms.extra_args["timeout"] == 600  # not overwritten

    def test_no_timeout_none(self) -> None:
        ms = MagicMock()
        ms.extra_args = {}
        agent = MagicMock()
        agent.model_settings = ms
        _ensure_ollama_settings(agent, None)
        assert "timeout" not in ms.extra_args

    def test_no_model_settings(self) -> None:
        agent = MagicMock(spec=[])  # no model_settings
        _ensure_ollama_settings(agent, 300)  # should not raise


class TestRunWithRetry:
    """Tests for run_with_retry() and arun_with_retry()."""

    def test_succeeds_first_try(self) -> None:
        """If Runner.run_sync succeeds, no retry needed."""
        from agents.items import ToolCallItem

        mock_result = MagicMock()
        mock_result.final_output = "answer"
        mock_result.new_items = [MagicMock(spec=ToolCallItem)]  # has tool calls

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
        from agents.items import ToolCallItem

        mock_result = MagicMock()
        mock_result.final_output = "recovered answer"
        mock_result.new_items = [MagicMock(spec=ToolCallItem)]  # has tool calls

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
        from agents.items import ToolCallItem

        mock_result = MagicMock()
        mock_result.final_output = "answer"
        mock_result.new_items = [MagicMock(spec=ToolCallItem)]  # has tool calls

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
        from agents.items import ToolCallItem

        mock_result = MagicMock()
        mock_result.final_output = "recovered"
        mock_result.new_items = [MagicMock(spec=ToolCallItem)]  # has tool calls

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
        from agents.items import ToolCallItem

        mock_result = MagicMock()
        mock_result.new_items = [MagicMock(spec=ToolCallItem)]  # has tool calls
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
        from agents.items import ToolCallItem

        ms = MagicMock()
        ms.extra_args = {}
        tool = MagicMock()
        tool.name = "read_file"
        agent = MagicMock()
        agent.tools = [tool]
        agent.model_settings = ms

        mock_result = MagicMock()
        mock_result.new_items = [MagicMock(spec=ToolCallItem)]
        with patch("openkb.agent.ollama_adapter.Runner.run_sync", return_value=mock_result):
            run_with_retry(agent, "question", max_turns=5, timeout=600)

        assert ms.extra_args["timeout"] == 600


class TestResolveOllamaSettings:
    """Tests for resolve_ollama_settings()."""

    def test_empty_config_returns_defaults(self) -> None:
        max_retries, timeout = resolve_ollama_settings({})
        assert max_retries == DEFAULT_MAX_RETRIES
        assert timeout == DEFAULT_OLLAMA_TIMEOUT

    def test_none_config_returns_defaults(self) -> None:
        max_retries, timeout = resolve_ollama_settings(None)  # type: ignore[arg-type]
        assert max_retries == DEFAULT_MAX_RETRIES
        assert timeout == DEFAULT_OLLAMA_TIMEOUT

    def test_ollama_timeout(self) -> None:
        config = {"ollama": {"timeout": 600}}
        max_retries, timeout = resolve_ollama_settings(config)
        assert timeout == 600.0
        assert max_retries == DEFAULT_MAX_RETRIES

    def test_ollama_tool_call_retries(self) -> None:
        config = {"ollama": {"tool_call_retries": 5}}
        max_retries, timeout = resolve_ollama_settings(config)
        assert max_retries == 5
        assert timeout == DEFAULT_OLLAMA_TIMEOUT

    def test_both_settings(self) -> None:
        config = {"ollama": {"timeout": 900, "tool_call_retries": 7}}
        max_retries, timeout = resolve_ollama_settings(config)
        assert max_retries == 7
        assert timeout == 900.0

    def test_ollama_not_dict(self) -> None:
        config = {"ollama": "not a dict"}
        max_retries, timeout = resolve_ollama_settings(config)
        assert max_retries == DEFAULT_MAX_RETRIES
        assert timeout == DEFAULT_OLLAMA_TIMEOUT

    def test_negative_timeout_ignored(self) -> None:
        config = {"ollama": {"timeout": -1}}
        max_retries, timeout = resolve_ollama_settings(config)
        assert timeout == DEFAULT_OLLAMA_TIMEOUT

    def test_zero_timeout_ignored(self) -> None:
        config = {"ollama": {"timeout": 0}}
        max_retries, timeout = resolve_ollama_settings(config)
        assert timeout == DEFAULT_OLLAMA_TIMEOUT

    def test_negative_retries_ignored(self) -> None:
        config = {"ollama": {"tool_call_retries": -3}}
        max_retries, timeout = resolve_ollama_settings(config)
        assert max_retries == DEFAULT_MAX_RETRIES

    def test_invalid_timeout_type(self) -> None:
        config = {"ollama": {"timeout": "not a number"}}
        max_retries, timeout = resolve_ollama_settings(config)
        assert timeout == DEFAULT_OLLAMA_TIMEOUT

    def test_invalid_retries_type(self) -> None:
        config = {"ollama": {"tool_call_retries": "not a number"}}
        max_retries, timeout = resolve_ollama_settings(config)
        assert max_retries == DEFAULT_MAX_RETRIES

    def test_timeout_as_string(self) -> None:
        config = {"ollama": {"timeout": "600"}}
        max_retries, timeout = resolve_ollama_settings(config)
        assert timeout == 600.0

    def test_retries_as_string(self) -> None:
        config = {"ollama": {"tool_call_retries": "5"}}
        max_retries, timeout = resolve_ollama_settings(config)
        assert max_retries == 5

    def test_falls_back_to_process_timeout(self) -> None:
        """When ollama.timeout is not set, fall back to get_timeout()."""
        from openkb.config import set_timeout
        set_timeout(1200.0)
        try:
            max_retries, timeout = resolve_ollama_settings({})
            assert timeout == 1200.0
        finally:
            set_timeout(None)

    def test_ollama_timeout_overrides_process_timeout(self) -> None:
        """ollama.timeout takes precedence over process-wide timeout."""
        from openkb.config import set_timeout
        set_timeout(1200.0)
        try:
            config = {"ollama": {"timeout": 600}}
            max_retries, timeout = resolve_ollama_settings(config)
            assert timeout == 600.0
        finally:
            set_timeout(None)



class TestHasToolCalls:
    """Tests for _has_tool_calls()."""

    def test_with_tool_call_item(self) -> None:
        """Result with ToolCallItem returns True."""
        from agents.items import ToolCallItem
        from unittest.mock import MagicMock

        tool_item = MagicMock(spec=ToolCallItem)
        result = MagicMock()
        result.new_items = [tool_item]
        assert _has_tool_calls(result) is True

    def test_without_tool_call_item(self) -> None:
        """Result with only MessageOutputItem returns False."""
        from unittest.mock import MagicMock

        result = MagicMock()
        result.new_items = [MagicMock(), MagicMock()]
        # None of the items are ToolCallItem instances
        assert _has_tool_calls(result) is False

    def test_empty_new_items(self) -> None:
        """Result with empty new_items returns False."""
        from unittest.mock import MagicMock

        result = MagicMock()
        result.new_items = []
        assert _has_tool_calls(result) is False

    def test_no_new_items_attr(self) -> None:
        """Result without new_items attribute returns False."""
        from unittest.mock import MagicMock

        result = MagicMock(spec=[])  # no new_items
        assert _has_tool_calls(result) is False


class TestBuildNudgeMessage:
    """Tests for _build_nudge_message()."""

    def test_attempt_1_contains_read_file(self) -> None:
        msg = _build_nudge_message(["read_file", "get_page_content"], 1)
        assert "read_file" in msg
        assert "summaries/index.md" in msg

    def test_attempt_2_more_forceful(self) -> None:
        msg = _build_nudge_message(["read_file"], 2)
        assert "MUST" in msg
        assert "Do NOT" in msg

    def test_attempt_3_most_forceful(self) -> None:
        msg = _build_nudge_message(["read_file", "get_image"], 3)
        assert "IMPORTANT" in msg
        assert "read_file" in msg
        assert "get_image" in msg

    def test_contains_available_tools(self) -> None:
        msg = _build_nudge_message(["read_file", "search"], 2)
        assert "read_file" in msg
        assert "search" in msg

    def test_empty_tools(self) -> None:
        msg = _build_nudge_message([], 1)
        # Should still produce a message
        assert len(msg) > 0


class TestAppendNudge:
    """Tests for _append_nudge()."""

    def test_string_input(self) -> None:
        result = _append_nudge("original question", "nudge message")
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["content"] == "original question"
        assert result[1]["content"] == "nudge message"

    def test_list_input(self) -> None:
        original = [
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "answer"},
        ]
        result = _append_nudge(original, "nudge")
        assert len(result) == 3
        assert result[2]["content"] == "nudge"
        assert result[2]["role"] == "user"

    def test_preserves_original(self) -> None:
        original = [{"role": "user", "content": "question"}]
        _append_nudge(original, "nudge")
        assert len(original) == 1  # not modified in place


class TestNoToolsCalledRetry:
    """Tests for the no-tools-called retry mechanism in arun_with_retry."""

    @pytest.mark.asyncio
    async def test_nudges_when_no_tools_called(self) -> None:
        """If model returns answer without tool calls, retry with nudge."""
        from unittest.mock import MagicMock, AsyncMock

        # First result: no tool calls, has final_output
        result_no_tools = MagicMock()
        result_no_tools.new_items = []  # no ToolCallItem
        result_no_tools.final_output = "I cannot find relevant information"

        # Second result: has tool calls (recovered)
        from agents.items import ToolCallItem
        result_with_tools = MagicMock()
        result_with_tools.new_items = [MagicMock(spec=ToolCallItem)]
        result_with_tools.final_output = "grounded answer"

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
                return result_no_tools
            return result_with_tools

        with patch("openkb.agent.ollama_adapter.Runner.run", side_effect=mock_run):
            result = await arun_with_retry(agent, "question", max_turns=5, max_retries=3)

        assert call_count[0] == 2
        assert result == result_with_tools

    @pytest.mark.asyncio
    async def test_returns_ungrounded_after_max_retries(self) -> None:
        """If all retries exhausted with no tools, return last result."""
        from unittest.mock import MagicMock

        result_no_tools = MagicMock()
        result_no_tools.new_items = []
        result_no_tools.final_output = "I cannot find info"

        ms = MagicMock()
        ms.extra_args = {}
        tool = MagicMock()
        tool.name = "read_file"
        agent = MagicMock()
        agent.tools = [tool]
        agent.model_settings = ms

        async def mock_run(*args: Any, **kwargs: Any) -> Any:
            return result_no_tools

        with patch("openkb.agent.ollama_adapter.Runner.run", side_effect=mock_run):
            result = await arun_with_retry(agent, "question", max_turns=5, max_retries=2)

        # Should return the ungrounded result after max_retries+1 attempts
        assert result == result_no_tools

    @pytest.mark.asyncio
    async def test_no_nudge_when_tools_called(self) -> None:
        """If model calls tools, no nudge retry needed."""
        from unittest.mock import MagicMock
        from agents.items import ToolCallItem

        result_with_tools = MagicMock()
        result_with_tools.new_items = [MagicMock(spec=ToolCallItem)]
        result_with_tools.final_output = "grounded answer"

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
            return result_with_tools

        with patch("openkb.agent.ollama_adapter.Runner.run", side_effect=mock_run):
            result = await arun_with_retry(agent, "question", max_turns=5, max_retries=3)

        assert call_count[0] == 1  # no retry needed
        assert result == result_with_tools

    @pytest.mark.asyncio
    async def test_no_nudge_when_agent_has_no_tools(self) -> None:
        """If agent has no tools, no nudge retry (nothing to nudge toward)."""
        from unittest.mock import MagicMock

        result = MagicMock()
        result.new_items = []
        result.final_output = "answer"

        ms = MagicMock()
        ms.extra_args = {}
        agent = MagicMock()
        agent.tools = []  # no tools
        agent.model_settings = ms

        call_count = [0]

        async def mock_run(*args: Any, **kwargs: Any) -> Any:
            call_count[0] += 1
            return result

        with patch("openkb.agent.ollama_adapter.Runner.run", side_effect=mock_run):
            r = await arun_with_retry(agent, "question", max_turns=5, max_retries=3)

        assert call_count[0] == 1  # no retry
        assert r == result

    @pytest.mark.asyncio
    async def test_nudge_message_escalates(self) -> None:
        """Verify nudge messages get more forceful with each attempt."""
        from unittest.mock import MagicMock

        result_no_tools = MagicMock()
        result_no_tools.new_items = []
        result_no_tools.final_output = "no info"

        ms = MagicMock()
        ms.extra_args = {}
        tool = MagicMock()
        tool.name = "read_file"
        agent = MagicMock()
        agent.tools = [tool]
        agent.model_settings = ms

        captured_inputs: list[Any] = []

        async def mock_run(agent: Any, input_data: Any, **kwargs: Any) -> Any:
            captured_inputs.append(input_data)
            return result_no_tools

        with patch("openkb.agent.ollama_adapter.Runner.run", side_effect=mock_run):
            await arun_with_retry(agent, "question", max_turns=5, max_retries=3)

        # Should have 4 calls: original + 3 nudges (max_retries=3, so 1+3=4)
        assert len(captured_inputs) == 4
        # Check that nudge messages escalate
        nudge1 = captured_inputs[1][-1]["content"]  # type: ignore[index]
        nudge2 = captured_inputs[2][-1]["content"]  # type: ignore[index]
        nudge3 = captured_inputs[3][-1]["content"]  # type: ignore[index]
        assert "summaries/index.md" in nudge1
        assert "MUST" in nudge2
        assert "IMPORTANT" in nudge3


class TestSanitizeUngroundedOutput:
    """Tests for _sanitize_ungrounded_output()."""

    def test_replaces_empty_output(self) -> None:
        from unittest.mock import MagicMock

        result = MagicMock()
        result.final_output = ""
        sanitized = _sanitize_ungrounded_output(result)
        assert "could not find" in str(sanitized.final_output).lower()

    def test_replaces_raw_json_object(self) -> None:
        from unittest.mock import MagicMock

        result = MagicMock()
        result.final_output = '{"name": "read_file", "arguments": {"path": "index.md"}}'
        sanitized = _sanitize_ungrounded_output(result)
        assert "could not find" in str(sanitized.final_output).lower()
        assert "{" not in str(sanitized.final_output)

    def test_replaces_raw_json_array(self) -> None:
        from unittest.mock import MagicMock

        result = MagicMock()
        result.final_output = '[{"type": "function", "function": {"name": "read_file"}}]'
        sanitized = _sanitize_ungrounded_output(result)
        assert "could not find" in str(sanitized.final_output).lower()

    def test_preserves_reasonable_text(self) -> None:
        from unittest.mock import MagicMock

        result = MagicMock()
        result.final_output = "I cannot find relevant information about that topic."
        sanitized = _sanitize_ungrounded_output(result)
        # Should not be replaced — it's a real text answer, not raw JSON
        assert sanitized.final_output == "I cannot find relevant information about that topic."

    def test_preserves_grounded_answer(self) -> None:
        from unittest.mock import MagicMock

        result = MagicMock()
        result.final_output = "The topics discussed are Infrastructure, AI Models, and Security."
        sanitized = _sanitize_ungrounded_output(result)
        assert sanitized.final_output == "The topics discussed are Infrastructure, AI Models, and Security."

    def test_none_output(self) -> None:
        from unittest.mock import MagicMock

        result = MagicMock()
        result.final_output = None
        sanitized = _sanitize_ungrounded_output(result)
        # Should return result unchanged when output is None
        assert sanitized == result

    @pytest.mark.asyncio
    async def test_arun_returns_sanitized_when_exhausted(self) -> None:
        """When retries exhausted with raw JSON, return sanitized message."""
        from unittest.mock import MagicMock

        result_no_tools = MagicMock()
        result_no_tools.new_items = []
        result_no_tools.final_output = '{"tool": "read_file", "path": "index.md"}'

        ms = MagicMock()
        ms.extra_args = {}
        tool = MagicMock()
        tool.name = "read_file"
        agent = MagicMock()
        agent.tools = [tool]
        agent.model_settings = ms

        async def mock_run(*args: Any, **kwargs: Any) -> Any:
            return result_no_tools

        with patch("openkb.agent.ollama_adapter.Runner.run", side_effect=mock_run):
            result = await arun_with_retry(agent, "question", max_turns=5, max_retries=2)

        # Should NOT return raw JSON — should return sanitized message
        output = str(result.final_output)
        assert "{" not in output
        assert "could not find" in output.lower()