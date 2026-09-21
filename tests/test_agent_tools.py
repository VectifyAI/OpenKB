"""Tests for openkb.agent.tools — plain function implementations."""

from __future__ import annotations

from openkb.agent.tools import (
    DocumentItem,
    TaxonomyItem,
    artifact_event_from_write,
    get_content,
    get_kb_status,
    get_wiki_page_content,
    list_documents,
    list_taxonomy,
    list_taxonomy_items,
    list_wiki_files,
    parse_pages,
    read_wiki_file,
    read_wiki_image,
    search_wiki,
    write_wiki_file,
)

FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8


# ---------------------------------------------------------------------------
# read_wiki_image
# ---------------------------------------------------------------------------


class TestReadWikiImage:
    def _make_image(self, tmp_path):
        images_dir = tmp_path / "sources" / "images" / "doc"
        images_dir.mkdir(parents=True)
        (images_dir / "p1_img1.png").write_bytes(FAKE_PNG)

    def test_reads_wiki_root_relative_path(self, tmp_path):
        self._make_image(tmp_path)

        result = read_wiki_image("sources/images/doc/p1_img1.png", str(tmp_path))

        assert result["type"] == "image"
        assert result["image_url"].startswith("data:image/png;base64,")

    def test_reads_note_relative_path_from_sources(self, tmp_path):
        # Source .md pages embed images as "images/<doc>/<file>" (relative to
        # wiki/sources/); the tool must resolve those verbatim too.
        self._make_image(tmp_path)

        result = read_wiki_image("images/doc/p1_img1.png", str(tmp_path))

        assert result["type"] == "image"
        assert result["image_url"].startswith("data:image/png;base64,")

    def test_missing_image_reports_not_found(self, tmp_path):
        self._make_image(tmp_path)

        result = read_wiki_image("images/doc/nope.png", str(tmp_path))

        assert result["type"] == "text"
        assert "not found" in result["text"].lower()

    def test_path_escape_denied(self, tmp_path):
        self._make_image(tmp_path)

        result = read_wiki_image("../outside.png", str(tmp_path))

        assert result["type"] == "text"
        assert "Access denied" in result["text"]


# ---------------------------------------------------------------------------
# list_wiki_files
# ---------------------------------------------------------------------------


class TestListWikiFiles:
    def test_lists_md_files(self, tmp_path):
        wiki_root = str(tmp_path)
        (tmp_path / "sources").mkdir()
        (tmp_path / "sources" / "doc1.md").write_text("# Doc 1")
        (tmp_path / "sources" / "doc2.md").write_text("# Doc 2")

        result = list_wiki_files("sources", wiki_root)

        assert "doc1.md" in result
        assert "doc2.md" in result

    def test_empty_directory_returns_no_files(self, tmp_path):
        wiki_root = str(tmp_path)
        (tmp_path / "concepts").mkdir()

        result = list_wiki_files("concepts", wiki_root)

        assert result == "No files found."

    def test_only_md_files_returned(self, tmp_path):
        wiki_root = str(tmp_path)
        (tmp_path / "sources").mkdir()
        (tmp_path / "sources" / "doc.md").write_text("# Doc")
        (tmp_path / "sources" / "image.png").write_bytes(b"PNG")
        (tmp_path / "sources" / "data.json").write_text("{}")

        result = list_wiki_files("sources", wiki_root)

        assert "doc.md" in result
        assert "image.png" not in result
        assert "data.json" not in result

    def test_nonexistent_directory_returns_no_files(self, tmp_path):
        wiki_root = str(tmp_path)

        result = list_wiki_files("does_not_exist", wiki_root)

        assert result == "No files found."


# ---------------------------------------------------------------------------
# read_wiki_file
# ---------------------------------------------------------------------------


