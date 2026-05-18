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
