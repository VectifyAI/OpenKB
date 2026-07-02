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

    def test_summary_and_source_text_both_included(self, sample_tree):
        output = render_summary_md(sample_tree, "Sample Document", "doc-abc")
        assert "Summary: Overview of the document topic." in output
        assert "Summary: Historical context." in output
        # The real per-node source text is now quoted too, not just a
        # paraphrase — IndexConfig(if_add_node_text=True) already fetches
        # it, the old renderer just silently discarded it.
        assert "Source text:" in output
        assert "> This document introduces the core concepts of the system." in output

    def test_node_without_text_has_no_source_text_block(self):
        tree = {
            "structure": [
                {"title": "Intro", "start_index": 1, "end_index": 2, "summary": "x", "nodes": []}
            ]
        }
        output = render_summary_md(tree, "my-doc", "doc-123")
        assert "Source text:" not in output

    def test_internal_pageindex_image_refs_are_stripped_from_source_text(self):
        # PageIndex's own image refs point into its private
        # .openkb/files/{doc_id}/images/... cache, which never resolves from
        # a wiki page, so they're stripped rather than quoted verbatim.
        tree = {
            "structure": [
                {
                    "title": "Intro",
                    "start_index": 1,
                    "end_index": 2,
                    "summary": "x",
                    "text": "Some text.\n![fig](/private/cache/img.png)\nMore text.",
                    "nodes": [],
                }
            ]
        }
        output = render_summary_md(tree, "my-doc", "doc-123")
        assert "![fig]" not in output
        assert "> Some text." in output
        assert "> More text." in output


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


def test_summary_full_text_quoted_yaml_safe():
    import yaml

    tree = {"structure": []}
    md = render_summary_md(tree, "weird: name", "doc-1", description="d")
    # full_text is JSON-quoted, so a source name with a colon stays valid YAML
    assert 'full_text: "sources/weird: name.json"' in md
    fm = yaml.safe_load(md.split("---")[1])
    assert fm["full_text"] == "sources/weird: name.json"
    assert fm["type"] == "Summary"
