"""Tests for openkb.skill.generator.Generator — the v0.1 abstraction that will
be reused by future ppt / podcast generators.

In v0.1, only target_type='skill' is supported. We test the dispatch shape
so future targets slot in cleanly."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from openkb.skill.generator import Generator


def _make_kb(tmp_path):
    (tmp_path / ".openkb").mkdir()
    (tmp_path / ".openkb" / "config.yaml").write_text("model: gpt-4o-mini\n")
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "index.md").write_text("# index\n")
    return tmp_path


def test_generator_rejects_unknown_target_type(tmp_path):
    kb = _make_kb(tmp_path)
    with pytest.raises(ValueError, match="target_type"):
        Generator(
            target_type="ppt",
            name="demo",
            intent="x",
            kb_dir=kb,
            model="gpt-4o-mini",
        )


def test_generator_skill_target_constructs_ok(tmp_path):
    kb = _make_kb(tmp_path)
    g = Generator(
        target_type="skill",
        name="demo",
        intent="x",
        kb_dir=kb,
        model="gpt-4o-mini",
    )
    assert g.output_dir == kb / "output" / "skills" / "demo"


@pytest.mark.asyncio
async def test_generator_run_delegates_to_skill_creator(tmp_path):
    kb = _make_kb(tmp_path)
    g = Generator(
        target_type="skill",
        name="demo",
        intent="x",
        kb_dir=kb,
        model="gpt-4o-mini",
    )
    with patch("openkb.skill.generator.run_skill_create", new=AsyncMock()) as runner, \
         patch("openkb.skill.generator.regenerate_marketplace") as regen:
        await g.run()
    runner.assert_awaited_once()
    regen.assert_called_once_with(kb)


# --- target_type="deck" dispatch -------------------------------------------

from openkb.deck.validator import ValidationResult as DeckValidationResult


@pytest.mark.asyncio
async def test_generator_deck_dispatches_to_deck_creator(tmp_path):
    kb_dir = tmp_path
    (kb_dir / "wiki").mkdir()
    (kb_dir / "wiki" / "AGENTS.md").write_text("schema", encoding="utf-8")

    gen = Generator(
        target_type="deck",
        name="my-deck",
        intent="…",
        kb_dir=kb_dir,
        model="openai/gpt-4o",
        critique=False,
    )

    # Post-refactor: validate_deck moved into run_skill (called inside
    # run_deck_create). Generator just propagates the SkillRunResult's
    # validation up to self.validation.
    from openkb.agent.skill_runner import SkillRunResult
    fake_run_result = SkillRunResult(
        skill_name="openkb-deck-editorial",
        output_path=gen.output_dir / "index.html",
        validation=DeckValidationResult(),
        metadata={"mode": "deck"},
    )

    with patch("openkb.skill.generator.run_deck_create", new_callable=AsyncMock) as run_dc, \
         patch("openkb.skill.generator.regenerate_marketplace") as regen:
        run_dc.return_value = fake_run_result
        result = await gen.run()

    run_dc.assert_awaited_once_with(
        kb_dir=kb_dir,
        deck_name="my-deck",
        intent="…",
        model="openai/gpt-4o",
        critique=False,
        skill_name="openkb-deck-editorial",
    )
    regen.assert_not_called()  # marketplace is skill-only
    assert result == gen.output_dir
    assert gen.validation is fake_run_result.validation


@pytest.mark.asyncio
async def test_generator_deck_output_dir_is_decks(tmp_path):
    gen = Generator(
        target_type="deck",
        name="my-deck",
        intent="…",
        kb_dir=tmp_path,
        model="openai/gpt-4o",
        critique=False,
    )
    assert gen.output_dir == tmp_path / "output" / "decks" / "my-deck"


def test_generator_rejects_podcast_target_type(tmp_path):
    with pytest.raises(ValueError, match="Unknown target_type"):
        Generator(
            target_type="podcast",  # type: ignore[arg-type]
            name="x",
            intent="…",
            kb_dir=tmp_path,
            model="openai/gpt-4o",
            critique=False,
        )


# Pre-skill-system snapshot/restore Generator tests removed: the
# html-critic skill patches HTML in place, so there is no longer a
# pre-critique snapshot to clean up or restore. See
# tests/test_deck_creator.py::test_run_deck_create_chains_critic_when_critique_true
# for the equivalent guarantee at the new layer.
