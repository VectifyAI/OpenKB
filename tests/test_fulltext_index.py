"""Tests for openkb.fulltext_index (BM25 hybrid search)."""

from __future__ import annotations

import json

from openkb.fulltext_index import Locator, TieredWikiSearch, WikiFullTextIndex


def _write(tmp_path, subdir, name, text):
    directory = tmp_path / subdir
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(text, encoding="utf-8")


class TestWikiFullTextIndex:
    def test_empty_wiki_returns_no_hits(self, tmp_path):
        index = WikiFullTextIndex(str(tmp_path))
        assert index.search("anything") == []

    def test_finds_page_by_keyword_in_body(self, tmp_path):
        _write(
            tmp_path,
            "concepts",
            "cnn.md",
            "# Convolutional Neural Networks\n\nAlexNet popularized ReLU activations "
            "and dropout regularization for large-scale image classification.",
        )
        _write(
            tmp_path,
            "concepts",
            "unrelated.md",
            "# Gardening\n\nTomatoes need plenty of sunlight and water.",
        )

        hits = WikiFullTextIndex(str(tmp_path)).search("dropout regularization")

        assert len(hits) == 1
        assert hits[0].path == "concepts/cnn.md"
        assert hits[0].title == "Convolutional Neural Networks"
        assert hits[0].score > 0

    def test_ranks_more_relevant_page_higher(self, tmp_path):
        _write(
            tmp_path,
            "concepts",
            "on-topic.md",
            "# Topic\n\nAlexNet AlexNet AlexNet training data criticism bias bias.",
        )
        _write(
            tmp_path,
            "concepts",
            "off-topic.md",
            "# Other\n\nA single passing mention of AlexNet in an unrelated paragraph "
            "about something else entirely, padded with filler words to change length.",
        )

        hits = WikiFullTextIndex(str(tmp_path)).search("AlexNet bias")

        assert [hit.path for hit in hits[:1]] == ["concepts/on-topic.md"]

    def test_respects_top_k(self, tmp_path):
        for i in range(10):
            _write(tmp_path, "entities", f"e{i}.md", f"# Entity {i}\n\nkeyword appears here {i}.")

        hits = WikiFullTextIndex(str(tmp_path)).search("keyword", top_k=3)

        assert len(hits) == 3

    def test_only_indexes_page_content_dirs(self, tmp_path):
        _write(tmp_path, "sources", "raw.md", "# Raw\n\nkeyword raw source content.")
        _write(tmp_path, "concepts", "c.md", "# Concept\n\nkeyword concept content.")

        hits = WikiFullTextIndex(str(tmp_path)).search("keyword")

        assert [hit.path for hit in hits] == ["concepts/c.md"]

    def test_falls_back_to_filename_when_no_heading(self, tmp_path):
        _write(tmp_path, "summaries", "no-heading.md", "keyword content without a heading line.")

        hits = WikiFullTextIndex(str(tmp_path)).search("keyword")

        assert hits[0].title == "no-heading"

    def test_no_query_tokens_returns_no_hits(self, tmp_path):
        _write(tmp_path, "concepts", "c.md", "# Concept\n\nkeyword concept content.")

        hits = WikiFullTextIndex(str(tmp_path)).search("   ")

        assert hits == []

    def test_snippet_contains_context_around_match(self, tmp_path):
        _write(
            tmp_path,
            "concepts",
            "c.md",
            "# Concept\n\n"
            + ("padding " * 40)
            + "the exact fee is five hundred dollars"
            + (" more" * 40),
        )

        hits = WikiFullTextIndex(str(tmp_path)).search("fee")

        assert "fee" in hits[0].snippet.lower()


class TestTieredWikiSearchBriefs:
    def test_matches_brief_frontmatter_not_body(self, tmp_path):
        _write(
            tmp_path,
            "summaries",
            "doc-a.md",
            '---\ndescription: "Salesforce Case Management overview"\n---\n\n'
            "# Doc A\n\nUnrelated body text about something else entirely.",
        )

        result = TieredWikiSearch(str(tmp_path)).search("case management", scope=["briefs"])

        assert len(result["briefs"]) == 1
        assert result["briefs"][0].path == "summaries/doc-a.md"

    def test_legacy_brief_key_still_resolves(self, tmp_path):
        _write(
            tmp_path,
            "summaries",
            "doc-a.md",
            '---\nbrief: "legacy field name lookup notes"\n---\n\n# Doc A\n\nBody.',
        )

        result = TieredWikiSearch(str(tmp_path)).search("field name lookup", scope=["briefs"])

        assert len(result["briefs"]) == 1

    def test_no_brief_frontmatter_yields_no_hit(self, tmp_path):
        _write(tmp_path, "summaries", "doc-a.md", "# Doc A\n\nkeyword body text, no frontmatter.")

        result = TieredWikiSearch(str(tmp_path)).search("keyword", scope=["briefs"])

        assert result["briefs"] == []


