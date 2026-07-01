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


def test_summary_full_text_quoted_yaml_safe():
    import yaml

    tree = {"structure": []}
    md = render_summary_md(tree, "weird: name", "doc-1", description="d")
    # full_text is JSON-quoted, so a source name with a colon stays valid YAML
    assert 'full_text: "sources/weird: name.json"' in md
    fm = yaml.safe_load(md.split("---")[1])
    assert fm["full_text"] == "sources/weird: name.json"
    assert fm["type"] == "Summary"
