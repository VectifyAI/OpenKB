"""Tests for the /deck new slash command inside openkb chat."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from prompt_toolkit.styles import Style

from openkb.agent.chat import _handle_slash
from openkb.agent.chat_session import ChatSession


def _make_kb(tmp_path):
    (tmp_path / ".openkb").mkdir()
    (tmp_path / ".openkb" / "config.yaml").write_text("model: gpt-4o-mini\n")
    (tmp_path / ".openkb" / "chats").mkdir()
    (tmp_path / "wiki" / "concepts").mkdir(parents=True)
    (tmp_path / "wiki" / "summaries").mkdir(parents=True)
    (tmp_path / "wiki" / "index.md").write_text("# index\n")
    # Populate so wiki-content gate accepts
    (tmp_path / "wiki" / "concepts" / "demo.md").write_text("# demo\n")
    (tmp_path / "wiki" / "summaries" / "demo.md").write_text("# demo\n")
    return tmp_path


@pytest.mark.asyncio
async def test_slash_deck_new_invokes_generator(tmp_path):
    kb = _make_kb(tmp_path)
    session = ChatSession.new(kb, "gpt-4o-mini", "en")
    style = Style.from_dict({})

    fake_validation = type("V", (), {"errors": [], "warnings": [], "ok": True})()

    with patch("openkb.skill.generator.Generator") as gen_cls:
        gen = gen_cls.return_value
        gen.run = AsyncMock(return_value=kb / "output" / "decks" / "demo")
        gen.validation = fake_validation
        gen.output_dir = kb / "output" / "decks" / "demo"

        action = await _handle_slash('/deck new demo "test intent"', kb, session, style)

    assert action is None  # continues chat session
    gen_cls.assert_called_once()
    kwargs = gen_cls.call_args.kwargs
    assert kwargs["target_type"] == "deck"
    assert kwargs["name"] == "demo"
    assert kwargs["intent"] == "test intent"
    assert kwargs["kb_dir"] == kb
    assert kwargs["critique"] is False


@pytest.mark.asyncio
async def test_slash_deck_new_with_critique_flag(tmp_path):
    kb = _make_kb(tmp_path)
    session = ChatSession.new(kb, "gpt-4o-mini", "en")
    style = Style.from_dict({})

    fake_validation = type("V", (), {"errors": [], "warnings": [], "ok": True})()

    with patch("openkb.skill.generator.Generator") as gen_cls:
        gen = gen_cls.return_value
        gen.run = AsyncMock(return_value=kb / "output" / "decks" / "demo")
        gen.validation = fake_validation
        gen.output_dir = kb / "output" / "decks" / "demo"

        action = await _handle_slash('/deck new --critique demo "test intent"', kb, session, style)

    assert action is None
    gen_cls.assert_called_once()
    kwargs = gen_cls.call_args.kwargs
    assert kwargs["target_type"] == "deck"
    assert kwargs["name"] == "demo"
    assert kwargs["critique"] is True


@pytest.mark.asyncio
async def test_slash_deck_new_reports_usage_when_args_missing(tmp_path):
    kb = _make_kb(tmp_path)
    session = ChatSession.new(kb, "gpt-4o-mini", "en")
    style = Style.from_dict({})

    action = await _handle_slash("/deck new", kb, session, style)
    assert action is None
    # No deck written
    assert not (kb / "output").exists()


@pytest.mark.asyncio
async def test_slash_deck_unknown_subcommand(tmp_path):
    kb = _make_kb(tmp_path)
    session = ChatSession.new(kb, "gpt-4o-mini", "en")
    style = Style.from_dict({})
    action = await _handle_slash("/deck list", kb, session, style)
    assert action is None


@pytest.mark.asyncio
async def test_slash_deck_new_rejects_empty_wiki(tmp_path):
    """Chat / slash command must catch freshly-init'd KBs (no compiled content)."""
    kb = tmp_path
    (kb / ".openkb").mkdir()
    (kb / ".openkb" / "config.yaml").write_text("model: gpt-4o-mini\n")
    (kb / ".openkb" / "chats").mkdir()
    # Empty wiki/ — exactly what `openkb init` creates
    (kb / "wiki" / "concepts").mkdir(parents=True)
    (kb / "wiki" / "summaries").mkdir(parents=True)
    (kb / "wiki" / "index.md").write_text("# index\n")

    session = ChatSession.new(kb, "gpt-4o-mini", "en")
    style = Style.from_dict({})

    action = await _handle_slash('/deck new demo "intent"', kb, session, style)
    assert action is None
    assert not (kb / "output").exists()


@pytest.mark.asyncio
async def test_slash_deck_new_rejects_when_target_exists(tmp_path):
    """Chat / slash command must not silently overwrite an existing deck."""
    kb = _make_kb(tmp_path)
    (kb / "output" / "decks" / "demo").mkdir(parents=True)
    (kb / "output" / "decks" / "demo" / "stale.html").write_text("old")

    session = ChatSession.new(kb, "gpt-4o-mini", "en")
    style = Style.from_dict({})

    action = await _handle_slash('/deck new demo "intent"', kb, session, style)
    assert action is None
    # stale.html must still be there (we didn't overwrite)
    assert (kb / "output" / "decks" / "demo" / "stale.html").read_text() == "old"
