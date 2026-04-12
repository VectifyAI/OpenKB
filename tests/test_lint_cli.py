"""Tests for the openkb lint CLI command."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from openkb.cli import cli


def _setup_kb(tmp_path: Path) -> Path:
    """Create a minimal KB structure and return kb_dir."""
    kb_dir = tmp_path
    (kb_dir / "raw").mkdir()
    (kb_dir / "wiki" / "sources" / "images").mkdir(parents=True)
    (kb_dir / "wiki" / "summaries").mkdir(parents=True)
    (kb_dir / "wiki" / "concepts").mkdir(parents=True)
    (kb_dir / "wiki" / "reports").mkdir(parents=True)
    openkb_dir = kb_dir / ".openkb"
    openkb_dir.mkdir()
    (openkb_dir / "config.yaml").write_text("model: gpt-4o-mini\n")
    (openkb_dir / "hashes.json").write_text(json.dumps({}))
    (kb_dir / "wiki" / "index.md").write_text(
        "# Knowledge Base Index\n\n## Documents\n\n## Concepts\n"
    )
    return kb_dir


class TestLintCommand:
    def test_lint_empty_kb_skips(self, tmp_path):
        """Lint on an empty KB (no indexed docs) should exit early."""
        kb_dir = _setup_kb(tmp_path)
        runner = CliRunner()
        with patch("openkb.cli._find_kb_dir", return_value=kb_dir):
            result = runner.invoke(cli, ["lint"])
        assert result.exit_code == 0
        assert "Nothing to lint" in result.output
        assert "no documents indexed" in result.output
        # No report should be written
        reports = list((kb_dir / "wiki" / "reports").glob("*.md"))
        assert reports == []

    def test_lint_no_hashes_file_skips(self, tmp_path):
        """Lint should also skip when hashes.json doesn't exist."""
        kb_dir = _setup_kb(tmp_path)
        (kb_dir / ".openkb" / "hashes.json").unlink()
        runner = CliRunner()
        with patch("openkb.cli._find_kb_dir", return_value=kb_dir):
            result = runner.invoke(cli, ["lint"])
        assert result.exit_code == 0
        assert "Nothing to lint" in result.output

    def test_lint_no_kb(self, tmp_path):
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path), \
             patch("openkb.cli._find_kb_dir", return_value=None):
            result = runner.invoke(cli, ["lint"])
            assert "No knowledge base found" in result.output

    def test_lint_runs_when_docs_exist(self, tmp_path):
        """Lint should proceed when there are indexed documents."""
        kb_dir = _setup_kb(tmp_path)
        hashes = {"abc": {"name": "paper.pdf", "type": "pdf"}}
        (kb_dir / ".openkb" / "hashes.json").write_text(json.dumps(hashes))
        runner = CliRunner()
        with patch("openkb.cli._find_kb_dir", return_value=kb_dir), \
             patch("openkb.cli._setup_llm_key"), \
             patch("openkb.agent.linter.run_knowledge_lint", return_value="No issues."):
            result = runner.invoke(cli, ["lint"])
        assert result.exit_code == 0
        assert "Running structural lint" in result.output
        assert "Running knowledge lint" in result.output
        assert "Report written to" in result.output
