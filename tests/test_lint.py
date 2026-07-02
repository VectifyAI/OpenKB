"""Tests for openkb.lint (Task 13)."""

from __future__ import annotations

from pathlib import Path

from openkb.lint import (
    _EXCLUDED_FILES,
    _load_wiki_pages,
    _normalize_target,
    build_norm_index,
    check_index_sync,
    find_broken_links,
    find_invalid_frontmatter,
    find_missing_entries,
    find_missing_okf_fields,
    find_orphans,
    fix_broken_links,
    list_existing_wiki_targets,
    run_structural_lint,
    strip_ghost_wikilinks,
)
from openkb.state import HashRegistry


def _make_wiki(tmp_path: Path) -> Path:
    """Create a minimal wiki directory structure."""
    wiki = tmp_path / "wiki"
    (wiki / "sources").mkdir(parents=True)
    (wiki / "summaries").mkdir(parents=True)
    (wiki / "concepts").mkdir(parents=True)
    (wiki / "reports").mkdir(parents=True)
    (wiki / "index.md").write_text("# Index\n\n## Documents\n\n## Concepts\n", encoding="utf-8")
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
        (wiki / "summaries" / "paper.md").write_text("See [[concepts/attention]]", encoding="utf-8")

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

    def test_qualified_link_does_not_hide_same_stem_orphan(self, tmp_path):
        # A qualified link [[concepts/dup]] must not mark a *different* page
        # that merely shares the "dup" stem (summaries/dup) as linked — that
        # page is a genuine orphan and must still be flagged.
        wiki = _make_wiki(tmp_path)
        (wiki / "concepts" / "dup.md").write_text("# Linked concept", encoding="utf-8")
        (wiki / "summaries" / "linker.md").write_text("See [[concepts/dup]]", encoding="utf-8")
        (wiki / "summaries" / "dup.md").write_text(
            "Orphan sharing the 'dup' stem, with no links.", encoding="utf-8"
        )

        result = find_orphans(wiki)

        assert "summaries/dup" in result
        assert "concepts/dup" not in result

    def test_bare_stem_link_still_matches_same_stem_page(self, tmp_path):
        # A bare [[dup]] link (no path) intentionally resolves by stem, so a
        # page with that stem is not considered an orphan.
        wiki = _make_wiki(tmp_path)
        (wiki / "concepts" / "dup.md").write_text("# A concept", encoding="utf-8")
        (wiki / "summaries" / "linker.md").write_text("See [[dup]]", encoding="utf-8")

        result = find_orphans(wiki)

        assert "concepts/dup" not in result


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


