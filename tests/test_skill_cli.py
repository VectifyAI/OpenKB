"""End-to-end tests for `openkb skill new` via click.testing.CliRunner.

The agent runner is patched so these tests don't burn LLM tokens. They
verify the CLI wiring: KB detection, name validation, overwrite logic,
marketplace.json regeneration, exit codes."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from openkb.cli import cli


def _make_kb(tmp_path):
    (tmp_path / ".openkb").mkdir()
    (tmp_path / ".openkb" / "config.yaml").write_text("model: gpt-4o-mini\n")
    (tmp_path / "wiki" / "concepts").mkdir(parents=True)
    (tmp_path / "wiki" / "summaries").mkdir(parents=True)
    (tmp_path / "wiki" / "index.md").write_text("# index\n")
    # Populate the wiki with compiled content so the wiki-content gate accepts it.
    (tmp_path / "wiki" / "concepts" / "demo.md").write_text("# demo\n")
    (tmp_path / "wiki" / "summaries" / "demo.md").write_text("# demo\n")
    return tmp_path


def _fake_compile(kb_dir, skill_name, **_kw):
    """Side-effect for the patched run_skill_create: write a minimal SKILL.md."""
    target = kb_dir / "output" / "skills" / skill_name
    target.mkdir(parents=True, exist_ok=True)
    (target / "SKILL.md").write_text(
        f"---\nname: {skill_name}\ndescription: test description\n---\n\n# {skill_name}\n"
    )


def test_skill_new_succeeds_and_writes_files(tmp_path):
    kb = _make_kb(tmp_path)
    runner = CliRunner()

    async def fake_run(kb_dir, skill_name, intent, model):
        _fake_compile(kb_dir, skill_name)

    with (
        patch("openkb.cli._find_kb_dir", return_value=kb),
        patch("openkb.skill.generator.run_skill_create", new=AsyncMock(side_effect=fake_run)),
    ):
        result = runner.invoke(cli, ["skill", "new", "demo", "test intent"])

    assert result.exit_code == 0, result.output
    assert (kb / "output" / "skills" / "demo" / "SKILL.md").exists()
    assert (kb / ".claude-plugin" / "marketplace.json").exists()
    manifest = json.loads((kb / ".claude-plugin" / "marketplace.json").read_text())
    assert manifest["plugins"][0]["skills"] == ["./output/skills/demo"]


def test_skill_new_rejects_invalid_name(tmp_path):
    kb = _make_kb(tmp_path)
    runner = CliRunner()
    with patch("openkb.cli._find_kb_dir", return_value=kb):
        result = runner.invoke(cli, ["skill", "new", "BadName", "x"])
    assert result.exit_code != 0
    assert "lowercase" in result.output.lower()


def test_skill_new_errors_without_kb(tmp_path):
    runner = CliRunner()
    with patch("openkb.cli._find_kb_dir", return_value=None):
        result = runner.invoke(cli, ["skill", "new", "demo", "x"])
    assert result.exit_code != 0
    assert "No knowledge base" in result.output


def test_skill_new_errors_with_empty_wiki(tmp_path):
    kb = tmp_path
    (kb / ".openkb").mkdir()
    (kb / ".openkb" / "config.yaml").write_text("model: gpt-4o-mini\n")
    # No wiki/ directory
    runner = CliRunner()
    with patch("openkb.cli._find_kb_dir", return_value=kb):
        result = runner.invoke(cli, ["skill", "new", "demo", "x"])
    assert result.exit_code != 0
    assert "wiki" in result.output.lower()


def test_skill_new_errors_with_freshly_init_wiki(tmp_path):
    """A freshly init'd KB has wiki/ + empty concepts/ + summaries/ + index.md.
    No documents have been ingested. The skill factory must refuse to compile
    rather than spend tokens on an empty wiki."""
    kb = tmp_path
    (kb / ".openkb").mkdir()
    (kb / ".openkb" / "config.yaml").write_text("model: gpt-4o-mini\n")
    # Mirror openkb init's layout: empty concepts + summaries, just index.md
    (kb / "wiki" / "concepts").mkdir(parents=True)
    (kb / "wiki" / "summaries").mkdir(parents=True)
    (kb / "wiki" / "index.md").write_text("# index\n")
    runner = CliRunner()
    with patch("openkb.cli._find_kb_dir", return_value=kb):
        result = runner.invoke(cli, ["skill", "new", "demo", "x"])
    assert result.exit_code != 0
    assert "compiled content" in result.output.lower() or "ingest" in result.output.lower()


def test_skill_new_aborts_when_target_exists_without_yes(tmp_path):
    kb = _make_kb(tmp_path)
    (kb / "output" / "skills" / "demo").mkdir(parents=True)
    runner = CliRunner()
    with patch("openkb.cli._find_kb_dir", return_value=kb):
        # Simulate non-interactive abort (CliRunner doesn't supply a TTY,
        # which our error path treats as "must pass -y").
        result = runner.invoke(cli, ["skill", "new", "demo", "x"], input="n\n")
    assert result.exit_code != 0
    # Either it asked and we said no, or it detected non-TTY and errored out.
    out = result.output.lower()
    assert "exists" in out or "overwrite" in out or "aborted" in out


def test_skill_new_overwrites_with_yes_flag(tmp_path):
    kb = _make_kb(tmp_path)
    (kb / "output" / "skills" / "demo").mkdir(parents=True)
    (kb / "output" / "skills" / "demo" / "stale.txt").write_text("old")
    runner = CliRunner()

    async def fake_run(kb_dir, skill_name, intent, model):
        _fake_compile(kb_dir, skill_name)

    with (
        patch("openkb.cli._find_kb_dir", return_value=kb),
        patch("openkb.skill.generator.run_skill_create", new=AsyncMock(side_effect=fake_run)),
    ):
        result = runner.invoke(cli, ["skill", "new", "demo", "x", "-y"])

    assert result.exit_code == 0, result.output
    assert not (kb / "output" / "skills" / "demo" / "stale.txt").exists()
    assert (kb / "output" / "skills" / "demo" / "SKILL.md").exists()


def test_skill_new_saves_iteration_when_overwriting(tmp_path):
    """Overwriting with -y must copy the old skill into the workspace
    under iteration-1/ before the generator runs."""
    kb = _make_kb(tmp_path)
    target = kb / "output" / "skills" / "demo"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text(
        "---\nname: demo\ndescription: original description\n---\n\n# demo\n"
    )

    runner = CliRunner()

    async def fake_run(kb_dir, skill_name, intent, model):
        _fake_compile(kb_dir, skill_name)

    with (
        patch("openkb.cli._find_kb_dir", return_value=kb),
        patch("openkb.skill.generator.run_skill_create", new=AsyncMock(side_effect=fake_run)),
    ):
        result = runner.invoke(cli, ["skill", "new", "demo", "x", "-y"])

    assert result.exit_code == 0, result.output
    iter1 = kb / "output" / "skills" / "demo-workspace" / "iteration-1"
    assert iter1.is_dir()
    assert (iter1 / "SKILL.md").exists()
    assert "original description" in (iter1 / "SKILL.md").read_text()
    # And a diff.md is dropped against the previous version
    assert (iter1 / "diff.md").exists()


def test_skill_history_command_lists_iterations(tmp_path):
    """`openkb skill history <name>` lists existing iteration directories."""
    kb = _make_kb(tmp_path)
    ws = kb / "output" / "skills" / "demo-workspace"
    (ws / "iteration-1").mkdir(parents=True)
    (ws / "iteration-1" / "SKILL.md").write_text("---\nname: demo\ndescription: v1\n---\n")
    (ws / "iteration-2").mkdir(parents=True)
    (ws / "iteration-2" / "SKILL.md").write_text("---\nname: demo\ndescription: v2\n---\n")

    runner = CliRunner()
    with patch("openkb.cli._find_kb_dir", return_value=kb):
        result = runner.invoke(cli, ["skill", "history", "demo"])

    assert result.exit_code == 0, result.output
    assert "iteration-1" in result.output
    assert "iteration-2" in result.output


def test_skill_history_command_when_no_iterations(tmp_path):
    kb = _make_kb(tmp_path)
    runner = CliRunner()
    with patch("openkb.cli._find_kb_dir", return_value=kb):
        result = runner.invoke(cli, ["skill", "history", "demo"])
    assert result.exit_code == 0, result.output
    assert "No previous iterations" in result.output


def test_skill_rollback_restores_from_workspace(tmp_path):
    """`openkb skill rollback <name>` copies the latest iteration back
    into output/skills/<name>/."""
    kb = _make_kb(tmp_path)
    ws = kb / "output" / "skills" / "demo-workspace"
    (ws / "iteration-1").mkdir(parents=True)
    (ws / "iteration-1" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: restored\n---\n\n# demo\n"
    )
    # Current skill is "broken"
    current = kb / "output" / "skills" / "demo"
    current.mkdir(parents=True)
    (current / "SKILL.md").write_text("---\nname: demo\ndescription: broken\n---\n")

    runner = CliRunner()
    with patch("openkb.cli._find_kb_dir", return_value=kb):
        result = runner.invoke(cli, ["skill", "rollback", "demo", "-y"])

    assert result.exit_code == 0, result.output
    text = (current / "SKILL.md").read_text()
    assert "restored" in text
    assert "broken" not in text
    # Marketplace manifest regenerated
    assert (kb / ".claude-plugin" / "marketplace.json").exists()


def test_skill_rollback_to_specific_iteration(tmp_path):
    kb = _make_kb(tmp_path)
    ws = kb / "output" / "skills" / "demo-workspace"
    (ws / "iteration-1").mkdir(parents=True)
    (ws / "iteration-1" / "SKILL.md").write_text("---\nname: demo\ndescription: v1\n---\n")
    (ws / "iteration-2").mkdir(parents=True)
    (ws / "iteration-2" / "SKILL.md").write_text("---\nname: demo\ndescription: v2\n---\n")
    current = kb / "output" / "skills" / "demo"
    current.mkdir(parents=True)
    (current / "SKILL.md").write_text("placeholder")

    runner = CliRunner()
    with patch("openkb.cli._find_kb_dir", return_value=kb):
        result = runner.invoke(cli, ["skill", "rollback", "demo", "--to", "1", "-y"])

    assert result.exit_code == 0, result.output
    assert "v1" in (current / "SKILL.md").read_text()


def test_skill_rollback_errors_when_no_iterations(tmp_path):
    kb = _make_kb(tmp_path)
    runner = CliRunner()
    with patch("openkb.cli._find_kb_dir", return_value=kb):
        result = runner.invoke(cli, ["skill", "rollback", "demo", "-y"])
    assert result.exit_code != 0
    assert "No iterations" in result.output


def test_skill_validate_passes_on_valid_skill(tmp_path):
    """`openkb skill validate <name>` exits 0 and prints OK for a well-formed skill."""
    kb = _make_kb(tmp_path)
    target = kb / "output" / "skills" / "demo"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text(
        "---\nname: demo\n"
        "description: A useful and descriptive activation signal for this skill.\n"
        "---\n\n# demo\n"
    )

    runner = CliRunner()
    with patch("openkb.cli._find_kb_dir", return_value=kb):
        result = runner.invoke(cli, ["skill", "validate", "demo"])

    assert result.exit_code == 0, result.output
    assert "[OK]" in result.output
    assert "demo" in result.output


def test_skill_validate_fails_on_invalid_frontmatter(tmp_path):
    """`openkb skill validate <name>` exits non-zero on malformed YAML."""
    kb = _make_kb(tmp_path)
    target = kb / "output" / "skills" / "broken"
    target.mkdir(parents=True)
    # No frontmatter at all — must error out.
    (target / "SKILL.md").write_text("# just a body, no frontmatter\n")

    runner = CliRunner()
    with patch("openkb.cli._find_kb_dir", return_value=kb):
        result = runner.invoke(cli, ["skill", "validate", "broken"])

    assert result.exit_code != 0, result.output
    assert "ERROR" in result.output
    assert "[FAIL]" in result.output


def test_skill_new_keeps_existing_skill_when_key_setup_fails(tmp_path):
    """If LLM key setup raises (e.g. no API key), the old skill output
    must be preserved — don't rmtree before key setup is verified."""
    kb = _make_kb(tmp_path)
    target = kb / "output" / "skills" / "demo"
    target.mkdir(parents=True)
    (target / "stale.txt").write_text("priceless")

    runner = CliRunner()
    with (
        patch("openkb.cli._find_kb_dir", return_value=kb),
        patch("openkb.cli._setup_llm_key", side_effect=RuntimeError("no API key configured")),
    ):
        result = runner.invoke(cli, ["skill", "new", "demo", "x", "-y"])

    assert result.exit_code != 0
    # Old skill must still be there
    assert (target / "stale.txt").read_text() == "priceless"


