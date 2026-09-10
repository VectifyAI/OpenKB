"""Tests for openkb.agent.consolidator."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from openkb.agent.consolidator import (
    _split_notes,
    consolidate_page,
    count_pending_notes,
    find_consolidation_candidates,
    resolve_page,
)


def _mock_completion(response: str):
    def side_effect(*args, **kwargs):
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = response
        mock_resp.usage = MagicMock(prompt_tokens=100, completion_tokens=50)
        mock_resp.usage.prompt_tokens_details = None
        return mock_resp

    return side_effect


def _write_page(wiki, page_dir: str, slug: str, body: str):
    d = wiki / page_dir
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{slug}.md"
    path.write_text(body, encoding="utf-8")
    return path


class TestSplitNotesAndCount:
    def test_no_notes_heading(self):
        existing, notes = _split_notes("# Attention\n\nSome prose.")
        assert existing == "# Attention\n\nSome prose."
        assert notes == ""

    def test_splits_prose_and_notes(self):
        body = (
            "# Attention\n\nSome prose.\n\n## Notes\n\n- **2026-01-01** A note. ([[summaries/x]])"
        )
        existing, notes = _split_notes(body)
        assert existing == "# Attention\n\nSome prose."
        assert "A note." in notes

    def test_count_pending_notes_zero_without_heading(self):
        assert count_pending_notes("# Attention\n\nSome prose.") == 0

    def test_count_pending_notes_counts_bullets(self):
        text = (
            "---\nsources: [a]\n---\n\n## Notes\n\n"
            "- **2026-01-02** Second. ([[summaries/b]])\n"
            "- **2026-01-01** First. ([[summaries/a]])\n"
        )
        assert count_pending_notes(text) == 2


class TestFindConsolidationCandidates:
    def test_finds_pages_with_enough_notes(self, tmp_path):
        wiki = tmp_path / "wiki"
        _write_page(
            wiki,
            "concepts",
            "approval-workflows",
            '---\nsources: ["a"]\n---\n\n## Notes\n\n- **2026-01-01** Note. ([[summaries/a]])\n',
        )
        _write_page(wiki, "concepts", "no-notes", '---\nsources: ["a"]\n---\n\n# Prose only.\n')
        candidates = find_consolidation_candidates(wiki, min_notes=1)
        assert candidates == [("concepts", "approval-workflows", 1)]

    def test_min_notes_filters_out_thin_pages(self, tmp_path):
        wiki = tmp_path / "wiki"
        _write_page(
            wiki,
            "entities",
            "acme-corp",
            '---\nsources: ["a"]\n---\n\n## Notes\n\n- **2026-01-01** Note. ([[summaries/a]])\n',
        )
        assert find_consolidation_candidates(wiki, min_notes=2) == []


class TestResolvePage:
    def test_exact_slug_match(self, tmp_path):
        wiki = tmp_path / "wiki"
        _write_page(wiki, "concepts", "approval-workflows", "---\nsources: []\n---\n\nBody.")
        assert resolve_page(wiki, "approval-workflows") == [("concepts", "approval-workflows")]

    def test_no_match(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        assert resolve_page(wiki, "nonexistent") == []

    def test_ambiguous_substring_match(self, tmp_path):
        wiki = tmp_path / "wiki"
        _write_page(wiki, "concepts", "approval-workflows", "---\nsources: []\n---\n\nBody.")
        _write_page(wiki, "entities", "approval-bot", "---\nsources: []\n---\n\nBody.")
        matches = resolve_page(wiki, "approval")
        assert set(matches) == {("concepts", "approval-workflows"), ("entities", "approval-bot")}


class TestConsolidatePage:
    def test_returns_false_without_notes_section(self, tmp_path):
        wiki = tmp_path / "wiki"
        _write_page(wiki, "concepts", "approval-workflows", '---\nsources: ["a"]\n---\n\nProse.\n')
        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.completion = MagicMock(side_effect=AssertionError("should not be called"))
            result = consolidate_page(wiki, "concepts", "approval-workflows", "gpt-4o-mini")
        assert result is False

    def test_consolidates_and_replaces_notes_section(self, tmp_path):
        wiki = tmp_path / "wiki"
        _write_page(
            wiki,
            "concepts",
            "approval-workflows",
            '---\nsources: ["summaries/a.md", "summaries/b.md"]\ntype: "Concept"\n'
            'description: "Old description"\n---\n\n'
            "Existing prose.\n\n## Notes\n\n"
            "- **2026-01-02** Second ticket note. ([[summaries/b]])\n"
            "- **2026-01-01** First ticket note. ([[summaries/a]])\n",
        )
        response = json.dumps(
            {
                "description": "How approvals are routed and escalated.",
                "content": "# Approval Workflows\n\nConsolidated prose covering both tickets.",
            }
        )
        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.completion = MagicMock(side_effect=_mock_completion(response))
            result = consolidate_page(wiki, "concepts", "approval-workflows", "gpt-4o-mini")

        assert result is True
        text = (wiki / "concepts" / "approval-workflows.md").read_text(encoding="utf-8")
        assert "## Notes" not in text
        assert "Consolidated prose covering both tickets." in text
        assert 'description: "How approvals are routed and escalated."' in text
        # sources: untouched by consolidation.
        assert '"summaries/a.md"' in text
        assert '"summaries/b.md"' in text

    def test_strips_ghost_wikilinks_from_consolidated_content(self, tmp_path):
        wiki = tmp_path / "wiki"
        _write_page(
            wiki,
            "concepts",
            "approval-workflows",
            '---\nsources: ["summaries/a.md"]\n---\n\n## Notes\n\n'
            "- **2026-01-01** Note. ([[summaries/a]])\n",
        )
        response = json.dumps(
            {
                "description": "Desc.",
                "content": "Mentions [[concepts/nonexistent-page]] which doesn't exist.",
            }
        )
        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.completion = MagicMock(side_effect=_mock_completion(response))
            consolidate_page(wiki, "concepts", "approval-workflows", "gpt-4o-mini")

        text = (wiki / "concepts" / "approval-workflows.md").read_text(encoding="utf-8")
        assert "[[concepts/nonexistent-page]]" not in text
