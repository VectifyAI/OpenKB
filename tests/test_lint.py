"""Tests for openkb.lint (Task 13)."""
from __future__ import annotations

from pathlib import Path

import pytest

from openkb.lint import (
    _normalize_target,
    build_norm_index,
    check_index_sync,
    find_broken_links,
    find_missing_entries,
    find_orphans,
    run_structural_lint,
    strip_ghost_wikilinks,
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


class TestNormalizeTarget:
    def test_lowercases(self):
        assert _normalize_target("concepts/AI-Agents") == "concepts/ai-agents"

    def test_underscore_to_hyphen(self):
        assert _normalize_target("concepts/gist_memory") == "concepts/gist-memory"

    def test_nfkc_fullwidth_paren(self):
        # Full-width ） normalizes to ASCII ) via NFKC
        assert _normalize_target("summaries/A（B)") == _normalize_target("summaries/A(B)")

    def test_collapses_repeated_hyphens(self):
        assert _normalize_target("concepts/foo--bar") == "concepts/foo-bar"

    def test_preserves_path_separator(self):
        assert _normalize_target("concepts/Foo") == "concepts/foo"
        # Does not collapse the slash
        assert "/" in _normalize_target("concepts/Foo")

    def test_strips_trailing_hyphens_per_segment(self):
        assert _normalize_target("concepts/-foo-") == "concepts/foo"


class TestStripGhostWikilinks:
    def test_keeps_direct_match(self):
        out, ghosts = strip_ghost_wikilinks(
            "See [[concepts/attention]] for details.",
            {"concepts/attention"},
        )
        assert out == "See [[concepts/attention]] for details."
        assert ghosts == []

    def test_strips_unknown_target(self):
        out, ghosts = strip_ghost_wikilinks(
            "See [[concepts/missing]] for details.",
            {"concepts/attention"},
        )
        assert out == "See missing for details."
        assert ghosts == ["concepts/missing"]

    def test_rewrites_fuzzy_match_underscore_to_hyphen(self):
        out, ghosts = strip_ghost_wikilinks(
            "See [[concepts/gist_memory]] now.",
            {"concepts/gist-memory"},
        )
        assert out == "See [[concepts/gist-memory]] now."
        assert ghosts == []

    def test_rewrites_fuzzy_match_case(self):
        out, ghosts = strip_ghost_wikilinks(
            "See [[concepts/AI-Agents]] now.",
            {"concepts/ai-agents"},
        )
        assert out == "See [[concepts/ai-agents]] now."
        assert ghosts == []

    def test_rewrites_fuzzy_match_unicode(self):
        # File on disk has full-width ）
        known = {"summaries/Agent 对接说明文档（已修订）"}
        # LLM wrote ASCII )
        out, ghosts = strip_ghost_wikilinks(
            "See [[summaries/Agent 对接说明文档（已修订)]].",
            known,
        )
        assert "[[summaries/Agent 对接说明文档（已修订）]]" in out
        assert ghosts == []

    def test_preserves_alias_on_direct_match(self):
        out, ghosts = strip_ghost_wikilinks(
            "See [[concepts/attention|the attention mechanism]].",
            {"concepts/attention"},
        )
        assert "[[concepts/attention|the attention mechanism]]" in out
        assert ghosts == []

    def test_preserves_alias_on_fuzzy_match(self):
        out, ghosts = strip_ghost_wikilinks(
            "See [[concepts/gist_memory|gist memory]].",
            {"concepts/gist-memory"},
        )
        assert "[[concepts/gist-memory|gist memory]]" in out

    def test_uses_alias_as_display_when_stripped(self):
        out, ghosts = strip_ghost_wikilinks(
            "See [[concepts/missing|my term]] now.",
            set(),
        )
        assert out == "See my term now."
        assert ghosts == ["concepts/missing"]

    def test_strips_bare_link_to_readable_text(self):
        # "concepts/multi_head_attention" → "multi head attention"
        out, ghosts = strip_ghost_wikilinks(
            "Uses [[concepts/multi_head_attention]] heavily.",
            set(),
        )
        assert out == "Uses multi head attention heavily."

    def test_handles_multiple_links_mixed(self):
        out, ghosts = strip_ghost_wikilinks(
            "[[concepts/a]] and [[concepts/b]] and [[concepts/c]]",
            {"concepts/a", "concepts/c"},
        )
        assert "[[concepts/a]]" in out
        assert "[[concepts/c]]" in out
        assert "[[concepts/b]]" not in out
        assert ghosts == ["concepts/b"]

    def test_no_wikilinks_returns_unchanged(self):
        text = "Plain markdown with no wikilinks at all."
        out, ghosts = strip_ghost_wikilinks(text, {"concepts/foo"})
        assert out == text
        assert ghosts == []

    def test_empty_known_set_strips_all(self):
        out, ghosts = strip_ghost_wikilinks(
            "[[a]] [[b/c]] [[d]]",
            set(),
        )
        assert "[[" not in out
        assert len(ghosts) == 3

    def test_same_ghost_appearing_multiple_times(self):
        out, ghosts = strip_ghost_wikilinks(
            "[[concepts/x]] and [[concepts/x]] again",
            set(),
        )
        # Each occurrence is recorded separately so callers can count
        assert ghosts == ["concepts/x", "concepts/x"]

    def test_accepts_prebuilt_norm_index_with_identical_result(self):
        """Passing a pre-built ``norm_index`` should produce the same
        result as letting the function build it internally — this is the
        contract that lets ``fix_broken_links`` and ``_save_transcript``
        amortize the index build across many calls.
        """
        known = {"concepts/gist-memory", "concepts/attention"}
        text = (
            "See [[concepts/gist_memory]] and [[concepts/attention]] and "
            "[[concepts/missing]]."
        )

        # Default (no norm_index passed)
        out_a, ghosts_a = strip_ghost_wikilinks(text, known)

        # With pre-built norm_index
        idx = build_norm_index(known)
        out_b, ghosts_b = strip_ghost_wikilinks(text, known, norm_index=idx)

        assert out_a == out_b
        assert ghosts_a == ghosts_b
        # Sanity: fuzzy rewrite, plus one ghost
        assert "[[concepts/gist-memory]]" in out_b
        assert "[[concepts/missing]]" not in out_b


class TestBuildNormIndex:
    def test_returns_normalized_to_canonical_map(self):
        idx = build_norm_index({"concepts/Gist_Memory", "summaries/Paper"})
        assert idx["concepts/gist-memory"] == "concepts/Gist_Memory"
        assert idx["summaries/paper"] == "summaries/Paper"

    def test_empty_set_returns_empty_dict(self):
        assert build_norm_index(set()) == {}