# --------------------------------------------------------------------------
# `openkb skill eval` — trigger-accuracy evaluator
# --------------------------------------------------------------------------


def _make_skill_dir(kb_dir, name="demo", description="Triggers for demo questions."):
    """Create a minimal compiled skill on disk under <kb>/output/skills/<name>."""
    skill_dir = kb_dir / "output" / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    return skill_dir


def test_skill_eval_runs_with_provided_eval_set(tmp_path):
    """Pass a pre-saved eval set + a perfect-grader mock — expect 100% pass."""
    kb = _make_kb(tmp_path)
    _make_skill_dir(kb, "demo")

    # Save an eval set we can point --eval-set at.
    eval_dir = kb / ".openkb" / "eval-sets"
    eval_dir.mkdir(parents=True)
    eval_path = eval_dir / "demo.json"
    eval_path.write_text(
        json.dumps(
            {
                "should_trigger": ["t0", "t1"],
                "should_not": ["n0", "n1"],
            }
        )
    )

    async def perfect_grader(description, question, *, model):
        return "trigger" if question.startswith("t") else "no-trigger"

    async def perfect_coverage(content, question, *, model):
        return "supported", ""

    runner = CliRunner()
    with (
        patch("openkb.cli._find_kb_dir", return_value=kb),
        patch("openkb.cli._setup_llm_key", return_value=None),
        patch("openkb.skill.evaluator.grade_one", side_effect=perfect_grader),
        patch("openkb.skill.evaluator.grade_coverage", side_effect=perfect_coverage),
    ):
        result = runner.invoke(
            cli,
            [
                "skill",
                "eval",
                "demo",
                "--eval-set",
                str(eval_path),
            ],
        )

    assert result.exit_code == 0, result.output
    assert "Trigger accuracy" in result.output
    assert "4/4" in result.output
    assert "Body coverage" in result.output
    assert "All prompts graded correctly" in result.output


