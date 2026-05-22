"""Tests for the deck critic agent + snapshot/restore safety hooks."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from openkb.deck.critic import (
    build_deck_critic_agent,
    restore_pre_critique,
    snapshot_pre_critique,
)


def test_build_critic_agent_shape(tmp_path: Path):
    wiki_root = tmp_path / "wiki"
    wiki_root.mkdir()
    (wiki_root / "AGENTS.md").write_text("# wiki schema placeholder", encoding="utf-8")
    deck_root = tmp_path / "deck"
    deck_root.mkdir()
    agent = build_deck_critic_agent(
        wiki_root=str(wiki_root),
        deck_root=str(deck_root),
        intent="A deck about transformers.",
        model="openai/gpt-4o",
    )
    assert agent.name == "deck-critic"
    # 8 tools: 5 wiki-read + write_deck_file + read_deck_file + done
    assert len(agent.tools) == 8
    tool_names = {getattr(t, "name", "?") for t in agent.tools}
    assert "read_deck_file" in tool_names
    assert "write_deck_file" in tool_names


def test_snapshot_creates_pre_critique_copy(tmp_path: Path):
    deck_root = tmp_path / "deck"
    deck_root.mkdir()
    (deck_root / "index.html").write_text("<html>v1</html>", encoding="utf-8")
    snapshot_pre_critique(deck_root)
    assert (deck_root / "index.pre-critique.html").read_text() == "<html>v1</html>"


def test_snapshot_missing_html_raises(tmp_path: Path):
    deck_root = tmp_path / "deck"
    deck_root.mkdir()
    with pytest.raises(FileNotFoundError):
        snapshot_pre_critique(deck_root)


def test_restore_recovers_index(tmp_path: Path):
    deck_root = tmp_path / "deck"
    deck_root.mkdir()
    (deck_root / "index.pre-critique.html").write_text("<html>v1</html>", encoding="utf-8")
    (deck_root / "index.html").write_text("<html>broken-by-critic</html>", encoding="utf-8")
    restore_pre_critique(deck_root)
    assert (deck_root / "index.html").read_text() == "<html>v1</html>"


def test_restore_idempotent_when_no_snapshot(tmp_path: Path):
    deck_root = tmp_path / "deck"
    deck_root.mkdir()
    (deck_root / "index.html").write_text("<html>only-version</html>", encoding="utf-8")
    # No pre-critique file. Should be a no-op, not an error.
    restore_pre_critique(deck_root)
    assert (deck_root / "index.html").read_text() == "<html>only-version</html>"


def test_cleanup_removes_snapshot(tmp_path: Path):
    deck_root = tmp_path / "deck"
    deck_root.mkdir()
    (deck_root / "index.html").write_text("<html>v2</html>", encoding="utf-8")
    (deck_root / "index.pre-critique.html").write_text("<html>v1</html>", encoding="utf-8")
    from openkb.deck.critic import cleanup_pre_critique
    cleanup_pre_critique(deck_root)
    assert not (deck_root / "index.pre-critique.html").exists()
    # index.html (the live deck) is untouched
    assert (deck_root / "index.html").read_text() == "<html>v2</html>"


def test_cleanup_idempotent_when_no_snapshot(tmp_path: Path):
    deck_root = tmp_path / "deck"
    deck_root.mkdir()
    (deck_root / "index.html").write_text("<html>v1</html>", encoding="utf-8")
    from openkb.deck.critic import cleanup_pre_critique
    cleanup_pre_critique(deck_root)  # No snapshot to clean — no exception.
    assert (deck_root / "index.html").read_text() == "<html>v1</html>"