class TestReadWikiFile:
    def test_reads_existing_file(self, tmp_path):
        wiki_root = str(tmp_path)
        (tmp_path / "sources").mkdir()
        (tmp_path / "sources" / "notes.md").write_text("# Notes\n\nContent here.")

        result = read_wiki_file("sources/notes.md", wiki_root)

        assert "# Notes" in result
        assert "Content here." in result

    def test_missing_file_returns_not_found(self, tmp_path):
        wiki_root = str(tmp_path)

        result = read_wiki_file("sources/missing.md", wiki_root)

        assert result == "File not found: sources/missing.md"

    def test_path_is_relative_to_wiki_root(self, tmp_path):
        wiki_root = str(tmp_path)
        (tmp_path / "summaries").mkdir()
        (tmp_path / "summaries" / "paper.md").write_text("Summary content.")

        result = read_wiki_file("summaries/paper.md", wiki_root)

        assert "Summary content." in result

    def test_reads_index_md(self, tmp_path):
        # index.md and reports/ are the two cases get_content added the
        # "index"/"report" kinds for, so read_wiki_file has no remaining
        # raw-path fallback case for them.
        wiki_root = str(tmp_path)
        (tmp_path / "index.md").write_text("# KB Index\n")

        result = read_wiki_file("index.md", wiki_root)

        assert "# KB Index" in result

    def test_reads_report_file(self, tmp_path):
        wiki_root = str(tmp_path)
        (tmp_path / "reports").mkdir()
        (tmp_path / "reports" / "health.md").write_text("All good.")

        result = read_wiki_file("reports/health.md", wiki_root)

        assert result == "All good."

    def test_path_traversal_denied_via_fallback(self, tmp_path):
        # Doesn't map to any of get_content's known directories -> falls
        # through to the defensive raw-path fallback, which still rejects it.
        wiki_root = str(tmp_path)

        result = read_wiki_file("../../etc/passwd", wiki_root)

        assert "denied" in result.lower()


# ---------------------------------------------------------------------------
# write_wiki_file
# ---------------------------------------------------------------------------


class TestWriteWikiFile:
    def test_writes_new_file(self, tmp_path):
        wiki_root = str(tmp_path)
        (tmp_path / "concepts").mkdir()

        result = write_wiki_file("concepts/new_concept.md", "# New Concept\n", wiki_root)

        assert result == "Written: concepts/new_concept.md"
        assert (tmp_path / "concepts" / "new_concept.md").read_text() == "# New Concept\n"

    def test_overwrites_existing_file(self, tmp_path):
        wiki_root = str(tmp_path)
        (tmp_path / "concepts").mkdir()
        (tmp_path / "concepts" / "existing.md").write_text("Old content.")

        write_wiki_file("concepts/existing.md", "New content.", wiki_root)

        assert (tmp_path / "concepts" / "existing.md").read_text() == "New content."

    def test_creates_parent_directories(self, tmp_path):
        wiki_root = str(tmp_path)

        result = write_wiki_file("deep/nested/dir/file.md", "# Deep File\n", wiki_root)

        assert result == "Written: deep/nested/dir/file.md"
        assert (tmp_path / "deep" / "nested" / "dir" / "file.md").exists()

    def test_returns_written_path(self, tmp_path):
        wiki_root = str(tmp_path)
        (tmp_path / "reports").mkdir()

        result = write_wiki_file("reports/health.md", "All good.", wiki_root)

        assert result == "Written: reports/health.md"


# ---------------------------------------------------------------------------
# parse_pages
# ---------------------------------------------------------------------------


class TestParsePages:
    def test_single_page(self):
        assert parse_pages("3") == [3]

    def test_range(self):
        assert parse_pages("3-5") == [3, 4, 5]

    def test_comma_separated(self):
        assert parse_pages("1,3,5") == [1, 3, 5]

    def test_mixed(self):
        assert parse_pages("1-3,7,10-12") == [1, 2, 3, 7, 10, 11, 12]

    def test_deduplication(self):
        assert parse_pages("3,3,3") == [3]

    def test_sorted(self):
        assert parse_pages("5,1,3") == [1, 3, 5]

    def test_ignores_zero_and_negative(self):
        assert parse_pages("0,-1,3") == [3]


# ---------------------------------------------------------------------------
# get_wiki_page_content
# ---------------------------------------------------------------------------


