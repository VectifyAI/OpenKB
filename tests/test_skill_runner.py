"""Direct unit tests for :mod:`openkb.agent.skill_runner`.

Pre-existing test files mocked ``run_skill`` at every caller (deck
creator, generator, chat slash). That covered the dispatch boundary but
left the function itself untested. The tests below construct synthetic
``SKILL.md`` files under ``tmp_path`` and patch ``agents.Runner.run`` so
we can verify the assembly path end-to-end without spending tokens:

* skill lookup — ``SkillNotFoundError`` with a helpful "available" list
* prompt assembly — body lands in ``agent.instructions`` followed by
  the "## User intent" section
* tools wired — ``write_file`` + ``read_output_or_skill_file`` present
  alongside the inherited wiki-read tools
* output-path templating — ``{slug}`` substituted, injected into
  intent, file existence enforced post-run
* deck-mode validator hook — when frontmatter ``od.mode == "deck"``,
  ``validate_deck`` runs with the skill's grammar after the agent finishes
* MaxTurnsExceeded → RuntimeError translation with a useful message
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from openkb.agent.skill_runner import (
    MAX_TURNS,
    SkillNotFoundError,
    SkillRunResult,
    run_skill,
)


def _make_kb(tmp_path: Path) -> Path:
    """Minimal KB layout so build_query_agent inside run_skill can boot."""
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "AGENTS.md").write_text("schema", encoding="utf-8")
    return tmp_path


def _install_skill(
    kb_dir: Path,
    name: str,
    *,
    body: str = "Do the thing.",
    od: dict | None = None,
    description: str = "A test skill.",
) -> Path:
    """Drop a SKILL.md under ``<kb>/skills/<name>/`` and return its path."""
    sk_dir = kb_dir / "skills" / name
    sk_dir.mkdir(parents=True)
    frontmatter = {"name": name, "description": description}
    if od is not None:
        frontmatter["od"] = od
    # Write YAML by hand to avoid a yaml.dump dependency on test side
    fm_lines = ["---"]
    for k, v in frontmatter.items():
        if isinstance(v, dict):
            fm_lines.append(f"{k}:")
            for kk, vv in v.items():
                if isinstance(vv, dict):
                    fm_lines.append(f"  {kk}:")
                    for kkk, vvv in vv.items():
                        fm_lines.append(f"    {kkk}: {vvv!r}")
                else:
                    fm_lines.append(f"  {kk}: {vv!r}")
        else:
            fm_lines.append(f"{k}: {v!r}")
    fm_lines.append("---")
    (sk_dir / "SKILL.md").write_text("\n".join(fm_lines) + "\n" + body, encoding="utf-8")
    return sk_dir


@pytest.mark.asyncio
async def test_run_skill_raises_skill_not_found_with_available_list(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)
    _install_skill(kb_dir, "alpha")
    _install_skill(kb_dir, "beta")

    with pytest.raises(SkillNotFoundError) as exc_info:
        await run_skill(
            skill_name="nonexistent",
            intent="anything",
            kb_dir=kb_dir,
            model="openai/gpt-4o",
        )
    msg = str(exc_info.value)
    # Helpful: lists what IS available so the user can see the typo
    assert "alpha" in msg
    assert "beta" in msg
    assert "nonexistent" in msg


@pytest.mark.asyncio
async def test_run_skill_loads_body_into_instructions(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)
    _install_skill(kb_dir, "marker-skill", body="UNIQUE-BODY-MARKER")

    captured: dict = {}

    async def fake_runner_run(agent, seed, **kw):
        captured["instructions"] = agent.instructions
        captured["tools"] = [getattr(t, "name", "?") for t in agent.tools]
        return MagicMock()

    with patch("openkb.agent.skill_runner.Runner.run", new=fake_runner_run):
        result = await run_skill(
            skill_name="marker-skill",
            intent="DO-THE-INTENT",
            kb_dir=kb_dir,
            model="openai/gpt-4o",
        )

    # Body and intent are both present in the constructed agent's instructions.
    assert "UNIQUE-BODY-MARKER" in captured["instructions"]
    assert "DO-THE-INTENT" in captured["instructions"]
    assert "## User intent" in captured["instructions"]
    # The skill-runner's two distinguishing tools are wired in.
    assert "write_file" in captured["tools"]
    assert "read_output_or_skill_file" in captured["tools"]
    # Return shape
    assert isinstance(result, SkillRunResult)
    assert result.skill_name == "marker-skill"


@pytest.mark.asyncio
async def test_run_skill_templates_output_path_and_enforces_existence(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)
    _install_skill(
        kb_dir,
        "templated",
        od={
            "mode": "deck",
            "output_path_template": "output/decks/{slug}/index.html",
        },
    )

    captured: dict = {}

    async def fake_runner_run(agent, seed, **kw):
        captured["instructions"] = agent.instructions
        # Simulate the skill writing the expected file
        out = kb_dir / "output" / "decks" / "my-slug" / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            '<section class="slide" data-type="cover"></section>' * 8,
            encoding="utf-8",
        )
        return MagicMock()

    with patch("openkb.agent.skill_runner.Runner.run", new=fake_runner_run):
        result = await run_skill(
            skill_name="templated",
            intent="brief",
            kb_dir=kb_dir,
            model="openai/gpt-4o",
            slug="my-slug",
        )

    # Path was injected into agent's intent so the skill knows where to write
    assert "output/decks/my-slug/index.html" in captured["instructions"]
    # output_path resolved against kb_dir
    assert result.output_path is not None
    assert result.output_path.name == "index.html"
    assert "my-slug" in str(result.output_path)


@pytest.mark.asyncio
async def test_run_skill_raises_if_templated_output_missing_post_run(tmp_path: Path):
    """If the skill declared output_path_template but didn't write the
    file, that's a hard error — the skill is misconfigured or the wiki
    lacks content."""
    kb_dir = _make_kb(tmp_path)
    _install_skill(
        kb_dir,
        "lazy-skill",
        od={
            "mode": "deck",
            "output_path_template": "output/decks/{slug}/index.html",
        },
    )

    async def fake_runner_run(agent, seed, **kw):
        return MagicMock()  # agent does nothing

    with patch("openkb.agent.skill_runner.Runner.run", new=fake_runner_run):
        with pytest.raises(RuntimeError, match="did not write the expected"):
            await run_skill(
                skill_name="lazy-skill",
                intent="x",
                kb_dir=kb_dir,
                model="openai/gpt-4o",
                slug="ghost",
            )


@pytest.mark.asyncio
async def test_run_skill_runs_deck_validator_when_mode_is_deck(tmp_path: Path):
    """When ``od.mode == "deck"`` and ``output_path_template`` is set,
    ``run_skill`` calls ``validate_deck`` with the skill's grammar after
    the run completes."""
    kb_dir = _make_kb(tmp_path)
    _install_skill(
        kb_dir,
        "deck-with-grammar",
        od={
            "mode": "deck",
            "output_path_template": "output/decks/{slug}/index.html",
            "deck_grammar": {
                "kind_attr": "data-type",
                "required": ["cover", "closing"],
            },
        },
    )

    async def fake_runner_run(agent, seed, **kw):
        out = kb_dir / "output" / "decks" / "demo" / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        # 5 slides, NO closing — grammar should reject
        out.write_text(
            '<section class="slide" data-type="cover"></section>'
            + '<section class="slide" data-type="thesis"></section>' * 4,
            encoding="utf-8",
        )
        return MagicMock()

    with patch("openkb.agent.skill_runner.Runner.run", new=fake_runner_run):
        result = await run_skill(
            skill_name="deck-with-grammar",
            intent="x",
            kb_dir=kb_dir,
            model="openai/gpt-4o",
            slug="demo",
        )

    # Validation was applied with the skill's grammar; missing-closing fired
    assert result.validation is not None
    assert any("closing" in e for e in result.validation.errors)


@pytest.mark.asyncio
async def test_run_skill_no_validator_when_mode_not_deck(tmp_path: Path):
    """Skills with ``od.mode`` other than ``"deck"`` (or no mode at all)
    skip the validator — they're not deck-shaped artifacts."""
    kb_dir = _make_kb(tmp_path)
    _install_skill(kb_dir, "no-mode-skill")  # no od block at all

    async def fake_runner_run(agent, seed, **kw):
        return MagicMock()

    with patch("openkb.agent.skill_runner.Runner.run", new=fake_runner_run):
        result = await run_skill(
            skill_name="no-mode-skill",
            intent="x",
            kb_dir=kb_dir,
            model="openai/gpt-4o",
        )

    assert result.validation is None


