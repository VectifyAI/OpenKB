"""Tests for openkb.agent.query (Task 11)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openkb.agent.query import _pageindex_retrieve_impl, build_query_agent, run_query
from openkb.schema import SCHEMA_MD


class TestBuildQueryAgent:
    def test_agent_name(self, tmp_path):
        agent = build_query_agent(str(tmp_path), str(tmp_path / "pi"), "gpt-4o-mini")
        assert agent.name == "wiki-query"

    def test_agent_has_three_tools(self, tmp_path):
        agent = build_query_agent(str(tmp_path), str(tmp_path / "pi"), "gpt-4o-mini")
        assert len(agent.tools) == 3

    def test_agent_tool_names(self, tmp_path):
        agent = build_query_agent(str(tmp_path), str(tmp_path / "pi"), "gpt-4o-mini")
        names = {t.name for t in agent.tools}
        assert "list_files" in names
        assert "read_file" in names
        assert "pageindex_retrieve" in names

    def test_instructions_reference_registered_pageindex_tool(self, tmp_path):
        agent = build_query_agent(str(tmp_path), str(tmp_path / "pi"), "gpt-4o-mini")
        tool_names = {t.name for t in agent.tools}
        assert "pageindex_retrieve" in agent.instructions
        assert "pageindex_retrieve" in tool_names

    def test_schema_in_instructions(self, tmp_path):
        agent = build_query_agent(str(tmp_path), str(tmp_path / "pi"), "gpt-4o-mini")
        assert SCHEMA_MD in agent.instructions

    def test_agent_model(self, tmp_path):
        agent = build_query_agent(str(tmp_path), str(tmp_path / "pi"), "my-model")
        assert agent.model == "my-model"


class TestPageindexRetrieve:
    def test_returns_page_content(self, tmp_path):
        mock_structure = [
            {
                "node_id": "n1",
                "title": "Introduction",
                "start_index": 1,
                "end_index": 5,
                "summary": "Overview section",
            }
        ]
        mock_pages = [
            {"page_index": 1, "text": "Introduction text here."},
            {"page_index": 2, "text": "More intro content."},
        ]

        mock_col = MagicMock()
        mock_col.get_document_structure.return_value = mock_structure
        mock_col.get_page_content.return_value = mock_pages

        mock_client = MagicMock()
        mock_client.collection.return_value = mock_col

        with patch("openkb.agent.query.PageIndexClient", return_value=mock_client), \
             patch("openkb.agent.query.litellm.completion") as mock_llm, \
             patch.dict("os.environ", {"PAGEINDEX_API_KEY": ""}, clear=False):
            mock_llm.return_value = MagicMock(
                choices=[MagicMock(message=MagicMock(content="1-2"))]
            )
            result = _pageindex_retrieve_impl("doc123", "What is the intro?", "/db", "gpt-4o-mini")

        assert "Introduction text here." in result
        assert "More intro content." in result

    def test_empty_structure_falls_back_to_query(self, tmp_path):
        mock_col = MagicMock()
        mock_col.get_document_structure.return_value = []
        mock_col.query.return_value = "Fallback answer from PageIndex query."

        mock_client = MagicMock()
        mock_client.collection.return_value = mock_col

        with patch("openkb.agent.query.PageIndexClient", return_value=mock_client):
            result = _pageindex_retrieve_impl("doc456", "What?", "/db", "gpt-4o-mini")

        assert "Fallback answer" in result
        mock_col.query.assert_called_once()

    def test_structure_error_falls_back_to_query(self, tmp_path):
        mock_col = MagicMock()
        mock_col.get_document_structure.side_effect = RuntimeError("DB error")
        mock_col.query.return_value = "Fallback answer."

        mock_client = MagicMock()
        mock_client.collection.return_value = mock_col

        with patch("openkb.agent.query.PageIndexClient", return_value=mock_client):
            result = _pageindex_retrieve_impl("doc789", "What?", "/db", "gpt-4o-mini")

        assert "Fallback answer" in result


class TestRunQuery:
    @pytest.mark.asyncio
    async def test_run_query_returns_final_output(self, tmp_path):
        (tmp_path / "wiki").mkdir()
        (tmp_path / ".okb").mkdir()

        mock_result = MagicMock()
        mock_result.final_output = "The answer is 42."

        with patch("openkb.agent.query.Runner.run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = mock_result
            answer = await run_query("What is the answer?", tmp_path, "gpt-4o-mini")

        assert answer == "The answer is 42."

    @pytest.mark.asyncio
    async def test_run_query_passes_question_to_agent(self, tmp_path):
        (tmp_path / "wiki").mkdir()
        (tmp_path / ".okb").mkdir()

        captured = {}

        async def fake_run(agent, message, **kwargs):
            captured["message"] = message
            return MagicMock(final_output="answer")

        with patch("openkb.agent.query.Runner.run", side_effect=fake_run):
            await run_query("How does attention work?", tmp_path, "gpt-4o-mini")

        assert "How does attention work?" in captured["message"]
