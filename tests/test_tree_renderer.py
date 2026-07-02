"""Tests for openkb.tree_renderer."""

from __future__ import annotations

from openkb.tree_renderer import render_summary_md

# ---------------------------------------------------------------------------
# render_summary_md
# ---------------------------------------------------------------------------


class TestRenderSummaryMd:
    def test_has_yaml_frontmatter(self, sample_tree):
        output = render_summary_md(sample_tree, "Sample Document", "doc-abc")
        assert output.startswith("---\n")
        assert "doc_type: pageindex" in output
        assert 'full_text: "sources/Sample Document.json"' in output

    def test_top_level_nodes_are_h1(self, sample_tree):
        output = render_summary_md(sample_tree, "Sample Document", "doc-abc")
        assert "# Introduction" in output
        assert "# Conclusion" in output

    def test_nested_nodes_are_h2(self, sample_tree):
        output = render_summary_md(sample_tree, "Sample Document", "doc-abc")
        assert "## Background" in output
        assert "## Motivation" in output

    def test_page_range_included(self, sample_tree):
        output = render_summary_md(sample_tree, "Sample Document", "doc-abc")
        assert "(pages 0–120)" in output
        assert "(pages 121–200)" in output

    def test_summary_included_not_text(self, sample_tree):
        output = render_summary_md(sample_tree, "Sample Document", "doc-abc")
        assert "Summary: Overview of the document topic." in output
        assert "Summary: Historical context." in output
        # Raw text should NOT appear in summary view
        assert "This document introduces the core concepts of the system." not in output


def test_summary_md_has_type_and_description():
    tree = {
        "structure": [
            {"title": "Intro", "start_index": 1, "end_index": 2, "summary": "x", "nodes": []}
        ]
    }
    md = render_summary_md(tree, "my-doc", "doc-123", description="Quarterly report.")
    assert 'type: "Summary"' in md
    assert 'description: "Quarterly report."' in md
    assert "doc_type: pageindex" in md
    assert 'full_text: "sources/my-doc.json"' in md


def test_duplicate_sibling_summaries_collapse_to_a_pointer():
    # Two sibling nodes on the same physical page can be handed the exact
    # same LLM-written summary (PageIndex#340). The second occurrence should
    # collapse to a pointer instead of repeating the block verbatim.
    tree = {
        "structure": [
            {
                "title": "1.1 First item",
                "start_index": 5,
                "end_index": 5,
                "summary": "Shared duplicate summary text.",
                "nodes": [],
            },
            {
                "title": "1.2 Second item",
                "start_index": 5,
                "end_index": 5,
                "summary": "Shared duplicate summary text.",
                "nodes": [],
            },
        ]
    }
    md = render_summary_md(tree, "my-doc", "doc-123")
    assert md.count("Summary: Shared duplicate summary text.") == 1
    assert '_(same content as "1.1 First item" above)_' in md


def test_duplicate_summaries_collapse_across_cousins_not_just_siblings():
    # The collision isn't confined to direct siblings — a node nested under
    # a different parent, seen later in document order, can repeat the same
    # summary too.
    tree = {
        "structure": [
            {
                "title": "Parent A",
                "start_index": 1,
                "end_index": 1,
                "summary": "",
                "nodes": [
                    {
                        "title": "Child A.1",
                        "start_index": 1,
                        "end_index": 1,
                        "summary": "Cousin duplicate.",
                        "nodes": [],
                    }
                ],
            },
            {
                "title": "Parent B",
                "start_index": 2,
                "end_index": 2,
                "summary": "",
                "nodes": [
                    {
                        "title": "Child B.1",
                        "start_index": 2,
                        "end_index": 2,
                        "summary": "Cousin duplicate.",
                        "nodes": [],
                    }
                ],
            },
        ]
    }
    md = render_summary_md(tree, "my-doc", "doc-123")
    assert md.count("Summary: Cousin duplicate.") == 1
    assert '_(same content as "Child A.1" above)_' in md


def test_distinct_summaries_are_not_collapsed():
    tree = {
        "structure": [
            {
                "title": "A",
                "start_index": 1,
                "end_index": 1,
                "summary": "Summary one.",
                "nodes": [],
            },
            {
                "title": "B",
                "start_index": 2,
                "end_index": 2,
                "summary": "Summary two.",
                "nodes": [],
            },
        ]
    }
    md = render_summary_md(tree, "my-doc", "doc-123")
    assert "Summary: Summary one." in md
    assert "Summary: Summary two." in md
    assert "same content as" not in md


def test_summary_full_text_quoted_yaml_safe():
    import yaml

    tree = {"structure": []}
    md = render_summary_md(tree, "weird: name", "doc-1", description="d")
    # full_text is JSON-quoted, so a source name with a colon stays valid YAML
    assert 'full_text: "sources/weird: name.json"' in md
    fm = yaml.safe_load(md.split("---")[1])
    assert fm["full_text"] == "sources/weird: name.json"
    assert fm["type"] == "Summary"