class TestGetWikiPageContent:
    def test_reads_pages_from_json(self, tmp_path):
        import json

        wiki_root = str(tmp_path)
        sources = tmp_path / "sources"
        sources.mkdir()
        pages = [
            {"page": 1, "content": "Page one text."},
            {"page": 2, "content": "Page two text."},
            {"page": 3, "content": "Page three text."},
        ]
        (sources / "paper.json").write_text(json.dumps(pages), encoding="utf-8")
        result = get_wiki_page_content("paper", "1,3", wiki_root)
        assert "[Page 1]" in result
        assert "Page one text." in result
        assert "[Page 3]" in result
        assert "Page three text." in result
        assert "Page two" not in result

    def test_returns_error_for_missing_file(self, tmp_path):
        wiki_root = str(tmp_path)
        (tmp_path / "sources").mkdir()
        result = get_wiki_page_content("nonexistent", "1", wiki_root)
        assert "not found" in result.lower()

    def test_returns_error_for_no_matching_pages(self, tmp_path):
        import json

        wiki_root = str(tmp_path)
        sources = tmp_path / "sources"
        sources.mkdir()
        pages = [{"page": 1, "content": "Only page."}]
        (sources / "paper.json").write_text(json.dumps(pages), encoding="utf-8")
        result = get_wiki_page_content("paper", "99", wiki_root)
        assert "no content" in result.lower()

    def test_includes_images_info(self, tmp_path):
        import json

        wiki_root = str(tmp_path)
        sources = tmp_path / "sources"
        sources.mkdir()
        pages = [
            {
                "page": 1,
                "content": "Text.",
                "images": [{"path": "images/p/img.png", "width": 100, "height": 80}],
            }
        ]
        (sources / "doc.json").write_text(json.dumps(pages), encoding="utf-8")
        result = get_wiki_page_content("doc", "1", wiki_root)
        assert "img.png" in result

    def test_path_escape_denied(self, tmp_path):
        wiki_root = str(tmp_path)
        (tmp_path / "sources").mkdir()
        result = get_wiki_page_content("../../etc/passwd", "1", wiki_root)
        assert "denied" in result.lower() or "not found" in result.lower()


# ---------------------------------------------------------------------------
# artifact_event_from_write
# ---------------------------------------------------------------------------

_ARGS = '{"path": "output/nvda-guizang-test.html", "content": "<html></html>"}'


def test_artifact_event_for_successful_output_html():
    ev = artifact_event_from_write("write_file", _ARGS, "Written: output/nvda-guizang-test.html")
    assert ev == {
        "kind": "file",
        "path": "output/nvda-guizang-test.html",
        "name": "nvda-guizang-test.html",
    }


def test_artifact_event_none_for_non_write_tool():
    assert artifact_event_from_write("read_file", _ARGS, "…") is None


def test_artifact_event_none_when_write_failed():
    # write_kb_file returns an "Access denied: …" string on rejection.
    assert (
        artifact_event_from_write("write_file", _ARGS, "Access denied: path escapes KB root.")
        is None
    )


def test_artifact_event_none_for_non_html():
    args = '{"path": "output/skills/x/SKILL.md", "content": "…"}'
    assert (
        artifact_event_from_write("write_file", args, "Written: output/skills/x/SKILL.md") is None
    )


def test_artifact_event_none_for_non_output_zone():
    args = '{"path": "wiki/explorations/note.html", "content": "…"}'
    assert (
        artifact_event_from_write("write_file", args, "Written: wiki/explorations/note.html")
        is None
    )


def test_artifact_event_none_for_bad_json():
    assert artifact_event_from_write("write_file", "not json", "Written: output/x.html") is None


# ---------------------------------------------------------------------------
# search_wiki
# ---------------------------------------------------------------------------


