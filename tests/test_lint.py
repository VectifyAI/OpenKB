"""Tests for openkb.lint (Task 13)."""
from __future__ import annotations

from pathlib import Path

import pytest

from openkb.lint import (
    check_index_sync,
    find_broken_links,
    find_missing_entries,
    find_orphans,
    run_structural_lint,
)


def _make_wiki(tmp_path: Path) -> Path:
    """Create a minimal wiki directory structure."""
    wiki = tmp_path / "wiki"
    (wiki / "sources").mkdir(parents=True)
    (wiki / "summaries").mkdir(parents=True)
    (wiki / "concepts").mkdir(parents=True)
    (wiki / "reports").mkdir(parents=True)
    (wiki / "index.md").write_text(
        "# Index\n\n## Documents\n\n## Concepts\n", encoding="utf-8"
    )
    return wiki


class TestFindBrokenLinks:
    def test_no_broken_links(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        (wiki / "concepts" / "attention.md").write_text("# Attention")
        (wiki / "summaries" / "paper.md").write_text(
            "Refers to [[concepts/attention]]", encoding="utf-8"
        )

        result = find_broken_links(wiki)

        assert result == []

    def test_detects_broken_link(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        (wiki / "summaries" / "paper.md").write_text(
            "See [[concepts/missing_concept]]", encoding="utf-8"
        )

        result = find_broken_links(wiki)

        assert len(result) == 1
        assert "missing_concept" in result[0]

    def test_multiple_broken_links(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        (wiki / "summaries" / "doc.md").write_text(
            "See [[concepts/foo]] and [[concepts/bar]]", encoding="utf-8"
        )

        result = find_broken_links(wiki)

        assert len(result) == 2

    def test_no_links_means_no_errors(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        (wiki / "summaries" / "paper.md").write_text("No wikilinks here.")

        result = find_broken_links(wiki)

        assert result == []


class TestFindOrphans:
    def test_linked_page_is_not_orphan(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        (wiki / "concepts" / "attention.md").write_text("# Attention")
        (wiki / "summaries" / "paper.md").write_text(
            "See [[concepts/attention]]", encoding="utf-8"
        )

        result = find_orphans(wiki)

        assert "concepts/attention" not in result

    def test_isolated_page_is_orphan(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        (wiki / "concepts" / "lonely.md").write_text("# Lonely page with no links.")

        result = find_orphans(wiki)

        assert any("lonely" in r for r in result)

    def test_page_with_outgoing_links_not_orphan(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        (wiki / "concepts" / "linking.md").write_text("See [[other/page]].")
        # linking.md has outgoing links so it's not orphaned even if unreferenced

        result = find_orphans(wiki)

        assert "concepts/linking" not in result

    def test_empty_wiki_has_no_orphans(self, tmp_path):
        wiki = _make_wiki(tmp_path)

        result = find_orphans(wiki)

        assert result == []


class TestFindMissingEntries:
    def test_no_missing_entries(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        raw = tmp_path / "raw"
        raw.mkdir()
        (raw / "paper.pdf").write_bytes(b"PDF content")
        (wiki / "sources" / "paper.md").write_text("# Paper")

        result = find_missing_entries(raw, wiki)

        assert result == []

    def test_detects_missing_entry(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        raw = tmp_path / "raw"
        raw.mkdir()
        (raw / "unprocessed.pdf").write_bytes(b"PDF content")
        # No corresponding wiki entry

        result = find_missing_entries(raw, wiki)

        assert "unprocessed.pdf" in result

    def test_summary_counts_as_entry(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        raw = tmp_path / "raw"
        raw.mkdir()
        (raw / "longdoc.pdf").write_bytes(b"PDF")
        (wiki / "summaries" / "longdoc.md").write_text("# Long doc summary")

        result = find_missing_entries(raw, wiki)

        assert "longdoc.pdf" not in result

    def test_empty_raw_means_no_missing(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        raw = tmp_path / "raw"
        raw.mkdir()

        result = find_missing_entries(raw, wiki)

        assert result == []


class TestCheckIndexSync:
    def test_clean_index(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        (wiki / "summaries" / "paper.md").write_text("# Paper")
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n- [[summaries/paper]]\n\n## Concepts\n"
        )

        result = check_index_sync(wiki)

        assert result == []

    def test_broken_index_link(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n- [[summaries/ghost]]\n"
        )

        result = check_index_sync(wiki)

        assert any("ghost" in issue for issue in result)

    def test_page_not_in_index(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        (wiki / "summaries" / "unlisted.md").write_text("# Unlisted")
        # index.md has no mention of unlisted

        result = check_index_sync(wiki)

        assert any("unlisted" in issue for issue in result)

    def test_missing_index_md(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()

        result = check_index_sync(wiki)

        assert any("does not exist" in issue for issue in result)


class TestRunStructuralLint:
    def test_returns_markdown_report(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        raw = tmp_path / "raw"
        raw.mkdir()

        report = run_structural_lint(tmp_path)

        assert "Structural Lint Report" in report
        assert "Broken Links" in report
        assert "Orphaned Pages" in report
        assert "Raw Files Without Wiki Entry" in report
        assert "Index Sync" in report

    def test_clean_kb_shows_no_issues(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        raw = tmp_path / "raw"
        raw.mkdir()

        report = run_structural_lint(tmp_path)

        assert "No broken links found" in report
        assert "No orphaned pages found" in report
        assert "All raw files have wiki entries" in report

    def test_report_includes_broken_link_details(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        raw = tmp_path / "raw"
        raw.mkdir()
        (wiki / "summaries" / "doc.md").write_text("See [[concepts/missing]]")

        report = run_structural_lint(tmp_path)

        assert "missing" in report
