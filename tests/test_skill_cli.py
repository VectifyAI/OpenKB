"""End-to-end tests for `openkb skill new` via click.testing.CliRunner.

The agent runner is patched so these tests don't burn LLM tokens. They
verify the CLI wiring: KB detection, name validation, overwrite logic,
marketplace.json regeneration, exit codes."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from openkb.cli import cli


def _make_kb(tmp_path):
    (tmp_path / ".openkb").mkdir()
    (tmp_path / ".openkb" / "config.yaml").write_text("model: gpt-4o-mini\n")
    (tmp_path / "wiki" / "concepts").mkdir(parents=True)
    (tmp_path / "wiki" / "summaries").mkdir(parents=True)
    (tmp_path / "wiki" / "index.md").write_text("# index\n")
    return tmp_path


def _fake_compile(kb_dir, skill_name, **_kw):
    """Side-effect for the patched run_skill_compile: write a minimal SKILL.md."""
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

    with patch("openkb.cli._find_kb_dir", return_value=kb), \
         patch("openkb.generator.run_skill_compile", new=AsyncMock(side_effect=fake_run)):
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

    with patch("openkb.cli._find_kb_dir", return_value=kb), \
         patch("openkb.generator.run_skill_compile", new=AsyncMock(side_effect=fake_run)):
        result = runner.invoke(cli, ["skill", "new", "demo", "x", "-y"])

    assert result.exit_code == 0, result.output
    assert not (kb / "output" / "skills" / "demo" / "stale.txt").exists()
    assert (kb / "output" / "skills" / "demo" / "SKILL.md").exists()
