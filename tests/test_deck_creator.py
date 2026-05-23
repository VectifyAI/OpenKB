"""Tests for the deck-creator wrapper around the skill runner.

Pre-skill-system tests (agent shape, handoff wiring, snapshot/restore)
have been removed alongside the build_deck_create_agent /
build_deck_critic_agent symbols they covered. See git history before
commit 08e95c3 if you need the originals.

The remaining surface to test is small: ``run_deck_create`` is a thin
wrapper that calls ``run_skill`` (mocked here), checks the output file
exists, and optionally chains the critic skill.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from openkb.agent.skill_runner import SkillNotFoundError
from openkb.deck.creator import CRITIC_MAX_TURNS, run_deck_create


def _make_kb(tmp_path: Path) -> Path:
    """Minimal KB layout so run_deck_create's path math works."""
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "AGENTS.md").write_text("schema", encoding="utf-8")
    return tmp_path


def _write_index(kb_dir: Path, deck_name: str, body: str = "<html></html>") -> Path:
    """Simulate the deck-editorial skill writing index.html."""
    out = kb_dir / "output" / "decks" / deck_name
    out.mkdir(parents=True, exist_ok=True)
    p = out / "index.html"
    p.write_text(body, encoding="utf-8")
    return p


@pytest.mark.asyncio
async def test_run_deck_create_calls_editorial_skill(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)

    async def fake_skill(skill_name, intent, **_):
        if skill_name == "openkb-deck-editorial":
            _write_index(kb_dir, "test-deck")

    with patch("openkb.deck.creator.run_skill", new=AsyncMock(side_effect=fake_skill)) as run_skill:
        result = await run_deck_create(
            kb_dir=kb_dir,
            deck_name="test-deck",
            intent="A test deck.",
            model="openai/gpt-4o",
            critique=False,
        )

    assert result == kb_dir / "output" / "decks" / "test-deck"
    assert (result / "index.html").is_file()
    # exactly one skill call (no critic when critique=False)
    assert run_skill.await_count == 1
    args, kwargs = run_skill.call_args
    assert kwargs["skill_name"] == "openkb-deck-editorial"
    assert "test-deck" in kwargs["intent"]


@pytest.mark.asyncio
async def test_run_deck_create_chains_critic_when_critique_true(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)
    calls: list[str] = []

    async def fake_skill(skill_name, intent, **_):
        calls.append(skill_name)
        if skill_name == "openkb-deck-editorial":
            _write_index(kb_dir, "test-deck")

    with patch("openkb.deck.creator.run_skill", new=AsyncMock(side_effect=fake_skill)):
        await run_deck_create(
            kb_dir=kb_dir,
            deck_name="test-deck",
            intent="A test deck.",
            model="openai/gpt-4o",
            critique=True,
        )

    assert calls == ["openkb-deck-editorial", "openkb-html-critic"]


@pytest.mark.asyncio
async def test_run_deck_create_critic_max_turns(tmp_path: Path):
    """When critique=True, second call is to the critic skill with the
    smaller CRITIC_MAX_TURNS budget (it's read-and-patch, not authoring)."""
    kb_dir = _make_kb(tmp_path)

    async def fake_skill(skill_name, intent, **kw):
        if skill_name == "openkb-deck-editorial":
            _write_index(kb_dir, "test-deck")

    with patch("openkb.deck.creator.run_skill", new=AsyncMock(side_effect=fake_skill)) as run_skill:
        await run_deck_create(
            kb_dir=kb_dir,
            deck_name="test-deck",
            intent="A test deck.",
            model="openai/gpt-4o",
            critique=True,
        )

    critic_call = run_skill.call_args_list[1]
    assert critic_call.kwargs["skill_name"] == "openkb-html-critic"
    assert critic_call.kwargs["max_turns"] == CRITIC_MAX_TURNS


@pytest.mark.asyncio
async def test_run_deck_create_raises_when_skill_missing(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)

    async def missing_skill(**_):
        raise SkillNotFoundError("not installed")

    with patch("openkb.deck.creator.run_skill", new=AsyncMock(side_effect=missing_skill)):
        with pytest.raises(RuntimeError, match="openkb-deck-editorial"):
            await run_deck_create(
                kb_dir=kb_dir,
                deck_name="test-deck",
                intent="A test deck.",
                model="openai/gpt-4o",
                critique=False,
            )


@pytest.mark.asyncio
async def test_run_deck_create_raises_when_html_missing(tmp_path: Path):
    """If the skill returns but no index.html was written, error out."""
    kb_dir = _make_kb(tmp_path)

    async def fake_skill(**_):
        return  # no file written

    with patch("openkb.deck.creator.run_skill", new=AsyncMock(side_effect=fake_skill)):
        with pytest.raises(RuntimeError, match="did not write index.html"):
            await run_deck_create(
                kb_dir=kb_dir,
                deck_name="test-deck",
                intent="A test deck.",
                model="openai/gpt-4o",
                critique=False,
            )


@pytest.mark.asyncio
async def test_run_deck_create_tolerates_missing_critic(tmp_path: Path):
    """Critic skill not installed shouldn't kill the run — the unpatched
    deck is still on disk and usable."""
    kb_dir = _make_kb(tmp_path)

    async def fake_skill(skill_name, **_):
        if skill_name == "openkb-deck-editorial":
            _write_index(kb_dir, "test-deck")
        else:
            raise SkillNotFoundError("critic not installed")

    with patch("openkb.deck.creator.run_skill", new=AsyncMock(side_effect=fake_skill)):
        result = await run_deck_create(
            kb_dir=kb_dir,
            deck_name="test-deck",
            intent="A test deck.",
            model="openai/gpt-4o",
            critique=True,
        )

    assert (result / "index.html").is_file()
