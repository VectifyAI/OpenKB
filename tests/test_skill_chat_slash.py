"""Tests for the /skill new slash command inside openkb chat."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

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
    return tmp_path


@pytest.mark.asyncio
async def test_slash_skill_new_calls_generator(tmp_path):
    kb = _make_kb(tmp_path)
    session = ChatSession.new(kb, "gpt-4o-mini", "en")
    style = Style.from_dict({})

    async def fake_run(kb_dir, skill_name, intent, model):
        target = kb_dir / "output" / "skills" / skill_name
        target.mkdir(parents=True, exist_ok=True)
        (target / "SKILL.md").write_text(
            f"---\nname: {skill_name}\ndescription: t\n---\n\n# {skill_name}\n"
        )

    with patch("openkb.generator.run_skill_compile", new=AsyncMock(side_effect=fake_run)):
        action = await _handle_slash(
            '/skill new demo "test intent"', kb, session, style
        )

    assert action is None  # continues chat session
    assert (kb / "output" / "skills" / "demo" / "SKILL.md").exists()
    assert (kb / ".claude-plugin" / "marketplace.json").exists()


@pytest.mark.asyncio
async def test_slash_skill_new_reports_usage_when_args_missing(tmp_path):
    kb = _make_kb(tmp_path)
    session = ChatSession.new(kb, "gpt-4o-mini", "en")
    style = Style.from_dict({})

    action = await _handle_slash('/skill new', kb, session, style)
    assert action is None
    # No skill written
    assert not (kb / "output").exists()


@pytest.mark.asyncio
async def test_slash_skill_unknown_subcommand(tmp_path):
    kb = _make_kb(tmp_path)
    session = ChatSession.new(kb, "gpt-4o-mini", "en")
    style = Style.from_dict({})
    action = await _handle_slash('/skill list', kb, session, style)
    assert action is None
