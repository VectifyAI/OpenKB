"""Tests for openkb.tree_renderer."""
from __future__ import annotations

import pytest

from openkb.tree_renderer import render_source_md, render_summary_md


# ---------------------------------------------------------------------------
# render_source_md
# ---------------------------------------------------------------------------


class TestRenderSourceMd:
    def test_has_yaml_frontmatter(self, sample_tree):
        output = render_source_md(sample_tree, "Sample Document", "doc-abc")
        assert output.startswith("---\n")
        assert "source: Sample Document" in output
        assert "type: pageindex" in output
        assert "doc_id: doc-abc" in output
        assert "---\n" in output

    def test_top_level_nodes_are_h1(self, sample_tree):
        output = render_source_md(sample_tree, "Sample Document", "doc-abc")
        assert "# Introduction" in output
        assert "# Conclusion" in output

    def test_nested_nodes_are_h2(self, sample_tree):
        output = render_source_md(sample_tree, "Sample Document", "doc-abc")
        assert "## Background" in output
        assert "## Motivation" in output

    def test_page_range_included(self, sample_tree):
        output = render_source_md(sample_tree, "Sample Document", "doc-abc")
        assert "(pages 0–120)" in output  # Introduction
        assert "(pages 0–60)" in output   # Background
        assert "(pages 61–120)" in output  # Motivation
        assert "(pages 121–200)" in output  # Conclusion

    def test_node_text_included(self, sample_tree):
        output = render_source_md(sample_tree, "Sample Document", "doc-abc")
        assert "This document introduces the core concepts of the system." in output
        assert "Background information on the subject." in output

    def test_no_summary_in_source(self, sample_tree):
        output = render_source_md(sample_tree, "Sample Document", "doc-abc")
        # Source pages show text, not summaries
        assert "Summary:" not in output

    def test_heading_depth_capped_at_6(self):
        """Deeply nested nodes must not exceed h6."""
        deep_tree = {
            "doc_name": "Deep",
            "doc_description": "A deeply nested doc.",
            "structure": [
                {
                    "title": "L1",
                    "start_index": 0,
                    "end_index": 10,
                    "text": "L1 text",
                    "summary": "L1 summary",
                    "nodes": [
                        {
                            "title": "L2",
                            "start_index": 0,
                            "end_index": 5,
                            "text": "L2 text",
                            "summary": "L2 summary",
                            "nodes": [
                                {
                                    "title": "L3",
                                    "start_index": 0,
                                    "end_index": 3,
                                    "text": "L3 text",
                                    "summary": "L3 summary",
                                    "nodes": [
                                        {
                                            "title": "L4",
                                            "start_index": 0,
                                            "end_index": 1,
                                            "text": "L4 text",
                                            "summary": "L4 summary",
                                            "nodes": [
                                                {
                                                    "title": "L5",
                                                    "start_index": 0,
                                                    "end_index": 1,
                                                    "text": "L5 text",
                                                    "summary": "L5 summary",
                                                    "nodes": [
                                                        {
                                                            "title": "L6",
                                                            "start_index": 0,
                                                            "end_index": 1,
                                                            "text": "L6 text",
                                                            "summary": "L6 summary",
                                                            "nodes": [
                                                                {
                                                                    "title": "L7",
                                                                    "start_index": 0,
                                                                    "end_index": 1,
                                                                    "text": "L7 text",
                                                                    "summary": "L7 summary",
                                                                    "nodes": [],
                                                                }
                                                            ],
                                                        }
                                                    ],
                                                }
                                            ],
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        output = render_source_md(deep_tree, "Deep", "doc-deep")
        # L7 is at depth 7 — must render as h6, not h7
        assert "#######" not in output
        assert "L7 text" in output


# ---------------------------------------------------------------------------
# render_summary_md
# ---------------------------------------------------------------------------


class TestRenderSummaryMd:
    def test_has_yaml_frontmatter(self, sample_tree):
        output = render_summary_md(sample_tree, "Sample Document", "doc-abc")
        assert output.startswith("---\n")
        assert "source: Sample Document" in output
        assert "type: pageindex" in output
        assert "doc_id: doc-abc" in output

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
