"""Tests for openkb.agent.compiler."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openkb.agent.compiler import (
    build_compiler_agent,
    compile_long_doc,
    compile_short_doc,
)
from openkb.schema import SCHEMA_MD


class TestBuildCompilerAgent:
    def test_agent_name(self, tmp_path):
        agent = build_compiler_agent(str(tmp_path), "gpt-4o-mini")
        assert agent.name == "wiki-compiler"

    def test_agent_tools_count(self, tmp_path):
        agent = build_compiler_agent(str(tmp_path), "gpt-4o-mini")
        # list_files, read_file, write_file
        assert len(agent.tools) == 3

    def test_schema_in_instructions(self, tmp_path):
        agent = build_compiler_agent(str(tmp_path), "gpt-4o-mini")
        assert SCHEMA_MD in agent.instructions

    def test_agent_model(self, tmp_path):
        agent = build_compiler_agent(str(tmp_path), "my-custom-model")
        assert agent.model == "litellm/my-custom-model"

    def test_tool_names(self, tmp_path):
        agent = build_compiler_agent(str(tmp_path), "gpt-4o-mini")
        tool_names = {t.name for t in agent.tools}
        assert "list_files" in tool_names
        assert "read_file" in tool_names
        assert "write_file" in tool_names


class TestCompileShortDoc:
    @pytest.mark.asyncio
    async def test_calls_runner_run(self, tmp_path):
        # Create a source file
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        source_path = wiki_dir / "sources" / "my_doc.md"
        source_path.parent.mkdir(parents=True)
        source_path.write_text("# My Doc\n\nSome content.", encoding="utf-8")

        # Create .openkb dir for agent build
        openkb_dir = tmp_path / ".openkb"
        openkb_dir.mkdir()

        mock_result = MagicMock()
        mock_result.final_output = "Done"

        with patch("openkb.agent.compiler.Runner.run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = mock_result
            await compile_short_doc("my_doc", source_path, tmp_path, "gpt-4o-mini")

        mock_run.assert_called_once()
        call_args = mock_run.call_args
        agent_arg = call_args[0][0]
        message_arg = call_args[0][1]

        assert agent_arg.name == "wiki-compiler"
        assert "my_doc" in message_arg
        assert "Some content." in message_arg
        assert "Generate summary" in message_arg

    @pytest.mark.asyncio
    async def test_message_contains_doc_name_and_content(self, tmp_path):
        wiki_dir = tmp_path / "wiki"
        source_path = wiki_dir / "sources" / "test_paper.md"
        source_path.parent.mkdir(parents=True)
        source_path.write_text("# Test Paper\n\nKey findings here.", encoding="utf-8")

        (tmp_path / ".openkb").mkdir()

        captured = {}

        async def fake_run(agent, message, **kwargs):
            captured["message"] = message
            return MagicMock(final_output="ok")

        with patch("openkb.agent.compiler.Runner.run", side_effect=fake_run):
            await compile_short_doc("test_paper", source_path, tmp_path, "gpt-4o-mini")

        assert "test_paper" in captured["message"]
        assert "Key findings here." in captured["message"]


class TestCompileLongDoc:
    @pytest.mark.asyncio
    async def test_calls_runner_run(self, tmp_path):
        wiki_dir = tmp_path / "wiki"
        summary_path = wiki_dir / "summaries" / "big_doc.md"
        summary_path.parent.mkdir(parents=True)
        summary_path.write_text("# Big Doc Summary\n\nSection tree.", encoding="utf-8")

        openkb_dir = tmp_path / ".openkb"
        openkb_dir.mkdir()
        # Write minimal config
        (openkb_dir / "config.yaml").write_text("model: gpt-4o-mini\n")

        mock_result = MagicMock()
        mock_result.final_output = "Done"

        with patch("openkb.agent.compiler.Runner.run", new_callable=AsyncMock) as mock_run, \
             patch("openkb.agent.compiler.PageIndexClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            mock_run.return_value = mock_result

            await compile_long_doc(
                "big_doc", summary_path, "doc-abc123", tmp_path, "gpt-4o-mini"
            )

        mock_run.assert_called_once()
        call_args = mock_run.call_args
        message_arg = call_args[0][1]

        assert "big_doc" in message_arg
        assert "doc-abc123" in message_arg
        assert "Do NOT regenerate summary" in message_arg

    @pytest.mark.asyncio
    async def test_long_doc_agent_has_four_tools(self, tmp_path):
        wiki_dir = tmp_path / "wiki"
        summary_path = wiki_dir / "summaries" / "big.md"
        summary_path.parent.mkdir(parents=True)
        summary_path.write_text("Summary content", encoding="utf-8")

        openkb_dir = tmp_path / ".openkb"
        openkb_dir.mkdir()
        (openkb_dir / "config.yaml").write_text("model: gpt-4o-mini\n")

        captured_agent = {}

        async def fake_run(agent, message, **kwargs):
            captured_agent["agent"] = agent
            return MagicMock(final_output="ok")

        with patch("openkb.agent.compiler.Runner.run", side_effect=fake_run), \
             patch("openkb.agent.compiler.PageIndexClient") as mock_client_cls:
            mock_client_cls.return_value = MagicMock()

            await compile_long_doc(
                "big", summary_path, "doc-xyz", tmp_path, "gpt-4o-mini"
            )

        agent = captured_agent["agent"]
        assert len(agent.tools) == 4
        tool_names = {t.name for t in agent.tools}
        assert "get_page_content" in tool_names
