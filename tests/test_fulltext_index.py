"""Tests for openkb.fulltext_index (BM25 hybrid search)."""

from __future__ import annotations

from openkb.fulltext_index import WikiFullTextIndex


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
