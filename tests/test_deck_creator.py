"""Tests for the deck-create agent builder + runner.

No real LLM calls — Runner.run is patched. We verify:
  * the Agent is built with the right name, prompt, tool count
  * critique=False produces an agent with no handoffs
  * run_deck_create raises if index.html is not written
  * run_deck_create raises with a helpful message on MaxTurnsExceeded
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openkb.deck.creator import build_deck_create_agent, run_deck_create


def _build(tmp_path: Path):
    wiki_root = tmp_path / "wiki"
    wiki_root.mkdir()
    # AGENTS.md is consulted by get_agents_md; an empty file is fine.
    (wiki_root / "AGENTS.md").write_text("# wiki schema placeholder", encoding="utf-8")
    deck_root = tmp_path / "output" / "decks" / "test-deck"
    return wiki_root, deck_root


def test_build_agent_shape(tmp_path: Path):
    wiki_root, deck_root = _build(tmp_path)
    agent = build_deck_create_agent(
        wiki_root=str(wiki_root),
        deck_root=str(deck_root),
        deck_name="test-deck",
        intent="A test deck about transformers.",
        model="openai/gpt-4o",
        critique=False,
    )
    assert agent.name == "deck-creator"
    # 7 tools: list_wiki_dir, read_wiki_file, get_page_content,
    # get_image, query_wiki, write_deck_file, done
    assert len(agent.tools) == 7
    tool_names = {getattr(t, "name", "?") for t in agent.tools}
    assert "write_deck_file" in tool_names
    assert "done" in tool_names
    # No handoffs when critique=False
    assert getattr(agent, "handoffs", []) in ([], None)


def test_build_agent_creates_output_dir(tmp_path: Path):
    wiki_root, deck_root = _build(tmp_path)
    assert not deck_root.exists()
    build_deck_create_agent(
        wiki_root=str(wiki_root),
        deck_root=str(deck_root),
        deck_name="test-deck",
        intent="A test deck.",
        model="openai/gpt-4o",
        critique=False,
    )
    assert deck_root.is_dir()


def test_run_raises_when_html_missing(tmp_path: Path):
    wiki_root, _ = _build(tmp_path)
    kb_dir = tmp_path

    with patch("openkb.deck.creator.Runner") as runner:
        runner.run = AsyncMock(return_value=MagicMock())
        import asyncio
        with pytest.raises(RuntimeError, match="did not write index.html"):
            asyncio.run(
                run_deck_create(
                    kb_dir=kb_dir,
                    deck_name="test-deck",
                    intent="A test deck.",
                    model="openai/gpt-4o",
                    critique=False,
                )
            )


def test_run_translates_maxturns(tmp_path: Path):
    wiki_root, _ = _build(tmp_path)
    kb_dir = tmp_path
    from agents.exceptions import MaxTurnsExceeded

    with patch("openkb.deck.creator.Runner") as runner:
        runner.run = AsyncMock(side_effect=MaxTurnsExceeded("nope"))
        import asyncio
        with pytest.raises(RuntimeError, match="step cap"):
            asyncio.run(
                run_deck_create(
                    kb_dir=kb_dir,
                    deck_name="test-deck",
                    intent="A test deck.",
                    model="openai/gpt-4o",
                    critique=False,
                )
            )
