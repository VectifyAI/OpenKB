"""Tests for the `openkb consolidate` CLI command."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from openkb.cli import cli


def _invoke(kb_dir, args):
    return CliRunner().invoke(cli, ["--kb-dir", str(kb_dir), *args])


def _seed_page_with_notes(kb_dir, page_dir="concepts", slug="approval-workflows"):
    d = kb_dir / "wiki" / page_dir
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{slug}.md").write_text(
        '---\nsources: ["summaries/a.md"]\n---\n\nExisting prose.\n\n## Notes\n\n'
        "- **2026-01-01** A note. ([[summaries/a]])\n",
        encoding="utf-8",
    )
    (kb_dir / "wiki" / "log.md").write_text("# Log\n\n", encoding="utf-8")


class TestConsolidateArgValidation:
    def test_requires_exactly_one_of_name_or_all(self, kb_dir):
        _seed_page_with_notes(kb_dir)
        result = _invoke(kb_dir, ["consolidate"])
        assert result.exit_code == 0
        assert "exactly one" in result.output.lower()

    def test_unknown_page_name(self, kb_dir):
        _seed_page_with_notes(kb_dir)
        result = _invoke(kb_dir, ["consolidate", "nonexistent"])
        assert "No concept/entity page matching" in result.output


class TestConsolidateDryRun:
    def test_dry_run_lists_candidates_no_calls_no_writes(self, kb_dir):
        _seed_page_with_notes(kb_dir)
        path = kb_dir / "wiki" / "concepts" / "approval-workflows.md"
        before = path.read_text(encoding="utf-8")
        with patch(
            "openkb.agent.consolidator.consolidate_page", new_callable=AsyncMock
        ) as mock_consolidate:
            result = _invoke(kb_dir, ["consolidate", "--all", "--dry-run"])

        assert result.exit_code == 0, result.output
        mock_consolidate.assert_not_called()
        assert "approval-workflows" in result.output
        assert "1 note" in result.output
        assert path.read_text(encoding="utf-8") == before

    def test_min_notes_filters_dry_run_candidates(self, kb_dir):
        _seed_page_with_notes(kb_dir)
        with patch(
            "openkb.agent.consolidator.consolidate_page", new_callable=AsyncMock
        ) as mock_consolidate:
            result = _invoke(kb_dir, ["consolidate", "--all", "--min-notes", "5", "--dry-run"])

        assert result.exit_code == 0, result.output
        mock_consolidate.assert_not_called()
        assert "No pages with pending notes found." in result.output


class TestConsolidateExecution:
    def test_single_page_by_name_dispatches_consolidate_page(self, kb_dir):
        _seed_page_with_notes(kb_dir)
        with patch(
            "openkb.agent.consolidator.consolidate_page",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_c:
            result = _invoke(kb_dir, ["consolidate", "approval-workflows"])

        assert result.exit_code == 0, result.output
        mock_c.assert_called_once()
        args = mock_c.call_args.args
        assert args[1] == "concepts"
        assert args[2] == "approval-workflows"
        assert "Done: consolidated 1, skipped 0." in result.output
        log_text = (kb_dir / "wiki" / "log.md").read_text(encoding="utf-8")
        assert "consolidate" in log_text

    def test_all_with_yes_skips_confirmation(self, kb_dir):
        _seed_page_with_notes(kb_dir)
        with patch(
            "openkb.agent.consolidator.consolidate_page",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_c:
            result = _invoke(kb_dir, ["consolidate", "--all", "--yes"])

        assert result.exit_code == 0, result.output
        mock_c.assert_called_once()
        assert "Done: consolidated 1, skipped 0." in result.output

    def test_skip_result_counts_as_skipped(self, kb_dir):
        _seed_page_with_notes(kb_dir)
        with patch(
            "openkb.agent.consolidator.consolidate_page",
            new_callable=AsyncMock,
            return_value=False,
        ):
            result = _invoke(kb_dir, ["consolidate", "approval-workflows"])

        assert result.exit_code == 0, result.output
        assert "Done: consolidated 0, skipped 1." in result.output

    def test_exception_in_one_page_reported_as_error_not_fatal(self, kb_dir):
        _seed_page_with_notes(kb_dir, slug="approval-workflows")
        _seed_page_with_notes(kb_dir, slug="second-page")
        with patch(
            "openkb.agent.consolidator.consolidate_page",
            new_callable=AsyncMock,
            side_effect=[ValueError("boom"), True],
        ):
            result = _invoke(kb_dir, ["consolidate", "--all", "--yes"])

        assert result.exit_code == 0, result.output
        assert "[ERROR] Consolidation failed: boom" in result.output
        assert "Done: consolidated 1, skipped 1." in result.output
