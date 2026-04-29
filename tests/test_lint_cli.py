"""Tests for the openkb lint CLI command."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from openkb.agent.lint_fix import LintFixRunResult
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

        issue_reports = list((kb_dir / "wiki" / "reports").glob("*.issues.json"))
        assert len(issue_reports) == 1
        data = json.loads(issue_reports[0].read_text(encoding="utf-8"))
        assert data["issue_types"] == ["structural", "schema", "knowledge"]
        assert data["fix_requested"] is False

    def test_lint_records_user_feedback_as_knowledge_issue(self, tmp_path):
        kb_dir = _setup_kb(tmp_path)
        hashes = {"abc": {"name": "paper.pdf", "type": "pdf"}}
        (kb_dir / ".openkb" / "hashes.json").write_text(json.dumps(hashes))
        (kb_dir / "wiki" / "concepts" / "topic.md").write_text("Questionable claim.")
        runner = CliRunner()

        with patch("openkb.cli._find_kb_dir", return_value=kb_dir), \
             patch("openkb.cli._setup_llm_key"), \
             patch("openkb.agent.linter.run_knowledge_lint", return_value="No issues."):
            result = runner.invoke(
                cli,
                [
                    "lint",
                    "--page",
                    "concepts/topic.md",
                    "--feedback",
                    "This claim looks wrong.",
                ],
            )

        assert result.exit_code == 0
        issue_reports = list((kb_dir / "wiki" / "reports").glob("*.issues.json"))
        data = json.loads(issue_reports[0].read_text(encoding="utf-8"))
        feedback_issues = [
            issue for issue in data["issues"]
            if issue["type"] == "knowledge" and issue["source"] == "user_feedback"
        ]
        assert len(feedback_issues) == 1
        assert feedback_issues[0]["page"] == "concepts/topic.md"
        assert feedback_issues[0]["description"] == "This claim looks wrong."
        assert feedback_issues[0]["fixable"] is True

    def test_lint_fix_runs_knowledge_fix_for_user_feedback(self, tmp_path):
        kb_dir = _setup_kb(tmp_path)
        hashes = {"abc": {"name": "paper.pdf", "type": "pdf"}}
        (kb_dir / ".openkb" / "hashes.json").write_text(json.dumps(hashes))
        (kb_dir / ".openkb" / "config.yaml").write_text(
            "model: weak-model\nlint_fix_model: strong-model\n"
        )
        (kb_dir / "wiki" / "concepts" / "topic.md").write_text("Questionable claim.")
        runner = CliRunner()

        with patch("openkb.cli._find_kb_dir", return_value=kb_dir), \
             patch("openkb.cli._setup_llm_key"), \
             patch("openkb.agent.linter.run_knowledge_lint", return_value="No issues."), \
             patch("openkb.agent.lint_fix.run_knowledge_fix", new_callable=AsyncMock) as mock_fix:
            mock_fix.return_value = LintFixRunResult(
                output="## Applied\n\nYes.",
                applied=True,
            )
            result = runner.invoke(
                cli,
                [
                    "lint",
                    "--fix",
                    "--page",
                    "concepts/topic.md",
                    "--feedback",
                    "This claim looks wrong.",
                ],
            )

        assert result.exit_code == 0
        assert "Running knowledge fix for user feedback" in result.output
        mock_fix.assert_awaited_once()
        args = mock_fix.call_args.args
        kwargs = mock_fix.call_args.kwargs
        assert args[:4] == (
            kb_dir,
            "concepts/topic.md",
            "This claim looks wrong.",
            "strong-model",
        )
        assert kwargs["apply"] is True

        issue_reports = list((kb_dir / "wiki" / "reports").glob("*.issues.json"))
        data = json.loads(issue_reports[0].read_text(encoding="utf-8"))
        assert data["fix_requested"] is True
        assert data["fix_results"][0]["status"] == "applied"

    def test_lint_fix_falls_back_to_configured_model(self, tmp_path):
        kb_dir = _setup_kb(tmp_path)
        hashes = {"abc": {"name": "paper.pdf", "type": "pdf"}}
        (kb_dir / ".openkb" / "hashes.json").write_text(json.dumps(hashes))
        (kb_dir / ".openkb" / "config.yaml").write_text(
            "model: anthropic/claude-sonnet-4-6\n"
        )
        (kb_dir / "wiki" / "concepts" / "topic.md").write_text("Questionable claim.")
        runner = CliRunner()

        with patch("openkb.cli._find_kb_dir", return_value=kb_dir), \
             patch("openkb.cli._setup_llm_key"), \
             patch("openkb.agent.linter.run_knowledge_lint", return_value="No issues."), \
             patch("openkb.agent.lint_fix.run_knowledge_fix", new_callable=AsyncMock) as mock_fix:
            mock_fix.return_value = LintFixRunResult(output="No change.", applied=False)
            result = runner.invoke(
                cli,
                [
                    "lint",
                    "--fix",
                    "--page",
                    "concepts/topic.md",
                    "--feedback",
                    "This claim looks wrong.",
                ],
            )

        assert result.exit_code == 0
        assert mock_fix.call_args.args[3] == "anthropic/claude-sonnet-4-6"

    def test_lint_page_requires_feedback(self, tmp_path):
        kb_dir = _setup_kb(tmp_path)
        runner = CliRunner()

        with patch("openkb.cli._find_kb_dir", return_value=kb_dir):
            result = runner.invoke(cli, ["lint", "--page", "concepts/topic.md"])

        assert result.exit_code == 0
        assert "Use --page and --feedback together" in result.output
