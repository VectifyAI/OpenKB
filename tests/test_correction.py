"""Tests for openkb.agent.correction."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openkb.agent.correction import (
    CorrectionRunResult,
    build_correction_agent,
    collect_related_files,
    run_correction,
    _preserve_existing_frontmatter,
)
from openkb.schema import SCHEMA_MD


class TestBuildCorrectionAgent:
    def test_agent_name(self, tmp_path):
        agent = build_correction_agent(str(tmp_path), "strong-model", "concepts/topic.md")
        assert agent.name == "wiki-correction"

    def test_review_mode_has_read_only_tools(self, tmp_path):
        agent = build_correction_agent(str(tmp_path), "strong-model", "concepts/topic.md")
        names = {t.name for t in agent.tools}
        assert names == {"list_files", "read_file", "get_page_content"}

    def test_apply_mode_has_target_write_tool(self, tmp_path):
        agent = build_correction_agent(
            str(tmp_path), "strong-model", "concepts/topic.md", apply=True
        )
        names = {t.name for t in agent.tools}
        assert "write_target_file" in names

    def test_apply_mode_instructions_preserve_frontmatter(self, tmp_path):
        agent = build_correction_agent(
            str(tmp_path), "strong-model", "concepts/topic.md", apply=True
        )

        assert "keep all existing YAML frontmatter fields exactly as they are" in agent.instructions

    def test_schema_in_instructions(self, tmp_path):
        agent = build_correction_agent(str(tmp_path), "strong-model", "concepts/topic.md")
        assert SCHEMA_MD in agent.instructions

    def test_agent_model(self, tmp_path):
        agent = build_correction_agent(str(tmp_path), "custom-model", "concepts/topic.md")
        assert agent.model == "litellm/custom-model"


def test_collect_related_files_follows_concept_sources_to_full_text(tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "concepts").mkdir(parents=True)
    (wiki / "summaries").mkdir()
    (wiki / "sources").mkdir()

    (wiki / "concepts" / "topic.md").write_text(
        "---\nsources: [summaries/doc]\n---\n\nClaim text.",
        encoding="utf-8",
    )
    (wiki / "summaries" / "doc.md").write_text(
        "---\nfull_text: sources/doc.md\n---\n\nSummary.",
        encoding="utf-8",
    )
    (wiki / "sources" / "doc.md").write_text("Original source.", encoding="utf-8")

    related = collect_related_files(wiki, "concepts/topic.md")

    assert related == ["summaries/doc.md", "sources/doc.md"]


def test_collect_related_files_skips_pageindex_json_full_text(tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "concepts").mkdir(parents=True)
    (wiki / "summaries").mkdir()
    (wiki / "sources").mkdir()

    (wiki / "concepts" / "topic.md").write_text(
        "---\nsources: [summaries/doc]\n---\n\nClaim text.",
        encoding="utf-8",
    )
    (wiki / "summaries" / "doc.md").write_text(
        "---\ndoc_type: pageindex\nfull_text: sources/doc.json\n---\n\n# Doc\n\nPages 1-10.",
        encoding="utf-8",
    )
    (wiki / "sources" / "doc.json").write_text("[]", encoding="utf-8")

    related = collect_related_files(wiki, "concepts/topic.md")

    assert related == ["summaries/doc.md"]


def test_preserve_existing_frontmatter_for_corrected_body():
    original = "---\nsources: [summaries/doc]\nbrief: Old brief\n---\n\n# Old\n\nBad claim."
    corrected = "# New\n\nCorrected claim."

    written = _preserve_existing_frontmatter(original, corrected)

    assert written == "---\nsources: [summaries/doc]\nbrief: Old brief\n---\n\n# New\n\nCorrected claim."


def test_preserve_existing_frontmatter_replaces_agent_frontmatter():
    original = "---\nsources: [summaries/doc]\nbrief: Old brief\n---\n\n# Old"
    corrected = "---\nsources: []\nbrief: Changed\n---\n\n# New"

    written = _preserve_existing_frontmatter(original, corrected)

    assert "sources: [summaries/doc]" in written
    assert "brief: Old brief" in written
    assert "brief: Changed" not in written
    assert written.endswith("# New")


class TestRunCorrection:
    @pytest.mark.asyncio
    async def test_returns_final_output_and_passes_prompt_context(self, tmp_path):
        wiki = tmp_path / "wiki"
        (tmp_path / ".openkb").mkdir()
        (wiki / "concepts").mkdir(parents=True)
        (wiki / "concepts" / "topic.md").write_text("# Topic\n\nBad claim.")

        captured = {}

        async def fake_run(agent, message, **kwargs):
            captured["agent"] = agent
            captured["message"] = message
            captured["kwargs"] = kwargs
            return MagicMock(final_output="## Verdict\n\nIncorrect.")

        with patch("openkb.agent.correction.Runner.run", side_effect=fake_run):
            result = await run_correction(
                tmp_path, "concepts/topic.md", "Bad claim.", "strong-model"
            )

        assert isinstance(result, CorrectionRunResult)
        assert "Incorrect" in result
        assert result.applied is False
        assert captured["agent"].name == "wiki-correction"
        assert "Target wiki page: concepts/topic.md" in captured["message"]
        assert "Bad claim." in captured["message"]
        assert captured["kwargs"]["max_turns"] > 0

    @pytest.mark.asyncio
    async def test_rejects_sources_as_target(self, tmp_path):
        wiki = tmp_path / "wiki"
        (tmp_path / ".openkb").mkdir()
        (wiki / "sources").mkdir(parents=True)
        (wiki / "sources" / "doc.md").write_text("Evidence.")

        with pytest.raises(ValueError):
            await run_correction(tmp_path, "sources/doc.md", "Claim", "strong-model")

    @pytest.mark.asyncio
    async def test_apply_mode_builds_agent_with_write_tool(self, tmp_path):
        wiki = tmp_path / "wiki"
        (tmp_path / ".openkb").mkdir()
        (wiki / "summaries").mkdir(parents=True)
        (wiki / "summaries" / "doc.md").write_text("# Doc\n\nBad claim.")

        with patch("openkb.agent.correction.Runner.run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = MagicMock(final_output="## Applied\n\nYes.")
            await run_correction(
                tmp_path, "summaries/doc.md", "Bad claim.", "strong-model", apply=True
            )

        agent = mock_run.call_args.args[0]
        names = {t.name for t in agent.tools}
        assert "write_target_file" in names

    @pytest.mark.asyncio
    async def test_apply_mode_reports_not_applied_when_agent_does_not_write(self, tmp_path):
        wiki = tmp_path / "wiki"
        (tmp_path / ".openkb").mkdir()
        (wiki / "summaries").mkdir(parents=True)
        (wiki / "summaries" / "doc.md").write_text("# Doc\n\nAccurate claim.")

        with patch("openkb.agent.correction.Runner.run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = MagicMock(
                final_output="## Verdict\n\nSupported.\n\n## Applied\n\nNo."
            )
            result = await run_correction(
                tmp_path, "summaries/doc.md", "Accurate claim.", "strong-model", apply=True
            )

        assert result.applied is False