class TestSearchWiki:
    def test_finds_matching_page_in_sources(self, tmp_path):
        wiki_root = str(tmp_path)
        (tmp_path / "sources").mkdir()
        (tmp_path / "sources" / "cnn.md").write_text(
            "# Convolutional Neural Networks\n\nDropout regularization prevents overfitting."
        )

        result = search_wiki("dropout regularization", wiki_root)

        assert "[[sources/cnn]]" in result
        assert "Convolutional Neural Networks" in result
        assert "## sources" in result

    def test_concepts_and_entities_are_not_searched(self, tmp_path):
        wiki_root = str(tmp_path)
        (tmp_path / "concepts").mkdir()
        (tmp_path / "concepts" / "cnn.md").write_text(
            "# Convolutional Neural Networks\n\nDropout regularization prevents overfitting."
        )

        result = search_wiki("dropout regularization", wiki_root)

        assert result == "No matching pages found."

    def test_no_matches_returns_message(self, tmp_path):
        wiki_root = str(tmp_path)
        (tmp_path / "sources").mkdir()
        (tmp_path / "sources" / "cnn.md").write_text("# CNN\n\nSomething else entirely.")

        result = search_wiki("nonexistent_keyword_xyz", wiki_root)

        assert result == "No matching pages found."

    def test_respects_top_k(self, tmp_path):
        wiki_root = str(tmp_path)
        (tmp_path / "sources").mkdir()
        for i in range(5):
            (tmp_path / "sources" / f"e{i}.md").write_text(f"# Entity {i}\n\nkeyword {i}.")

        result = search_wiki("keyword", wiki_root, top_k=2)

        assert result.count("[[sources/") == 2

    def test_scope_restricts_to_requested_tiers(self, tmp_path):
        wiki_root = str(tmp_path)
        (tmp_path / "summaries").mkdir()
        (tmp_path / "summaries" / "doc.md").write_text(
            '---\ndescription: "keyword brief"\n---\n\n# Doc\n\nkeyword body.'
        )
        (tmp_path / "sources").mkdir()
        (tmp_path / "sources" / "doc.md").write_text("keyword raw source.")

        result = search_wiki("keyword", wiki_root, scope=["sources"])

        assert "## sources" in result
        assert "## briefs" not in result
        assert "## summaries" not in result

    def test_invalid_scope_returns_error_message(self, tmp_path):
        result = search_wiki("keyword", str(tmp_path), scope=["not-a-real-tier"])

        assert "Unknown scope" in result

    def test_result_includes_locator_for_source_hit(self, tmp_path):
        wiki_root = str(tmp_path)
        (tmp_path / "sources").mkdir()
        (tmp_path / "sources" / "notes.md").write_text("Line one.\nkeyword on line two.")

        result = search_wiki("keyword", wiki_root, scope=["sources"])

        assert "[line 2]" in result


# ---------------------------------------------------------------------------
# list_taxonomy_items
# ---------------------------------------------------------------------------


class TestListTaxonomyItems:
    def test_lists_concepts_and_entities_by_default(self, tmp_path):
        (tmp_path / "concepts").mkdir()
        (tmp_path / "concepts" / "attention.md").write_text(
            '---\ndescription: "How attention works"\n---\n\n# Attention\n\nBody.'
        )
        (tmp_path / "entities").mkdir()
        (tmp_path / "entities" / "acme.md").write_text(
            '---\ntype: organization\ndescription: "A company"\n---\n\n# Acme\n\nBody.'
        )

        items = list_taxonomy_items(str(tmp_path))

        assert len(items) == 2
        by_slug = {i.slug: i for i in items}
        assert by_slug["attention"].kind == "concept"
        assert by_slug["attention"].brief == "How attention works"
        assert by_slug["attention"].type is None
        assert by_slug["acme"].kind == "entity"
        assert by_slug["acme"].type == "organization"
        assert by_slug["acme"].brief == "A company"

    def test_kind_filter_restricts_to_one_directory(self, tmp_path):
        (tmp_path / "concepts").mkdir()
        (tmp_path / "concepts" / "c.md").write_text("# C\n\nBody.")
        (tmp_path / "entities").mkdir()
        (tmp_path / "entities" / "e.md").write_text("# E\n\nBody.")

        items = list_taxonomy_items(str(tmp_path), kind="concept")

        assert len(items) == 1
        assert items[0].kind == "concept"

    def test_legacy_brief_key_resolves(self, tmp_path):
        (tmp_path / "concepts").mkdir()
        (tmp_path / "concepts" / "c.md").write_text('---\nbrief: "legacy brief"\n---\n\n# C\n\nX.')

        items = list_taxonomy_items(str(tmp_path))

        assert items[0].brief == "legacy brief"

    def test_missing_directories_return_empty_list(self, tmp_path):
        assert list_taxonomy_items(str(tmp_path)) == []

    def test_no_frontmatter_yields_empty_brief(self, tmp_path):
        (tmp_path / "concepts").mkdir()
        (tmp_path / "concepts" / "c.md").write_text("# C\n\nNo frontmatter here.")

        items = list_taxonomy_items(str(tmp_path))

        assert items[0].brief == ""

    def test_invalid_kind_raises_value_error(self, tmp_path):
        import pytest

        with pytest.raises(ValueError, match="Unknown kind"):
            list_taxonomy_items(str(tmp_path), kind="document")

    def test_items_are_taxonomy_item_instances(self, tmp_path):
        (tmp_path / "concepts").mkdir()
        (tmp_path / "concepts" / "c.md").write_text("# C\n\nBody.")

        items = list_taxonomy_items(str(tmp_path))

        assert isinstance(items[0], TaxonomyItem)


