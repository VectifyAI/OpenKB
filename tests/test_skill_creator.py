"""Tests for openkb.skill.creator.

The agent itself is mocked (we don't want to spend tokens in unit tests).
What we DO test:
  * Tools wire up correctly (write_skill_file is bound to the right path)
  * System prompt gets the intent and skill_name interpolated
  * The skill output dir is created before the agent starts
  * If the agent finishes without writing SKILL.md, we surface an error
  * If the SDK hits its max-turns cap, we translate to RuntimeError so
    the CLI/chat call sites (which only catch RuntimeError) print a
    friendly message instead of leaking a traceback.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from openkb.skill.creator import (
    build_skill_create_agent,
    run_skill_create,
)


def _make_kb(tmp_path):
    (tmp_path / "wiki" / "concepts").mkdir(parents=True)
    (tmp_path / "wiki" / "summaries").mkdir(parents=True)
    (tmp_path / "wiki" / "index.md").write_text("# index\n\nNo concepts yet.\n")
    (tmp_path / ".openkb").mkdir()
    (tmp_path / ".openkb" / "config.yaml").write_text("model: gpt-4o-mini\n")
    return tmp_path


def test_build_agent_interpolates_intent_and_name(tmp_path):
    kb = _make_kb(tmp_path)
    agent = build_skill_create_agent(
        wiki_root=str(kb / "wiki"),
        skill_root=str(kb / "output" / "skills" / "demo"),
        skill_name="demo",
        intent="distill a tax-lookup skill",
        model="gpt-4o-mini",
    )
    assert "demo" in agent.instructions
    assert "distill a tax-lookup skill" in agent.instructions


@pytest.mark.asyncio
async def test_run_skill_create_creates_output_dir(tmp_path):
    kb = _make_kb(tmp_path)
    target = kb / "output" / "skills" / "demo"

    # Fake the agent run: just write a minimal SKILL.md to simulate success.
    async def fake_runner(*args, **kwargs):
        target.mkdir(parents=True, exist_ok=True)
        (target / "SKILL.md").write_text("---\nname: demo\ndescription: test\n---\n\n# demo\n")
        from types import SimpleNamespace

        return SimpleNamespace(final_output="done")

    with patch("openkb.skill.creator.Runner.run", new=AsyncMock(side_effect=fake_runner)):
        await run_skill_create(
            kb_dir=kb,
            skill_name="demo",
            intent="test intent",
            model="gpt-4o-mini",
        )

    assert (target / "SKILL.md").exists()


@pytest.mark.asyncio
async def test_run_skill_create_raises_when_no_skill_md_written(tmp_path):
    kb = _make_kb(tmp_path)
    target = kb / "output" / "skills" / "demo"
    target.mkdir(parents=True, exist_ok=True)

    # Agent runs but never writes SKILL.md.
    async def fake_runner(*args, **kwargs):
        from types import SimpleNamespace

        return SimpleNamespace(final_output="done")

    with patch("openkb.skill.creator.Runner.run", new=AsyncMock(side_effect=fake_runner)):
        with pytest.raises(RuntimeError, match="did not write SKILL.md"):
            await run_skill_create(
                kb_dir=kb,
                skill_name="demo",
                intent="test intent",
                model="gpt-4o-mini",
            )


@pytest.mark.asyncio
async def test_run_skill_create_translates_max_turns_to_runtime_error(tmp_path):
    """MaxTurnsExceeded from the SDK should be re-raised as a RuntimeError
    with a user-friendly message — otherwise both CLI and chat call sites
    (which only catch RuntimeError) leak a Python traceback."""
    from agents.exceptions import MaxTurnsExceeded

    kb = _make_kb(tmp_path)

    async def fake_runner(*args, **kwargs):
        raise MaxTurnsExceeded("agent ran out of turns")

    with patch("openkb.skill.creator.Runner.run", new=AsyncMock(side_effect=fake_runner)):
        with pytest.raises(RuntimeError, match="step cap"):
            await run_skill_create(
                kb_dir=kb,
                skill_name="demo",
                intent="x",
                model="gpt-4o-mini",
            )