class TestFindMissingEntriesRegistry:
    """With ``kb_dir``, raw files resolve through the hash registry first.

    Watch-mode/URL-ingested files keep their original filename in raw/
    (e.g. arXiv ``2509.11420.pdf``) while artifacts are named by the
    sanitized ``doc_name`` (``2509-11420.md``) — a pure stem comparison
    false-positives those as missing. The registry maps one to the other.
    """

    def _add_entry(self, kb: Path, file_hash: str, metadata: dict) -> None:
        HashRegistry(kb / ".openkb" / "hashes.json").add(file_hash, metadata)

    def test_registry_doc_name_resolves_renamed_artifacts(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        raw = tmp_path / "raw"
        raw.mkdir()
        (raw / "2509.11420.pdf").write_bytes(b"%PDF-1.4 fake")
        (wiki / "sources" / "2509-11420.md").write_text("x", encoding="utf-8")
        (wiki / "summaries" / "2509-11420.md").write_text("x", encoding="utf-8")
        self._add_entry(
            tmp_path,
            "h1",
            {
                "name": "2509.11420.pdf",
                "doc_name": "2509-11420",
                "type": "long_pdf",
                "raw_path": "raw/2509.11420.pdf",
            },
        )

        result = find_missing_entries(raw, wiki, kb_dir=tmp_path)

        assert "2509.11420.pdf" not in result

    def test_registry_doc_name_resolves_collision_suffix(self, tmp_path):
        # raw/paper.pdf whose artifacts got a collision suffix
        wiki = _make_wiki(tmp_path)
        raw = tmp_path / "raw"
        raw.mkdir()
        (raw / "paper.pdf").write_bytes(b"%PDF-1.4 fake")
        (wiki / "sources" / "paper-ab12cd34.md").write_text("x", encoding="utf-8")
        self._add_entry(
            tmp_path,
            "h2",
            {
                "name": "paper.pdf",
                "doc_name": "paper-ab12cd34",
                "type": "pdf",
                "raw_path": "raw/paper.pdf",
            },
        )

        result = find_missing_entries(raw, wiki, kb_dir=tmp_path)

        assert result == []

    def test_registry_doc_name_json_source_counts_as_entry(self, tmp_path):
        # Long docs store sources/{doc_name}.json instead of .md
        wiki = _make_wiki(tmp_path)
        raw = tmp_path / "raw"
        raw.mkdir()
        (raw / "2509.11420.pdf").write_bytes(b"%PDF-1.4 fake")
        (wiki / "sources" / "2509-11420.json").write_text("{}", encoding="utf-8")
        self._add_entry(
            tmp_path,
            "h3",
            {
                "name": "2509.11420.pdf",
                "doc_name": "2509-11420",
                "type": "long_pdf",
                "raw_path": "raw/2509.11420.pdf",
            },
        )

        result = find_missing_entries(raw, wiki, kb_dir=tmp_path)

        assert result == []

    def test_unregistered_file_without_artifacts_still_missing(self, tmp_path):
        # No registry entry, no artifacts → stays reported missing
        wiki = _make_wiki(tmp_path)
        raw = tmp_path / "raw"
        raw.mkdir()
        (raw / "unprocessed.pdf").write_bytes(b"PDF content")

        result = find_missing_entries(raw, wiki, kb_dir=tmp_path)

        assert "unprocessed.pdf" in result

    def test_legacy_stem_match_without_registry_not_missing(self, tmp_path):
        # No registry entry but artifacts share the raw stem → old behavior
        wiki = _make_wiki(tmp_path)
        raw = tmp_path / "raw"
        raw.mkdir()
        (raw / "legacy.pdf").write_bytes(b"PDF content")
        (wiki / "sources" / "legacy.md").write_text("# Legacy", encoding="utf-8")

        result = find_missing_entries(raw, wiki, kb_dir=tmp_path)

        assert result == []

    def test_registered_file_with_no_artifacts_is_missing(self, tmp_path):
        # Registry entry exists but its doc_name artifacts were deleted
        wiki = _make_wiki(tmp_path)
        raw = tmp_path / "raw"
        raw.mkdir()
        (raw / "2509.11420.pdf").write_bytes(b"%PDF-1.4 fake")
        self._add_entry(
            tmp_path,
            "h4",
            {
                "name": "2509.11420.pdf",
                "doc_name": "2509-11420",
                "type": "long_pdf",
                "raw_path": "raw/2509.11420.pdf",
            },
        )

        result = find_missing_entries(raw, wiki, kb_dir=tmp_path)

        assert "2509.11420.pdf" in result


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
        (wiki / "index.md").write_text("# Index\n\n## Documents\n- [[summaries/ghost]]\n")

        result = check_index_sync(wiki)

        assert any("ghost" in issue for issue in result)

    def test_page_not_in_index(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        (wiki / "summaries" / "unlisted.md").write_text("# Unlisted")
        # index.md has no mention of unlisted

        result = check_index_sync(wiki)

        assert any("unlisted" in issue for issue in result)

    def test_entity_page_not_in_index(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        (wiki / "entities").mkdir()
        (wiki / "entities" / "ada-lovelace.md").write_text("# Ada Lovelace")
        # index.md has no mention of the entity
        (wiki / "index.md").write_text("# Index\n\n## Documents\n\n## Concepts\n\n## Entities\n")

        result = check_index_sync(wiki)

        assert any(
            "entities/ada-lovelace.md not mentioned in index.md" in issue for issue in result
        )

    def test_missing_index_md(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()

        result = check_index_sync(wiki)

        assert any("does not exist" in issue for issue in result)


class TestRunStructuralLint:
    def test_returns_markdown_report(self, tmp_path):
        _make_wiki(tmp_path)
        raw = tmp_path / "raw"
        raw.mkdir()

        report = run_structural_lint(tmp_path)

        assert "Structural Lint Report" in report
        assert "Broken Links" in report
        assert "Orphaned Pages" in report
        assert "Raw Files Without Wiki Entry" in report
        assert "Index Sync" in report

    def test_clean_kb_shows_no_issues(self, tmp_path):
        _make_wiki(tmp_path)
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
        text = "See [[concepts/gist_memory]] and [[concepts/attention]] and [[concepts/missing]]."

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


class TestFixBrokenLinksRestrictTo:
    """Issue #58 / Bug 2: ``fix_broken_links`` must support scoping the
    rewrite to a caller-supplied subset of files so ``openkb remove``
    can clean up only the pages it actually touched (modified concept
    pages ∪ index.md) instead of sweeping the entire wiki and stripping
    pre-existing dangling links the user may want to keep.
    """

    def test_default_behavior_scans_all_files(self, tmp_path):
        """Calling ``fix_broken_links(wiki)`` without ``restrict_to``
        still processes every wiki file — the existing global behavior
        is preserved for callers that want it (e.g. ``openkb lint --fix``).
        """
        wiki = _make_wiki(tmp_path)
        a = wiki / "concepts" / "a.md"
        b = wiki / "concepts" / "b.md"
        a.write_text("# A\n\nLink [[concepts/ghost]] here.\n", encoding="utf-8")
        b.write_text("# B\n\nLink [[concepts/ghost]] too.\n", encoding="utf-8")

        files_changed, ghosts = fix_broken_links(wiki)

        assert files_changed == 2
        assert ghosts == 2
        assert "[[concepts/ghost]]" not in a.read_text()
        assert "[[concepts/ghost]]" not in b.read_text()

    def test_restrict_to_only_touches_listed_files(self, tmp_path):
        """When ``restrict_to`` is provided, only those files are
        rewritten — even if pre-existing ghost links exist elsewhere
        in the wiki, those files are left alone.
        """
        wiki = _make_wiki(tmp_path)
        touched = wiki / "concepts" / "touched.md"
        untouched = wiki / "concepts" / "untouched.md"
        touched.write_text(
            "# touched\n\nGhost [[concepts/ghost]] here.\n",
            encoding="utf-8",
        )
        untouched.write_text(
            "# untouched\n\nGhost [[concepts/ghost]] here.\n",
            encoding="utf-8",
        )

        files_changed, ghosts = fix_broken_links(wiki, restrict_to=[touched])

        assert files_changed == 1
        assert ghosts == 1
        assert "[[concepts/ghost]]" not in touched.read_text()
        # Untouched file keeps its pre-existing ghost link verbatim.
        assert "[[concepts/ghost]]" in untouched.read_text()

    def test_restrict_to_empty_list_is_noop(self, tmp_path):
        """An empty ``restrict_to`` means "process nothing" (not "fall
        back to wiki-wide"). The whole point of the parameter is letting
        the CLI say "I touched zero files; don't sweep the wiki on my
        behalf."
        """
        wiki = _make_wiki(tmp_path)
        a = wiki / "concepts" / "a.md"
        a.write_text("# A\n\nGhost [[concepts/ghost]] here.\n", encoding="utf-8")

        files_changed, ghosts = fix_broken_links(wiki, restrict_to=[])

        assert files_changed == 0
        assert ghosts == 0
        assert "[[concepts/ghost]]" in a.read_text()

    def test_restrict_to_skips_paths_not_under_wiki(self, tmp_path):
        """Defensive: a path that doesn't live under ``wiki`` (e.g. a
        leftover absolute path from the caller) is silently skipped
        rather than rewriting an unrelated file.
        """
        wiki = _make_wiki(tmp_path)
        stray = tmp_path / "stray.md"
        stray.write_text("# stray\n[[concepts/ghost]]\n", encoding="utf-8")
        before = stray.read_text()

        files_changed, ghosts = fix_broken_links(wiki, restrict_to=[stray])

        assert files_changed == 0
        assert ghosts == 0
        assert stray.read_text() == before

    def test_restrict_to_uses_global_known_targets(self, tmp_path):
        """The valid-target set must still be computed from the whole
        wiki — restricting only narrows which files get *rewritten*,
        not what counts as a valid link target. Without this,
        ``[[concepts/sibling]]`` in the file under review would be
        misclassified as a ghost just because ``sibling.md`` is outside
        ``restrict_to``.
        """
        wiki = _make_wiki(tmp_path)
        (wiki / "concepts" / "sibling.md").write_text("# sibling", encoding="utf-8")
        target = wiki / "concepts" / "target.md"
        target.write_text(
            "Valid [[concepts/sibling]] and ghost [[concepts/ghost]]\n",
            encoding="utf-8",
        )

        files_changed, ghosts = fix_broken_links(wiki, restrict_to=[target])

        assert files_changed == 1
        assert ghosts == 1
        text = target.read_text()
        # Real sibling link survives unchanged.
        assert "[[concepts/sibling]]" in text
        # Ghost link gets demoted.
        assert "[[concepts/ghost]]" not in text


def test_whitelist_includes_entities(tmp_path):
    (tmp_path / "entities").mkdir()
    (tmp_path / "entities" / "anthropic.md").write_text("# A", encoding="utf-8")
    targets = list_existing_wiki_targets(tmp_path)
    assert "entities/anthropic" in targets


def test_flags_missing_type_and_description(tmp_path):
    wiki = tmp_path / "wiki"
    for sub in ("summaries", "concepts", "entities"):
        (wiki / sub).mkdir(parents=True)
    (wiki / "concepts" / "good.md").write_text(
        '---\ntype: "Concept"\ndescription: "ok"\n---\n\n# Good\n', encoding="utf-8"
    )
    (wiki / "concepts" / "no_type.md").write_text(
        '---\ndescription: "x"\n---\n\n# Bad\n', encoding="utf-8"
    )
    (wiki / "summaries" / "no_desc.md").write_text(
        '---\ntype: "Summary"\n---\n\n# Bad\n', encoding="utf-8"
    )
    issues = find_missing_okf_fields(wiki)
    assert any("no_type.md" in i and "type" in i for i in issues)
    assert any("no_desc.md" in i and "description" in i for i in issues)
    assert not any("good.md" in i for i in issues)


def test_flags_null_type_as_missing(tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "concepts").mkdir(parents=True)
    (wiki / "concepts" / "null_type.md").write_text(
        '---\ntype: null\ndescription: "x"\n---\n\n# Bad\n', encoding="utf-8"
    )
    issues = find_missing_okf_fields(wiki)
    assert any("null_type.md" in i and "type" in i for i in issues)


def test_flags_non_string_type_as_missing(tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "concepts").mkdir(parents=True)
    (wiki / "concepts" / "bool_type.md").write_text(
        '---\ntype: true\ndescription: "x"\n---\n\n# Bad\n', encoding="utf-8"
    )
    issues = find_missing_okf_fields(wiki)
    assert any("bool_type.md" in i and "type" in i for i in issues)


class TestLoadWikiPages:
    """Tests for :func:`_load_wiki_pages` scope and exclusion rules."""

    def test_excludes_reports_directory(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        (wiki / "reports" / "run.md").write_text("# Report", encoding="utf-8")

        pages = _load_wiki_pages(wiki)

        assert not any(p.parts[-2] == "reports" for p in pages)

    def test_excludes_sources_directory(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        (wiki / "sources" / "doc.md").write_text("# Source", encoding="utf-8")

        pages = _load_wiki_pages(wiki)

        assert not any(p.parts[-2] == "sources" for p in pages)

    def test_excludes_excluded_files(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        for name in _EXCLUDED_FILES:
            (wiki / name).write_text("# excluded", encoding="utf-8")

        pages = _load_wiki_pages(wiki)

        assert not any(p.name in _EXCLUDED_FILES for p in pages)

    def test_includes_summaries_concepts_entities(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        (wiki / "entities").mkdir(parents=True, exist_ok=True)
        (wiki / "summaries" / "paper.md").write_text("x", encoding="utf-8")
        (wiki / "concepts" / "idea.md").write_text("x", encoding="utf-8")
        (wiki / "entities" / "person.md").write_text("x", encoding="utf-8")

        pages = _load_wiki_pages(wiki)

        paths = {p.name for p in pages}
        assert "paper.md" in paths
        assert "idea.md" in paths
        assert "person.md" in paths

    def test_shared_pages_yields_identical_results_find_invalid_frontmatter(self, tmp_path):
        """Calling ``find_invalid_frontmatter`` with a pre-loaded ``pages``
        dict must produce the same issues as calling it without one."""
        wiki = _make_wiki(tmp_path)
        (wiki / "concepts" / "bad.md").write_text(
            "---\nkey: value: broken\n---\n\n# Bad\n", encoding="utf-8"
        )
        (wiki / "concepts" / "good.md").write_text(
            '---\ntype: "Concept"\n---\n\n# Good\n', encoding="utf-8"
        )

        standalone = find_invalid_frontmatter(wiki)
        shared = find_invalid_frontmatter(wiki, pages=_load_wiki_pages(wiki))

        assert standalone == shared

    def test_shared_pages_yields_identical_results_find_missing_okf_fields(self, tmp_path):
        """Calling ``find_missing_okf_fields`` with a pre-loaded ``pages``
        dict must produce the same issues as calling it without one."""
        wiki = _make_wiki(tmp_path)
        (wiki / "concepts" / "missing_type.md").write_text(
            '---\ndescription: "ok"\n---\n\n# Bad\n', encoding="utf-8"
        )
        (wiki / "summaries" / "complete.md").write_text(
            '---\ntype: "Summary"\ndescription: "fine"\n---\n\n# Good\n',
            encoding="utf-8",
        )

        standalone = find_missing_okf_fields(wiki)
        shared = find_missing_okf_fields(wiki, pages=_load_wiki_pages(wiki))

        assert standalone == shared