# ---------------------------------------------------------------------------
# list_taxonomy (agent-facing text formatter over list_taxonomy_items)
# ---------------------------------------------------------------------------


class TestListTaxonomy:
    def test_formats_concepts_and_entities_as_wikilinks(self, tmp_path):
        wiki_root = str(tmp_path)
        (tmp_path / "concepts").mkdir()
        (tmp_path / "concepts" / "attention.md").write_text(
            '---\ndescription: "How attention works"\n---\n\n# Attention\n\nBody.'
        )
        (tmp_path / "entities").mkdir()
        (tmp_path / "entities" / "acme.md").write_text(
            '---\ntype: organization\ndescription: "A company"\n---\n\n# Acme\n\nBody.'
        )

        result = list_taxonomy(wiki_root)

        assert "[[concepts/attention]]" in result
        assert "How attention works" in result
        assert "[[entities/acme]] (organization)" in result
        assert "A company" in result

    def test_kind_filter(self, tmp_path):
        wiki_root = str(tmp_path)
        (tmp_path / "concepts").mkdir()
        (tmp_path / "concepts" / "c.md").write_text("# C\n\nBody.")
        (tmp_path / "entities").mkdir()
        (tmp_path / "entities" / "e.md").write_text("# E\n\nBody.")

        result = list_taxonomy(wiki_root, kind="concept")

        assert "concepts/c" in result
        assert "entities/e" not in result

    def test_empty_taxonomy_returns_message(self, tmp_path):
        result = list_taxonomy(str(tmp_path))

        assert result == "No concepts or entities found."


# ---------------------------------------------------------------------------
# list_documents
# ---------------------------------------------------------------------------


class TestListDocuments:
    def test_lists_summaries_and_explorations_by_default(self, tmp_path):
        (tmp_path / "summaries").mkdir()
        (tmp_path / "summaries" / "paper.md").write_text(
            '---\ndescription: "A paper about attention"\n---\n\nBody.'
        )
        (tmp_path / "explorations").mkdir()
        (tmp_path / "explorations" / "q1.md").write_text(
            '---\nquery: "What is attention?"\n---\n\nAnswer body.'
        )

        items = list_documents(str(tmp_path))

        assert len(items) == 2
        by_slug = {i.slug: i for i in items}
        assert by_slug["paper"].kind == "summary"
        assert by_slug["paper"].brief == "A paper about attention"
        assert by_slug["q1"].kind == "exploration"
        assert by_slug["q1"].brief == "What is attention?"

    def test_kind_filter_restricts_to_one_directory(self, tmp_path):
        (tmp_path / "summaries").mkdir()
        (tmp_path / "summaries" / "s.md").write_text("Body.")
        (tmp_path / "explorations").mkdir()
        (tmp_path / "explorations" / "e.md").write_text("Body.")

        items = list_documents(str(tmp_path), kind="summary")

        assert len(items) == 1
        assert items[0].kind == "summary"

    def test_missing_directories_return_empty_list(self, tmp_path):
        assert list_documents(str(tmp_path)) == []

    def test_exploration_without_query_field_yields_empty_brief(self, tmp_path):
        (tmp_path / "explorations").mkdir()
        (tmp_path / "explorations" / "e.md").write_text("No frontmatter here.")

        items = list_documents(str(tmp_path), kind="exploration")

        assert items[0].brief == ""

    def test_invalid_kind_raises_value_error(self, tmp_path):
        import pytest

        with pytest.raises(ValueError, match="Unknown kind"):
            list_documents(str(tmp_path), kind="concept")

    def test_items_are_document_item_instances(self, tmp_path):
        (tmp_path / "summaries").mkdir()
        (tmp_path / "summaries" / "s.md").write_text("Body.")

        items = list_documents(str(tmp_path))

        assert isinstance(items[0], DocumentItem)