@pytest.mark.asyncio
async def test_run_skill_translates_max_turns_exceeded(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)
    _install_skill(kb_dir, "loopy")
    from agents.exceptions import MaxTurnsExceeded

    async def fake_runner_run(agent, seed, **kw):
        raise MaxTurnsExceeded("model spun forever")

    with patch("openkb.agent.skill_runner.Runner.run", new=fake_runner_run):
        with pytest.raises(RuntimeError) as exc_info:
            await run_skill(
                skill_name="loopy",
                intent="x",
                kb_dir=kb_dir,
                model="openai/gpt-4o",
                max_turns=10,
            )
    msg = str(exc_info.value)
    assert "10" in msg
    assert "step cap" in msg or "step" in msg.lower()
    assert "loopy" in msg


@pytest.mark.asyncio
async def test_run_skill_default_max_turns(tmp_path: Path):
    """Default ``max_turns`` is the module-level ``MAX_TURNS`` constant."""
    kb_dir = _make_kb(tmp_path)
    _install_skill(kb_dir, "default-budget")

    captured: dict = {}

    async def fake_runner_run(agent, seed, **kw):
        captured["max_turns"] = kw.get("max_turns")
        return MagicMock()

    with patch("openkb.agent.skill_runner.Runner.run", new=fake_runner_run):
        await run_skill(
            skill_name="default-budget",
            intent="x",
            kb_dir=kb_dir,
            model="openai/gpt-4o",
        )

    assert captured["max_turns"] == MAX_TURNS
