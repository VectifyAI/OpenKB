"""Tests for the deck-creator wrapper around the skill runner.

Pre-skill-system tests (agent shape, handoff wiring, snapshot/restore)
have been removed alongside the build_deck_create_agent /
build_deck_critic_agent symbols they covered. See git history before
commit 08e95c3 if you need the originals.

The remaining surface to test is small: ``run_deck_create`` is a thin
wrapper that calls ``run_skill`` (mocked here), returns the producer
skill's ``SkillRunResult``, and optionally chains the critic skill.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from openkb.agent.skill_runner import SkillNotFoundError, SkillRunResult
from openkb.deck.creator import (
    CRITIC_MAX_TURNS,
    CRITIC_SKILL,
    DEFAULT_DECK_SKILL,
    run_deck_create,
)


def _make_kb(tmp_path: Path) -> Path:
    """Minimal KB layout so run_deck_create's path math works."""
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "AGENTS.md").write_text("schema", encoding="utf-8")
    return tmp_path


def _write_index(kb_dir: Path, deck_name: str, body: str = "<html></html>") -> Path:
    """Simulate the producer skill writing index.html."""
    out = kb_dir / "output" / "decks" / deck_name
    out.mkdir(parents=True, exist_ok=True)
    p = out / "index.html"
    p.write_text(body, encoding="utf-8")
    return p


def _producer_result(kb_dir: Path, deck_name: str) -> SkillRunResult:
    """Build the SkillRunResult ``run_skill`` would return for the producer."""
    return SkillRunResult(
        skill_name=DEFAULT_DECK_SKILL,
        output_path=(kb_dir / "output" / "decks" / deck_name / "index.html").resolve(),
        validation=None,  # validator is mocked elsewhere; not relevant here
        metadata={"mode": "deck"},
    )


def _critic_result() -> SkillRunResult:
    return SkillRunResult(skill_name=CRITIC_SKILL, output_path=None, validation=None)


@pytest.mark.asyncio
async def test_run_deck_create_calls_editorial_skill_by_default(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)

    async def fake_skill(skill_name, intent, **kw):
        if skill_name == DEFAULT_DECK_SKILL:
            _write_index(kb_dir, "test-deck")
            return _producer_result(kb_dir, "test-deck")
        return _critic_result()

    with patch("openkb.deck.creator.run_skill", new=AsyncMock(side_effect=fake_skill)) as run_skill:
        result = await run_deck_create(
            kb_dir=kb_dir,
            deck_name="test-deck",
            intent="A test deck.",
            model="openai/gpt-4o",
            critique=False,
        )

    # Producer skill ran with the default name + correct slug.
    assert run_skill.await_count == 1
    kwargs = run_skill.call_args.kwargs
    assert kwargs["skill_name"] == DEFAULT_DECK_SKILL
    assert kwargs["slug"] == "test-deck"
    # Return value is the producer SkillRunResult.
    assert isinstance(result, SkillRunResult)
    assert result.skill_name == DEFAULT_DECK_SKILL


@pytest.mark.asyncio
async def test_run_deck_create_honors_skill_name_override(tmp_path: Path):
    """When the caller passes ``skill_name=...`` (e.g. CLI ``--skill``),
    the producer skill switches and the path layout still applies."""
    kb_dir = _make_kb(tmp_path)

    async def fake_skill(skill_name, intent, **kw):
        _write_index(kb_dir, "test-deck")
        return _producer_result(kb_dir, "test-deck")

    with patch("openkb.deck.creator.run_skill", new=AsyncMock(side_effect=fake_skill)) as run_skill:
        await run_deck_create(
            kb_dir=kb_dir,
            deck_name="test-deck",
            intent="A test deck.",
            model="openai/gpt-4o",
            critique=False,
            skill_name="deck-guizang-editorial",
        )

    assert run_skill.call_args.kwargs["skill_name"] == "deck-guizang-editorial"