# ---------------------------------------------------------------------------
# get_content
# ---------------------------------------------------------------------------


class TestGetContent:
    def test_reads_concept_page(self, tmp_path):
        (tmp_path / "concepts").mkdir()
        (tmp_path / "concepts" / "attention.md").write_text("# Attention\n\nFull content here.")

        result = get_content("attention", str(tmp_path), kind="concept")

        assert len(result) == 1
        assert result[0].error is None
        assert "Full content here." in result[0].content

    def test_kind_disambiguates_same_slug(self, tmp_path):
        (tmp_path / "concepts").mkdir()
        (tmp_path / "concepts" / "acme.md").write_text("# Acme concept")
        (tmp_path / "entities").mkdir()
        (tmp_path / "entities" / "acme.md").write_text("# Acme entity")

        concept = get_content("acme", str(tmp_path), kind="concept")[0]
        entity = get_content("acme", str(tmp_path), kind="entity")[0]

        assert "concept" in concept.content
        assert "entity" in entity.content

    def test_without_kind_returns_one_entry_per_match_not_first_wins(self, tmp_path):
        # Summary and source commonly share the same slug (same document) —
        # kind=None must surface BOTH, not silently drop one via precedence.
        (tmp_path / "summaries").mkdir()
        (tmp_path / "summaries" / "paper.md").write_text("Summary body.")
        (tmp_path / "sources").mkdir()
        (tmp_path / "sources" / "paper.md").write_text("Source body.")

        results = get_content("paper", str(tmp_path))

        assert len(results) == 2
        kinds = {r.kind for r in results}
        assert kinds == {"summary", "source"}

    def test_single_match_is_still_a_list(self, tmp_path):
        (tmp_path / "concepts").mkdir()
        (tmp_path / "concepts" / "solo.md").write_text("Only one match.")

        results = get_content("solo", str(tmp_path))

        assert isinstance(results, list)
        assert len(results) == 1

    def test_no_match_returns_empty_list_when_kind_not_given(self, tmp_path):
        assert get_content("nonexistent", str(tmp_path)) == []

    def test_explicit_kind_not_found_returns_error_entry(self, tmp_path):
        result = get_content("nonexistent", str(tmp_path), kind="concept")

        assert len(result) == 1
        assert result[0].content is None
        assert "not found" in result[0].error.lower()

    def test_invalid_kind_raises_value_error(self, tmp_path):
        import pytest

        with pytest.raises(ValueError, match="Unknown kind"):
            get_content("slug", str(tmp_path), kind="document")

    def test_path_traversal_is_rejected(self, tmp_path):
        result = get_content("../../etc/passwd", str(tmp_path), kind="concept")[0]

        assert "denied" in result.error.lower()

    def test_reads_index_page(self, tmp_path):
        (tmp_path / "index.md").write_text("# Knowledge Base Index\n")

        result = get_content("index", str(tmp_path), kind="index")[0]

        assert result.error is None
        assert "Knowledge Base Index" in result.content

    def test_reads_report_page(self, tmp_path):
        (tmp_path / "reports").mkdir()
        (tmp_path / "reports" / "health.md").write_text("All good.")

        result = get_content("health", str(tmp_path), kind="report")[0]

        assert result.content == "All good."

    def test_source_short_doc_reads_whole_file(self, tmp_path):
        (tmp_path / "sources").mkdir()
        (tmp_path / "sources" / "notes.md").write_text("Notes body.")

        result = get_content("notes", str(tmp_path), kind="source")[0]

        assert result.content == "Notes body."

    def test_source_short_doc_rejects_pages(self, tmp_path):
        (tmp_path / "sources").mkdir()
        (tmp_path / "sources" / "notes.md").write_text("Notes body.")

        result = get_content("notes", str(tmp_path), kind="source", pages="1")[0]

        assert result.content is None
        assert "pages" in result.error.lower()

    def test_source_long_doc_requires_pages(self, tmp_path):
        import json

        (tmp_path / "sources").mkdir()
        (tmp_path / "sources" / "paper.json").write_text(
            json.dumps([{"page": 1, "content": "Page one."}]), encoding="utf-8"
        )

        result = get_content("paper", str(tmp_path), kind="source")[0]

        assert result.content is None
        assert "pages" in result.error.lower()

    def test_source_long_doc_with_pages_returns_content(self, tmp_path):
        import json

        (tmp_path / "sources").mkdir()
        (tmp_path / "sources" / "paper.json").write_text(
            json.dumps([{"page": 1, "content": "Page one."}, {"page": 2, "content": "Page two."}]),
            encoding="utf-8",
        )

        result = get_content("paper", str(tmp_path), kind="source", pages="2")[0]

        assert result.error is None
        assert "Page two." in result.content
        assert "Page one." not in result.content

    def test_explicit_non_source_kind_with_pages_errors(self, tmp_path):
        (tmp_path / "concepts").mkdir()
        (tmp_path / "concepts" / "c.md").write_text("Concept body.")

        result = get_content("c", str(tmp_path), kind="concept", pages="1")[0]

        assert result.content is None
        assert "pages" in result.error.lower()

    def test_pages_silently_ignored_during_kind_none_fan_out(self, tmp_path):
        # Setting `pages` while fanning out across all kinds (kind=None) must
        # not error out a match that has nothing to do with pagination — it's
        # only meaningful for a "source" match, and even then only when that
        # source turns out to be a long PageIndex document.
        (tmp_path / "concepts").mkdir()
        (tmp_path / "concepts" / "c.md").write_text("Concept body.")

        results = get_content("c", str(tmp_path), pages="1")

        assert len(results) == 1
        assert results[0].error is None
        assert results[0].content == "Concept body."


