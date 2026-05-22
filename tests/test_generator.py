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

    with patch("openkb.skill.generator.run_deck_create", new_callable=AsyncMock) as run_dc, \
         patch("openkb.skill.generator.regenerate_marketplace") as regen, \
         patch("openkb.skill.generator.validate_deck") as v_deck:
        run_dc.return_value = gen.output_dir
        v_deck.return_value = DeckValidationResult()
        result = await gen.run()

    run_dc.assert_awaited_once_with(
        kb_dir=kb_dir,
        deck_name="my-deck",
        intent="…",
        model="openai/gpt-4o",
        critique=False,
    )
    v_deck.assert_called_once_with(gen.output_dir)
    regen.assert_not_called()  # marketplace is skill-only
    assert result == gen.output_dir


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


@pytest.mark.asyncio
async def test_generator_deck_cleanup_on_critique_success(tmp_path):
    """When critique=True and validation passes, the pre-critique snapshot
    must be deleted so it doesn't accumulate across runs."""
    kb_dir = tmp_path
    (kb_dir / "wiki").mkdir()
    (kb_dir / "wiki" / "AGENTS.md").write_text("schema", encoding="utf-8")

    gen = Generator(
        target_type="deck",
        name="my-deck",
        intent="…",
        kb_dir=kb_dir,
        model="openai/gpt-4o",
        critique=True,
    )

    # Simulate the creator + critic having written both files.
    gen.output_dir.mkdir(parents=True, exist_ok=True)
    (gen.output_dir / "index.html").write_text("<html>critic-output</html>", encoding="utf-8")
    (gen.output_dir / "index.pre-critique.html").write_text("<html>pre-critic</html>", encoding="utf-8")

    with patch("openkb.skill.generator.run_deck_create", new_callable=AsyncMock) as run_dc, \
         patch("openkb.skill.generator.validate_deck") as v_deck:
        run_dc.return_value = gen.output_dir
        v_deck.return_value = DeckValidationResult()  # no errors → success
        await gen.run()

    # On critique success, the snapshot is cleaned up.
    assert not (gen.output_dir / "index.pre-critique.html").exists()
    # The live deck is untouched.
    assert (gen.output_dir / "index.html").read_text() == "<html>critic-output</html>"


@pytest.mark.asyncio
async def test_generator_deck_restore_on_critique_failure(tmp_path):
    """When critique=True and validation fails, the pre-critique snapshot
    must be restored back to index.html and the snapshot kept for inspection."""
    kb_dir = tmp_path
    (kb_dir / "wiki").mkdir()
    (kb_dir / "wiki" / "AGENTS.md").write_text("schema", encoding="utf-8")

    gen = Generator(
        target_type="deck",
        name="my-deck",
        intent="…",
        kb_dir=kb_dir,
        model="openai/gpt-4o",
        critique=True,
    )

    # Simulate critic having corrupted index.html, with snapshot of original.
    gen.output_dir.mkdir(parents=True, exist_ok=True)
    (gen.output_dir / "index.html").write_text("<html>critic-broken</html>", encoding="utf-8")
    (gen.output_dir / "index.pre-critique.html").write_text("<html>pre-critic-good</html>", encoding="utf-8")

    # First validation reports an error; second (post-restore) is clean.
    first_result = DeckValidationResult()
    first_result.errors.append("bad slide count")
    second_result = DeckValidationResult()

    with patch("openkb.skill.generator.run_deck_create", new_callable=AsyncMock) as run_dc, \
         patch("openkb.skill.generator.validate_deck", side_effect=[first_result, second_result]):
        run_dc.return_value = gen.output_dir
        await gen.run()

    # index.html was restored from the snapshot.
    assert (gen.output_dir / "index.html").read_text() == "<html>pre-critic-good</html>"
    # The warning about restore is surfaced.
    assert any("restored pre-critique draft" in w for w in gen.validation.warnings)