@pytest.mark.asyncio
async def test_run_deck_create_chains_critic_when_critique_true(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)
    calls: list[str] = []

    async def fake_skill(skill_name, intent, **kw):
        calls.append(skill_name)
        if skill_name == DEFAULT_DECK_SKILL:
            _write_index(kb_dir, "test-deck")
            return _producer_result(kb_dir, "test-deck")
        return _critic_result()

    with patch("openkb.deck.creator.run_skill", new=AsyncMock(side_effect=fake_skill)):
        await run_deck_create(
            kb_dir=kb_dir,
            deck_name="test-deck",
            intent="A test deck.",
            model="openai/gpt-4o",
            critique=True,
        )

    assert calls == [DEFAULT_DECK_SKILL, CRITIC_SKILL]


@pytest.mark.asyncio
async def test_run_deck_create_critic_max_turns(tmp_path: Path):
    """When critique=True, second call is to the critic skill with the
    smaller CRITIC_MAX_TURNS budget (it's read-and-patch, not authoring)."""
    kb_dir = _make_kb(tmp_path)

    async def fake_skill(skill_name, intent, **kw):
        if skill_name == DEFAULT_DECK_SKILL:
            _write_index(kb_dir, "test-deck")
            return _producer_result(kb_dir, "test-deck")
        return _critic_result()

    with patch("openkb.deck.creator.run_skill", new=AsyncMock(side_effect=fake_skill)) as run_skill:
        await run_deck_create(
            kb_dir=kb_dir,
            deck_name="test-deck",
            intent="A test deck.",
            model="openai/gpt-4o",
            critique=True,
        )

    critic_call = run_skill.call_args_list[1]
    assert critic_call.kwargs["skill_name"] == CRITIC_SKILL
    assert critic_call.kwargs["max_turns"] == CRITIC_MAX_TURNS


@pytest.mark.asyncio
async def test_run_deck_create_raises_when_skill_missing(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)

    async def missing_skill(**_):
        raise SkillNotFoundError("not installed")

    with patch("openkb.deck.creator.run_skill", new=AsyncMock(side_effect=missing_skill)):
        with pytest.raises(RuntimeError, match="not installed"):
            await run_deck_create(
                kb_dir=kb_dir,
                deck_name="test-deck",
                intent="A test deck.",
                model="openai/gpt-4o",
                critique=False,
            )


@pytest.mark.asyncio
async def test_run_deck_create_critique_handles_symlinked_tmp(tmp_path: Path):
    """Regression: on macOS ``/tmp`` symlinks to ``/private/tmp`` so
    ``output_path`` comes back resolved while ``kb_dir`` is still the
    symlink form. ``relative_to`` must not blow up — both must resolve
    before comparing. Caught in smoke testing the e2e flow."""
    kb_dir = _make_kb(tmp_path)
    # output_path resolved to a deeper-named absolute path (simulates
    # the macOS /tmp -> /private/tmp situation)
    resolved_target = (kb_dir / "output" / "decks" / "test-deck" / "index.html").resolve()

    async def fake_skill(skill_name, intent, **kw):
        if skill_name == DEFAULT_DECK_SKILL:
            _write_index(kb_dir, "test-deck")
            return SkillRunResult(
                skill_name=DEFAULT_DECK_SKILL,
                output_path=resolved_target,
                metadata={"mode": "deck"},
            )
        return _critic_result()

    with patch("openkb.deck.creator.run_skill", new=AsyncMock(side_effect=fake_skill)):
        # critique=True is what exercises the relative_to call
        await run_deck_create(
            kb_dir=kb_dir,
            deck_name="test-deck",
            intent="t",
            model="m",
            critique=True,
        )


@pytest.mark.asyncio
async def test_run_deck_create_tolerates_missing_critic(tmp_path: Path):
    """Critic skill not installed shouldn't kill the run — the unpatched
    deck is still on disk and usable."""
    kb_dir = _make_kb(tmp_path)

    async def fake_skill(skill_name, **kw):
        if skill_name == DEFAULT_DECK_SKILL:
            _write_index(kb_dir, "test-deck")
            return _producer_result(kb_dir, "test-deck")
        raise SkillNotFoundError("critic not installed")

    with patch("openkb.deck.creator.run_skill", new=AsyncMock(side_effect=fake_skill)):
        result = await run_deck_create(
            kb_dir=kb_dir,
            deck_name="test-deck",
            intent="A test deck.",
            model="openai/gpt-4o",
            critique=True,
        )

    assert isinstance(result, SkillRunResult)
    # File is still on disk despite critic failure
    assert (kb_dir / "output" / "decks" / "test-deck" / "index.html").is_file()