# ---------------------------------------------------------------------------
# get_kb_status
# ---------------------------------------------------------------------------


class TestGetKbStatus:
    def test_counts_md_files_per_subdir(self, tmp_path):
        (tmp_path / "wiki" / "concepts").mkdir(parents=True)
        (tmp_path / "wiki" / "concepts" / "a.md").write_text("A")
        (tmp_path / "wiki" / "concepts" / "b.md").write_text("B")
        (tmp_path / "wiki" / "summaries").mkdir()
        (tmp_path / "wiki" / "summaries" / "s.md").write_text("S")

        status = get_kb_status(str(tmp_path))

        assert status.counts["concepts"] == 2
        assert status.counts["summaries"] == 1
        assert status.counts["entities"] == 0

    def test_kb_dir_is_absolute(self, tmp_path):
        status = get_kb_status(str(tmp_path))

        assert status.kb_dir == str(tmp_path.resolve())

    def test_no_registry_yields_zero_total_indexed(self, tmp_path):
        status = get_kb_status(str(tmp_path))

        assert status.total_indexed == 0

    def test_reads_total_indexed_from_hashes_registry(self, tmp_path):
        import json

        (tmp_path / ".openkb").mkdir()
        (tmp_path / ".openkb" / "hashes.json").write_text(
            json.dumps({"hash1": {"name": "a.pdf"}, "hash2": {"name": "b.pdf"}})
        )

        status = get_kb_status(str(tmp_path))

        assert status.total_indexed == 2

    def test_counts_raw_files_when_raw_dir_exists(self, tmp_path):
        (tmp_path / "raw").mkdir()
        (tmp_path / "raw" / "doc.pdf").write_text("x")
        (tmp_path / "raw" / "doc2.pdf").write_text("x")

        status = get_kb_status(str(tmp_path))

        assert status.counts["raw"] == 2

    def test_no_raw_dir_omits_raw_count(self, tmp_path):
        status = get_kb_status(str(tmp_path))

        assert "raw" not in status.counts