def test_skill_eval_reports_misses(tmp_path):
    """Grader always returns 'trigger' — the no-trigger half must show as misses."""
    kb = _make_kb(tmp_path)
    _make_skill_dir(kb, "demo")

    eval_dir = kb / ".openkb" / "eval-sets"
    eval_dir.mkdir(parents=True)
    eval_path = eval_dir / "demo.json"
    eval_path.write_text(
        json.dumps(
            {
                "should_trigger": ["t0", "t1"],
                "should_not": ["n0", "n1"],
            }
        )
    )

    async def biased_grader(description, question, *, model):
        return "trigger"

    async def perfect_coverage(content, question, *, model):
        return "supported", ""

    runner = CliRunner()
    with (
        patch("openkb.cli._find_kb_dir", return_value=kb),
        patch("openkb.cli._setup_llm_key", return_value=None),
        patch("openkb.skill.evaluator.grade_one", side_effect=biased_grader),
        patch("openkb.skill.evaluator.grade_coverage", side_effect=perfect_coverage),
    ):
        result = runner.invoke(
            cli,
            [
                "skill",
                "eval",
                "demo",
                "--eval-set",
                str(eval_path),
            ],
        )

    assert result.exit_code == 0, result.output
    assert "Trigger accuracy" in result.output
    assert "2/4" in result.output
    assert "Trigger misses (2)" in result.output
    # Each missed prompt must be listed in the output
    assert "n0" in result.output
    assert "n1" in result.output
