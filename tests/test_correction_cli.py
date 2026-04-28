"""Tests for the openkb correct CLI command."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from openkb.agent.correction import CorrectionRunResult
from openkb.cli import cli


def _make_kb(tmp_path):
    kb = tmp_path / "kb"
    (kb / ".openkb").mkdir(parents=True)
    (kb / "wiki" / "concepts").mkdir(parents=True)
    (kb / "wiki" / "reports").mkdir()
    (kb / "wiki" / "log.md").write_text("# Operations Log\n\n", encoding="utf-8")
    (kb / ".openkb" / "config.yaml").write_text(
        "model: weak-model\ncorrection_model: strong-model\n",
        encoding="utf-8",
    )
    (kb / "wiki" / "concepts" / "topic.md").write_text(
        "# Topic\n\nQuestionable claim.",
        encoding="utf-8",
    )
    return kb


def test_correct_cli_runs_review_and_writes_report(tmp_path):
    kb = _make_kb(tmp_path)
    runner = CliRunner()

    with patch("openkb.cli._setup_llm_key"), \
         patch("openkb.agent.correction.run_correction", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = "## Verdict\n\nIncorrect."
        result = runner.invoke(
            cli,
            [
                "--kb-dir",
                str(kb),
                "correct",
                "concepts/topic.md",
                "Questionable claim.",
                "--note",
                "Please verify this.",
            ],
        )

    assert result.exit_code == 0
    assert "review mode" in result.output
    assert "strong-model" in result.output
    assert "Incorrect" in result.output
    mock_run.assert_awaited_once()
    args = mock_run.call_args.args
    kwargs = mock_run.call_args.kwargs
    assert args[:4] == (kb, "concepts/topic.md", "Questionable claim.", "strong-model")
    assert kwargs["note"] == "Please verify this."
    assert kwargs["apply"] is False

    reports = list((kb / "wiki" / "reports" / "corrections").glob("*.md"))
    assert len(reports) == 1
    report = reports[0].read_text(encoding="utf-8")
    assert "Questionable claim." in report
    assert "Incorrect" in report


def test_correct_cli_apply_and_model_override(tmp_path):
    kb = _make_kb(tmp_path)
    runner = CliRunner()

    with patch("openkb.cli._setup_llm_key"), \
         patch("openkb.agent.correction.run_correction", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = CorrectionRunResult(
            output="## Applied\n\nYes.",
            applied=True,
        )
        result = runner.invoke(
            cli,
            [
                "--kb-dir",
                str(kb),
                "correct",
                "concepts/topic.md",
                "Questionable claim.",
                "--apply",
                "--model",
                "override-strong",
            ],
        )

    assert result.exit_code == 0
    assert "apply mode" in result.output
    mock_run.assert_awaited_once()
    assert mock_run.call_args.args[3] == "override-strong"
    assert mock_run.call_args.kwargs["apply"] is True

    reports = list((kb / "wiki" / "reports" / "corrections").glob("*.md"))
    assert len(reports) == 1
    report = reports[0].read_text(encoding="utf-8")
    assert "- Apply requested: `True`" in report
    assert "- Applied: `True`" in report


def test_correct_cli_reports_actual_not_applied_status(tmp_path):
    kb = _make_kb(tmp_path)
    runner = CliRunner()

    with patch("openkb.cli._setup_llm_key"), \
         patch("openkb.agent.correction.run_correction", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = CorrectionRunResult(
            output="## Verdict\n\nSupported.\n\n## Applied\n\nNo.",
            applied=False,
        )
        result = runner.invoke(
            cli,
            [
                "--kb-dir",
                str(kb),
                "correct",
                "concepts/topic.md",
                "Questionable claim.",
                "--apply",
            ],
        )

    assert result.exit_code == 0
    reports = list((kb / "wiki" / "reports" / "corrections").glob("*.md"))
    assert len(reports) == 1
    report = reports[0].read_text(encoding="utf-8")
    assert "- Apply requested: `True`" in report
    assert "- Applied: `False`" in report


def test_write_correction_report_uses_millisecond_timestamp_and_unique_suffix(tmp_path):
    from openkb.cli import _write_correction_report

    kb = _make_kb(tmp_path)

    first = _write_correction_report(
        kb, "concepts/topic.md", "Claim.", None, False, "model", "Result."
    )
    second = _write_correction_report(
        kb, "concepts/topic.md", "Claim.", None, False, "model", "Result."
    )

    assert first != second
    assert first.exists()
    assert second.exists()
    timestamp = first.name.split("_topic.md")[0]
    assert len(timestamp.split("_")[-1]) == 3
