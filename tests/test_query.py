"""Tests for openkb.agent.query (Task 11)."""
from __future__ import annotations

import io
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openkb.agent.query import build_query_agent, run_query
from openkb.schema import SCHEMA_MD


class TestBuildQueryAgent:
    def test_agent_name(self, tmp_path):
        agent = build_query_agent(str(tmp_path), "gpt-4o-mini")
        assert agent.name == "wiki-query"

    def test_agent_has_three_tools(self, tmp_path):
        agent = build_query_agent(str(tmp_path), "gpt-4o-mini")
        assert len(agent.tools) == 3

    def test_agent_tool_names(self, tmp_path):
        agent = build_query_agent(str(tmp_path), "gpt-4o-mini")
        names = {t.name for t in agent.tools}
        assert "read_file" in names
        assert "get_page_content" in names
        assert "get_image" in names

    def test_instructions_mention_get_page_content(self, tmp_path):
        agent = build_query_agent(str(tmp_path), "gpt-4o-mini")
        assert "get_page_content" in agent.instructions
        assert "pageindex_retrieve" not in agent.instructions

    def test_schema_in_instructions(self, tmp_path):
        agent = build_query_agent(str(tmp_path), "gpt-4o-mini")
        assert SCHEMA_MD in agent.instructions

    def test_agent_model(self, tmp_path):
        agent = build_query_agent(str(tmp_path), "my-model")
        assert agent.model == "litellm/my-model"


class TestRunQuery:
    @pytest.mark.asyncio
    async def test_run_query_returns_final_output(self, tmp_path):
        (tmp_path / "wiki").mkdir()
        (tmp_path / ".openkb").mkdir()

        mock_result = MagicMock()
        mock_result.final_output = "The answer is 42."

        with patch("openkb.agent.query.Runner.run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = mock_result
            answer = await run_query("What is the answer?", tmp_path, "gpt-4o-mini")

        assert answer == "The answer is 42."

    @pytest.mark.asyncio
    async def test_run_query_passes_question_to_agent(self, tmp_path):
        (tmp_path / "wiki").mkdir()
        (tmp_path / ".openkb").mkdir()

        captured = {}

        async def fake_run(agent, message, **kwargs):
            captured["message"] = message
            return MagicMock(final_output="answer")

        with patch("openkb.agent.query.Runner.run", side_effect=fake_run):
            await run_query("How does attention work?", tmp_path, "gpt-4o-mini")

        assert "How does attention work?" in captured["message"]


def test_query_strategy_mentions_entities():
    """Task 10: query agent must direct who/what questions to entities/."""
    from openkb.agent import query as query_mod

    text = query_mod._QUERY_INSTRUCTIONS_TEMPLATE
    assert "entities/" in text


class TestFmtFallback:
    """Regression tests for issue #34.

    `_fmt` must not invoke prompt_toolkit's `print_formatted_text` when the
    output stream cannot drive a console (non-TTY stdout, or `NO_COLOR=1`).
    On Windows, `print_formatted_text` constructs a `Win32Output` that
    requires a real console handle and crashes with `NoConsoleScreenBufferError`
    when stdout is a pipe, file, or captured subprocess stream.
    """

    @staticmethod
    def _boom(*_args, **_kwargs):
        raise AssertionError(
            "print_formatted_text must not run when output is not a TTY"
        )

    def test_fmt_falls_back_when_stdout_is_not_tty(self, monkeypatch):
        from openkb.agent import chat

        monkeypatch.setattr(chat, "print_formatted_text", self._boom)
        buf = io.StringIO()  # StringIO.isatty() returns False
        monkeypatch.setattr(sys, "stdout", buf)

        style = chat._build_style(use_color=False)
        chat._fmt(style, ("class:tool", "hello"), ("class:tool", " world\n"))

        assert buf.getvalue() == "hello world\n"

    def test_fmt_falls_back_when_no_color_env(self, monkeypatch):
        from openkb.agent import chat

        monkeypatch.setattr(chat, "print_formatted_text", self._boom)

        fake_tty = io.StringIO()
        fake_tty.isatty = lambda: True  # type: ignore[method-assign]
        monkeypatch.setattr(sys, "stdout", fake_tty)
        monkeypatch.setenv("NO_COLOR", "1")

        style = chat._build_style(use_color=False)
        chat._fmt(style, ("class:error", "boom\n"))

        assert fake_tty.getvalue() == "boom\n"

    def test_fmt_uses_prompt_toolkit_on_real_tty(self, monkeypatch):
        from openkb.agent import chat

        called = {"count": 0}

        def fake_print(*_args, **_kwargs):
            called["count"] += 1

        monkeypatch.setattr(chat, "print_formatted_text", fake_print)

        fake_tty = io.StringIO()
        fake_tty.isatty = lambda: True  # type: ignore[method-assign]
        monkeypatch.setattr(sys, "stdout", fake_tty)
        monkeypatch.delenv("NO_COLOR", raising=False)

        style = chat._build_style(use_color=True)
        chat._fmt(style, ("class:header", "hi\n"))

        assert called["count"] == 1
        assert fake_tty.getvalue() == ""