class TestTieredWikiSearchSummaries:
    def test_matches_full_body_not_just_brief(self, tmp_path):
        _write(
            tmp_path,
            "summaries",
            "doc-a.md",
            '---\ndescription: "General overview"\n---\n\n'
            "# Doc A\n\nDetails about custom_field_xyz appear only here.",
        )

        result = TieredWikiSearch(str(tmp_path)).search("custom_field_xyz", scope=["summaries"])

        assert len(result["summaries"]) == 1
        assert result["summaries"][0].locator is not None
        assert result["summaries"][0].locator.kind == "line"

    def test_frontmatter_block_itself_is_not_indexed(self, tmp_path):
        _write(
            tmp_path,
            "summaries",
            "doc-a.md",
            '---\ndescription: "uniquefrontmatterterm should not match body search"\n---\n\n'
            "# Doc A\n\nUnrelated body.",
        )

        result = TieredWikiSearch(str(tmp_path)).search(
            "uniquefrontmatterterm", scope=["summaries"]
        )

        assert result["summaries"] == []


class TestTieredWikiSearchSources:
    def test_short_source_doc_gets_line_locator(self, tmp_path):
        _write(
            tmp_path,
            "sources",
            "notes.md",
            "Line one.\nLine two.\nAuthor: Jane Doe, created 2024-03-15.\nLine four.",
        )

        result = TieredWikiSearch(str(tmp_path)).search("Jane Doe", scope=["sources"])

        assert len(result["sources"]) == 1
        hit = result["sources"][0]
        assert hit.path == "sources/notes.md"
        assert hit.locator == Locator(kind="line", value=3)

    def test_pageindex_json_hit_gets_page_locator_not_whole_document(self, tmp_path):
        pages = [
            {"page": 1, "content": "Introduction, nothing special here."},
            {"page": 2, "content": "The field_xyz default value is 42."},
            {"page": 3, "content": "Conclusion, also nothing special."},
        ]
        sources_dir = tmp_path / "sources"
        sources_dir.mkdir(parents=True)
        (sources_dir / "long-doc.json").write_text(json.dumps(pages), encoding="utf-8")

        result = TieredWikiSearch(str(tmp_path)).search("field_xyz", scope=["sources"])

        assert len(result["sources"]) == 1
        hit = result["sources"][0]
        assert hit.path == "sources/long-doc.json"
        assert hit.locator == Locator(kind="page", value=2)

    def test_malformed_json_source_is_skipped_not_raised(self, tmp_path):
        sources_dir = tmp_path / "sources"
        sources_dir.mkdir(parents=True)
        (sources_dir / "broken.json").write_text("{not valid json", encoding="utf-8")

        result = TieredWikiSearch(str(tmp_path)).search("anything", scope=["sources"])

        assert result["sources"] == []


class TestTieredWikiSearchScope:
    def test_default_scope_searches_all_four_tiers(self, tmp_path):
        _write(
            tmp_path,
            "summaries",
            "doc.md",
            '---\ndescription: "keyword brief"\n---\n\n# Doc\n\nkeyword body.',
        )
        _write(tmp_path, "sources", "doc.md", "keyword raw source.")
        _write(
            tmp_path,
            "explorations",
            "q1.md",
            '---\nquery: "keyword question"\n---\n\nkeyword answer body.',
        )

        result = TieredWikiSearch(str(tmp_path)).search("keyword")

        assert set(result.keys()) == {"briefs", "summaries", "sources", "explorations"}
        assert len(result["briefs"]) == 1
        assert len(result["summaries"]) == 1
        assert len(result["sources"]) == 1
        assert len(result["explorations"]) == 1

    def test_concepts_and_entities_are_never_searched(self, tmp_path):
        _write(tmp_path, "concepts", "c.md", "# Concept\n\nkeyword concept content.")
        _write(tmp_path, "entities", "e.md", "# Entity\n\nkeyword entity content.")

        result = TieredWikiSearch(str(tmp_path)).search("keyword")

        assert result["briefs"] == []
        assert result["summaries"] == []
        assert result["sources"] == []
        assert result["explorations"] == []

    def test_explorations_is_its_own_tier_not_merged_with_summaries(self, tmp_path):
        _write(
            tmp_path,
            "summaries",
            "doc.md",
            '---\ndescription: "keyword brief"\n---\n\n# Doc\n\nkeyword body.',
        )
        _write(
            tmp_path,
            "explorations",
            "q1.md",
            '---\nquery: "keyword question"\n---\n\nkeyword answer body.',
        )

        result = TieredWikiSearch(str(tmp_path)).search("keyword", scope=["explorations"])

        assert set(result.keys()) == {"explorations"}
        assert len(result["explorations"]) == 1
        assert result["explorations"][0].path == "explorations/q1.md"
        assert result["explorations"][0].title == "keyword question"

    def test_invalid_scope_raises_value_error(self, tmp_path):
        import pytest

        with pytest.raises(ValueError, match="Unknown scope"):
            TieredWikiSearch(str(tmp_path)).search("keyword", scope=["not-a-real-tier"])

    def test_empty_wiki_returns_empty_lists_for_all_tiers(self, tmp_path):
        result = TieredWikiSearch(str(tmp_path)).search("anything")

        assert result == {"briefs": [], "summaries": [], "sources": [], "explorations": []}
