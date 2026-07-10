"""Tests for openkb.agent.linter (Task 14)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openkb.agent.linter import build_lint_agent, run_knowledge_lint
from openkb.schema import SCHEMA_MD


class TestBuildLintAgent:
    def test_agent_name(self, tmp_path):
        agent = build_lint_agent(str(tmp_path), "gpt-4o-mini")
        assert agent.name == "wiki-linter"

    def test_agent_has_two_tools(self, tmp_path):
        agent = build_lint_agent(str(tmp_path), "gpt-4o-mini")
        assert len(agent.tools) == 2

    def test_agent_tool_names(self, tmp_path):
        agent = build_lint_agent(str(tmp_path), "gpt-4o-mini")
        names = {t.name for t in agent.tools}
        assert "list_files" in names
        assert "read_file" in names

    def test_schema_in_instructions(self, tmp_path):
        agent = build_lint_agent(str(tmp_path), "gpt-4o-mini")
        assert SCHEMA_MD in agent.instructions

    def test_agent_model(self, tmp_path):
        agent = build_lint_agent(str(tmp_path), "custom-model")
        assert agent.model == "litellm/custom-model"

    def test_instructions_mention_contradictions(self, tmp_path):
        agent = build_lint_agent(str(tmp_path), "gpt-4o-mini")
        assert "Contradictions" in agent.instructions or "contradictions" in agent.instructions

    def test_instructions_mention_gaps(self, tmp_path):
        agent = build_lint_agent(str(tmp_path), "gpt-4o-mini")
        assert "Gaps" in agent.instructions or "gaps" in agent.instructions


class TestLintAgentParallelToolCalls:
    """Lint never sent parallel_tool_calls before, so its default stays "omit"
    (sending any value, incl. False, breaks Bedrock #175). Explicit config wins.
    """

    def test_unset_omits_the_setting(self, tmp_path):
        from openkb.config import set_parallel_tool_calls

        set_parallel_tool_calls(None, False)  # not configured
        agent = build_lint_agent(str(tmp_path), "bedrock/eu.anthropic.claude-sonnet-4-6")
        assert agent.model_settings.parallel_tool_calls is None

    def test_explicit_null_omits(self, tmp_path):
        from openkb.config import set_parallel_tool_calls

        set_parallel_tool_calls(None, True)  # explicit null
        agent = build_lint_agent(str(tmp_path), "gpt-4o-mini")
        assert agent.model_settings.parallel_tool_calls is None

    def test_explicit_bools_flow_through(self, tmp_path):
        from openkb.config import set_parallel_tool_calls

        set_parallel_tool_calls(True, True)
        assert build_lint_agent(str(tmp_path), "m").model_settings.parallel_tool_calls is True
        set_parallel_tool_calls(False, True)
        assert build_lint_agent(str(tmp_path), "m").model_settings.parallel_tool_calls is False


class TestRunKnowledgeLint:
    @pytest.mark.asyncio
    async def test_returns_final_output(self, tmp_path):
        (tmp_path / "wiki").mkdir()

        mock_result = MagicMock()
        mock_result.final_output = "## Lint Report\n\nNo issues found."

        with patch("openkb.agent.linter.Runner.run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = mock_result
            result = await run_knowledge_lint(tmp_path, "gpt-4o-mini")

        assert "No issues found" in result

    @pytest.mark.asyncio
    async def test_calls_runner_with_correct_agent(self, tmp_path):
        (tmp_path / "wiki").mkdir()

        captured = {}

        async def fake_run(agent, message, **kwargs):
            captured["agent"] = agent
            return MagicMock(final_output="report")

        with patch("openkb.agent.linter.Runner.run", side_effect=fake_run):
            await run_knowledge_lint(tmp_path, "gpt-4o-mini")

        assert captured["agent"].name == "wiki-linter"

    @pytest.mark.asyncio
    async def test_handles_empty_final_output(self, tmp_path):
        (tmp_path / "wiki").mkdir()

        mock_result = MagicMock()
        mock_result.final_output = None

        with patch("openkb.agent.linter.Runner.run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = mock_result
            result = await run_knowledge_lint(tmp_path, "gpt-4o-mini")

        assert "completed" in result.lower() or result != ""
