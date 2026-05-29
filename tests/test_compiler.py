"""Tests for openkb.agent.compiler pipeline."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from openkb.agent.compiler import (
    compile_long_doc,
    compile_short_doc,
    _compile_concepts,
    _parse_json,
    _sanitize_concept_name,
    _write_summary,
    _write_concept,
    _update_index,
    _read_wiki_context,
    _read_concept_briefs,
    _add_related_link,
    _backlink_summary,
    _backlink_concepts,
)


class TestParseJson:
    def test_plain_json(self):
        assert _parse_json('[{"name": "foo"}]') == [{"name": "foo"}]

    def test_fenced_json(self):
        text = '```json\n[{"name": "bar"}]\n```'
        assert _parse_json(text) == [{"name": "bar"}]

    def test_invalid_json(self):
        with pytest.raises((json.JSONDecodeError, ValueError)):
            _parse_json("not json")


class TestParseConceptsPlan:
    def test_dict_format(self):
        text = json.dumps({
            "create": [{"name": "foo", "title": "Foo"}],
            "update": [{"name": "bar", "title": "Bar"}],
            "related": ["baz"],
        })
        parsed = _parse_json(text)
        assert isinstance(parsed, dict)
        assert len(parsed["create"]) == 1
        assert len(parsed["update"]) == 1
        assert parsed["related"] == ["baz"]

    def test_fallback_list_format(self):
        text = json.dumps([{"name": "foo", "title": "Foo"}])
        parsed = _parse_json(text)
        assert isinstance(parsed, list)

    def test_fenced_dict(self):
        text = '```json\n{"create": [], "update": [], "related": []}\n```'
        parsed = _parse_json(text)
        assert isinstance(parsed, dict)
        assert parsed["create"] == []


class TestParseBriefContent:
    def test_dict_with_brief_and_content(self):
        text = json.dumps({"brief": "A short desc", "content": "# Full page\n\nDetails."})
        parsed = _parse_json(text)
        assert parsed["brief"] == "A short desc"
        assert "# Full page" in parsed["content"]

    def test_plain_text_fallback(self):
        """If LLM returns plain text, _parse_json raises — caller handles fallback."""
        with pytest.raises((json.JSONDecodeError, ValueError)):
            _parse_json("Just plain markdown text without JSON")


class TestSanitizeConceptName:
    def test_ascii_passthrough(self):
        assert _sanitize_concept_name("hello-world") == "hello-world"

    def test_spaces_replaced(self):
        assert _sanitize_concept_name("hello world") == "hello-world"

    def test_chinese(self):
        result = _sanitize_concept_name("注意力机制")
        assert result == "注意力机制"

    def test_japanese(self):
        result = _sanitize_concept_name("トランスフォーマー")
        assert result == "トランスフォーマー"

    def test_french_accents(self):
        result = _sanitize_concept_name("réseau neuronal")
        assert "r" in result
        assert result != "r-seau-neuronal"  # accented chars preserved, not stripped

    def test_distinct_chinese_names_no_collision(self):
        a = _sanitize_concept_name("注意力机制")
        b = _sanitize_concept_name("变压器模型")
        assert a != b

    def test_empty_fallback(self):
        assert _sanitize_concept_name("!!!") == "unnamed-concept"

    def test_nfkc_normalization(self):
        # U+FF21 (fullwidth A) should normalize to regular A
        assert _sanitize_concept_name("\uff21\uff22") == "AB"


class TestWriteSummary:
    def test_writes_with_frontmatter(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        _write_summary(wiki, "my-doc", "# Summary\n\nContent here.")
        path = wiki / "summaries" / "my-doc.md"
        assert path.exists()
        text = path.read_text()
        assert "doc_type: short" in text
        assert "full_text: sources/my-doc.md" in text
        assert "# Summary" in text

    def test_writes_without_brief(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        _write_summary(wiki, "my-doc", "# Summary\n\nContent here.")
        path = wiki / "summaries" / "my-doc.md"
        text = path.read_text()
        assert "doc_type: short" in text
        assert "full_text: sources/my-doc.md" in text


class TestWriteConcept:
    def test_new_concept_with_brief(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        _write_concept(wiki, "attention", "# Attention\n\nDetails.", "paper.pdf", False, brief="Mechanism for selective focus")
        path = wiki / "concepts" / "attention.md"
        assert path.exists()
        text = path.read_text()
        assert 'sources: ["paper.pdf"]' in text
        assert 'brief: "Mechanism for selective focus"' in text
        assert "# Attention" in text

    def test_new_concept_without_brief(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        _write_concept(wiki, "attention", "# Attention\n\nDetails.", "paper.pdf", False)
        path = wiki / "concepts" / "attention.md"
        assert path.exists()
        text = path.read_text()
        assert 'sources: ["paper.pdf"]' in text
        assert "brief:" not in text

    def test_update_concept_updates_brief(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "attention.md").write_text(
            "---\nsources: [paper1.pdf]\nbrief: Old brief\n---\n\n# Attention\n\nOld content.",
            encoding="utf-8",
        )
        _write_concept(wiki, "attention", "New info.", "paper2.pdf", True, brief="Updated brief")
        text = (concepts / "attention.md").read_text()
        assert "paper2.pdf" in text
        assert "paper1.pdf" in text
        assert 'brief: "Updated brief"' in text
        assert "Old brief" not in text

    def test_update_concept_appends_source(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "attention.md").write_text(
            "---\nsources: [paper1.pdf]\n---\n\n# Attention\n\nOld content.",
            encoding="utf-8",
        )
        _write_concept(wiki, "attention", "New info from paper2.", "paper2.pdf", True)
        text = (concepts / "attention.md").read_text()
        assert "paper2.pdf" in text
        assert "paper1.pdf" in text
        assert "New info from paper2." in text

    def test_update_concept_merges_into_non_canonical_sources(self, tmp_path):
        """sources:[a] (no space after colon) must still get paper2 prepended,
        matching the helper's behavior in _add_related_link."""
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "attention.md").write_text(
            "---\nsources:[paper1.pdf]\n---\n\n# Attention\n\nOld content.",
            encoding="utf-8",
        )
        _write_concept(wiki, "attention", "New info from paper2.", "paper2.pdf", True)
        text = (concepts / "attention.md").read_text()
        assert "paper1.pdf" in text
        assert "paper2.pdf" in text
        assert "New info from paper2." in text


class TestUpdateIndex:
    def test_appends_entries_with_briefs(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n\n## Concepts\n\n## Explorations\n",
            encoding="utf-8",
        )
        _update_index(wiki, "my-doc", ["attention", "transformer"],
                       doc_brief="Introduces transformers",
                       concept_briefs={"attention": "Focus mechanism", "transformer": "NN architecture"})
        text = (wiki / "index.md").read_text()
        assert "[[summaries/my-doc]] (short) — Introduces transformers" in text
        assert "[[concepts/attention]] — Focus mechanism" in text
        assert "[[concepts/transformer]] — NN architecture" in text

    def test_updates_only_exact_concept_row(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n\n## Concepts\n"
            "- [[concepts/transformer]] — Uses [[concepts/attention]] internally\n"
            "- [[concepts/attention]] — Old brief\n\n## Explorations\n",
            encoding="utf-8",
        )
        _update_index(
            wiki,
            "my-doc",
            ["attention"],
            concept_briefs={"attention": "New brief"},
        )
        text = (wiki / "index.md").read_text()
        assert "- [[concepts/transformer]] — Uses [[concepts/attention]] internally" in text
        assert "- [[concepts/attention]] — New brief" in text
        assert text.count("[[concepts/attention]] — New brief") == 1

    def test_no_duplicates(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n- [[summaries/my-doc]] — Old brief\n\n## Concepts\n",
            encoding="utf-8",
        )
        _update_index(wiki, "my-doc", [], doc_brief="New brief")
        text = (wiki / "index.md").read_text()
        assert text.count("[[summaries/my-doc]]") == 1

    def test_backwards_compat_no_briefs(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n\n## Concepts\n\n## Explorations\n",
            encoding="utf-8",
        )
        _update_index(wiki, "my-doc", ["attention"])
        text = (wiki / "index.md").read_text()
        assert "[[summaries/my-doc]]" in text
        assert "[[concepts/attention]]" in text

    def test_updates_concept_brief_only_inside_concepts_section(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "index.md").write_text(
            "# Index\n\n"
            "## Documents\n"
            "- [[summaries/my-doc]] (short) — Mentions [[concepts/attention]] here\n\n"
            "## Concepts\n"
            "- [[concepts/attention]] — Old brief\n\n"
            "## Explorations\n",
            encoding="utf-8",
        )

        _update_index(
            wiki,
            "my-doc",
            ["attention"],
            concept_briefs={"attention": "New brief"},
        )

        text = (wiki / "index.md").read_text()
        assert "- [[summaries/my-doc]] (short) — Mentions [[concepts/attention]] here" in text
        assert "- [[concepts/attention]] — New brief" in text
        assert "- [[concepts/attention]] — Old brief" not in text

    def test_adds_concept_entry_when_link_exists_outside_concepts_section(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "index.md").write_text(
            "# Index\n\n"
            "## Documents\n"
            "- [[summaries/my-doc]] (short) — Mentions [[concepts/attention]] here\n\n"
            "## Concepts\n\n"
            "## Explorations\n",
            encoding="utf-8",
        )

        _update_index(
            wiki,
            "my-doc",
            ["attention"],
            concept_briefs={"attention": "New brief"},
        )

        text = (wiki / "index.md").read_text()
        assert "- [[summaries/my-doc]] (short) — Mentions [[concepts/attention]] here" in text
        assert "- [[concepts/attention]] — New brief" in text

    def test_recovers_when_documents_section_missing(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "index.md").write_text(
            "# Index\n\n## Concepts\n\n## Explorations\n",
            encoding="utf-8",
        )
        _update_index(wiki, "my-doc", [], doc_brief="Brief")
        text = (wiki / "index.md").read_text()
        assert "## Documents" in text
        assert "[[summaries/my-doc]] (short) — Brief" in text

    def test_recovers_when_concepts_section_missing(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n\n## Explorations\n",
            encoding="utf-8",
        )
        _update_index(wiki, "my-doc", ["attention"],
                       concept_briefs={"attention": "Focus"})
        text = (wiki / "index.md").read_text()
        assert "## Concepts" in text
        assert "[[concepts/attention]] — Focus" in text
        assert "[[summaries/my-doc]]" in text


class TestReadWikiContext:
    def test_empty_wiki(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        index, concepts = _read_wiki_context(wiki)
        assert index == ""
        assert concepts == []

    def test_with_content(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "index.md").write_text("# Index\n", encoding="utf-8")
        concepts_dir = wiki / "concepts"
        concepts_dir.mkdir()
        (concepts_dir / "attention.md").write_text("# Attention", encoding="utf-8")
        (concepts_dir / "transformer.md").write_text("# Transformer", encoding="utf-8")
        index, concepts = _read_wiki_context(wiki)
        assert "# Index" in index
        assert concepts == ["attention", "transformer"]


class TestReadConceptBriefs:
    def test_empty_wiki(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "concepts").mkdir()
        assert _read_concept_briefs(wiki) == "(none yet)"

    def test_no_concepts_dir(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        assert _read_concept_briefs(wiki) == "(none yet)"

    def test_reads_briefs_with_frontmatter(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "attention.md").write_text(
            "---\nsources: [paper.pdf]\n---\n\nAttention is a mechanism that allows models to focus on relevant parts.",
            encoding="utf-8",
        )
        result = _read_concept_briefs(wiki)
        assert "- attention:" in result
        assert "Attention is a mechanism" in result
        assert "sources" not in result
        assert "---" not in result

    def test_reads_briefs_without_frontmatter(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "transformer.md").write_text(
            "Transformer is a neural network architecture based on attention.",
            encoding="utf-8",
        )
        result = _read_concept_briefs(wiki)
        assert "- transformer:" in result
        assert "Transformer is a neural network" in result

    def test_truncates_long_content(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        long_body = "A" * 300
        (concepts / "longconcept.md").write_text(long_body, encoding="utf-8")
        result = _read_concept_briefs(wiki)
        # The brief part should be truncated at 150 chars
        brief = result.split("- longconcept: ", 1)[1]
        assert len(brief) == 150
        assert brief == "A" * 150

    def test_sorted_alphabetically(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "zebra.md").write_text("Zebra concept.", encoding="utf-8")
        (concepts / "apple.md").write_text("Apple concept.", encoding="utf-8")
        (concepts / "mango.md").write_text("Mango concept.", encoding="utf-8")
        result = _read_concept_briefs(wiki)
        lines = result.strip().splitlines()
        slugs = [line.split(":")[0].lstrip("- ") for line in lines]
        assert slugs == ["apple", "mango", "zebra"]

    def test_reads_brief_from_frontmatter(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "attention.md").write_text(
            "---\nsources: [paper.pdf]\nbrief: Selective focus mechanism\n---\n\n# Attention\n\nLong content...",
            encoding="utf-8",
        )
        result = _read_concept_briefs(wiki)
        assert "- attention: Selective focus mechanism" in result

    def test_falls_back_to_body_truncation(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "old.md").write_text(
            "---\nsources: [paper.pdf]\n---\n\nOld concept without brief field.",
            encoding="utf-8",
        )
        result = _read_concept_briefs(wiki)
        assert "- old: Old concept without brief field." in result


class TestBacklinkSummary:
    def test_adds_missing_concept_links(self, tmp_path):
        wiki = tmp_path / "wiki"
        summaries = wiki / "summaries"
        summaries.mkdir(parents=True)
        (summaries / "paper.md").write_text(
            "---\nsources: [paper.pdf]\n---\n\n# Summary\n\nContent about attention.",
            encoding="utf-8",
        )
        _backlink_summary(wiki, "paper", ["attention", "transformer"])
        text = (summaries / "paper.md").read_text()
        assert "[[concepts/attention]]" in text
        assert "[[concepts/transformer]]" in text

    def test_skips_already_linked(self, tmp_path):
        wiki = tmp_path / "wiki"
        summaries = wiki / "summaries"
        summaries.mkdir(parents=True)
        (summaries / "paper.md").write_text(
            "---\nsources: [paper.pdf]\n---\n\n# Summary\n\nSee [[concepts/attention]].",
            encoding="utf-8",
        )
        _backlink_summary(wiki, "paper", ["attention", "transformer"])
        text = (summaries / "paper.md").read_text()
        # attention already linked, should not duplicate
        assert text.count("[[concepts/attention]]") == 1
        # transformer should be added
        assert "[[concepts/transformer]]" in text

    def test_no_op_when_all_linked(self, tmp_path):
        wiki = tmp_path / "wiki"
        summaries = wiki / "summaries"
        summaries.mkdir(parents=True)
        original = "# Summary\n\n[[concepts/attention]] and [[concepts/transformer]]"
        (summaries / "paper.md").write_text(original, encoding="utf-8")
        _backlink_summary(wiki, "paper", ["attention", "transformer"])
        assert (summaries / "paper.md").read_text() == original

    def test_skips_if_file_missing(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        # Should not raise
        _backlink_summary(wiki, "nonexistent", ["attention"])

    def test_merges_into_existing_section(self, tmp_path):
        """Second add should merge into existing ## Related Concepts, not duplicate."""
        wiki = tmp_path / "wiki"
        summaries = wiki / "summaries"
        summaries.mkdir(parents=True)
        (summaries / "paper.md").write_text(
            "# Summary\n\nContent.\n\n## Related Concepts\n- [[concepts/attention]]\n",
            encoding="utf-8",
        )
        _backlink_summary(wiki, "paper", ["attention", "transformer"])
        text = (summaries / "paper.md").read_text()
        assert text.count("## Related Concepts") == 1
        assert "[[concepts/transformer]]" in text
        assert text.count("[[concepts/attention]]") == 1

    def test_section_with_trailing_whitespace_still_merges(self, tmp_path):
        """Heading with trailing space must merge into the existing section,
        not append a duplicate H2."""
        wiki = tmp_path / "wiki"
        summaries = wiki / "summaries"
        summaries.mkdir(parents=True)
        (summaries / "paper.md").write_text(
            "# Summary\n\nContent.\n\n## Related Concepts \n- [[concepts/attention]]\n",
            encoding="utf-8",
        )
        _backlink_summary(wiki, "paper", ["attention", "transformer"])
        text = (summaries / "paper.md").read_text()
        assert "[[concepts/transformer]]" in text
        assert text.count("## Related Concepts") == 1


class TestBacklinkConcepts:
    def test_adds_summary_link_to_concept(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "attention.md").write_text(
            "---\nsources: [paper.pdf]\n---\n\n# Attention\n\nContent.",
            encoding="utf-8",
        )
        _backlink_concepts(wiki, "paper", ["attention"])
        text = (concepts / "attention.md").read_text()
        assert "[[summaries/paper]]" in text
        assert "## Related Documents" in text

    def test_skips_if_already_linked(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "attention.md").write_text(
            "# Attention\n\nBased on [[summaries/paper]].",
            encoding="utf-8",
        )
        _backlink_concepts(wiki, "paper", ["attention"])
        text = (concepts / "attention.md").read_text()
        assert text.count("[[summaries/paper]]") == 1
        assert "## Related Documents" not in text

    def test_merges_into_existing_section(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "attention.md").write_text(
            "# Attention\n\n## Related Documents\n- [[summaries/old-paper]]\n",
            encoding="utf-8",
        )
        _backlink_concepts(wiki, "new-paper", ["attention"])
        text = (concepts / "attention.md").read_text()
        assert text.count("## Related Documents") == 1
        assert "[[summaries/old-paper]]" in text
        assert "[[summaries/new-paper]]" in text

    def test_skips_missing_concept_file(self, tmp_path):
        wiki = tmp_path / "wiki"
        (wiki / "concepts").mkdir(parents=True)
        # Should not raise
        _backlink_concepts(wiki, "paper", ["nonexistent"])

    def test_section_with_trailing_whitespace_still_merges(self, tmp_path):
        """Heading with trailing space must merge into the existing section,
        not append a duplicate H2."""
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "attention.md").write_text(
            "# Attention\n\n## Related Documents \n- [[summaries/old-paper]]\n",
            encoding="utf-8",
        )
        _backlink_concepts(wiki, "new-paper", ["attention"])
        text = (concepts / "attention.md").read_text()
        assert "[[summaries/new-paper]]" in text
        assert "[[summaries/old-paper]]" in text
        assert text.count("## Related Documents") == 1


class TestAddRelatedLink:
    def test_adds_see_also_link(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "attention.md").write_text(
            "---\nsources: [paper1.pdf]\n---\n\n# Attention\n\nSome content.",
            encoding="utf-8",
        )
        _add_related_link(wiki, "attention", "new-doc", "paper2.pdf")
        text = (concepts / "attention.md").read_text()
        assert "[[summaries/new-doc]]" in text
        assert "paper2.pdf" in text

    def test_skips_if_already_linked(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "attention.md").write_text(
            "---\nsources: [paper1.pdf]\n---\n\n# Attention\n\nSee also: [[summaries/new-doc]]",
            encoding="utf-8",
        )
        _add_related_link(wiki, "attention", "new-doc", "paper1.pdf")
        text = (concepts / "attention.md").read_text()
        assert text.count("[[summaries/new-doc]]") == 1

    def test_skips_if_file_missing(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        # Should not raise
        _add_related_link(wiki, "nonexistent", "doc", "file.pdf")

    def test_frontmatter_without_space_after_colon_still_merges(self, tmp_path):
        """sources:[a] (no space after colon) must still prepend new source."""
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "attention.md").write_text(
            "---\nsources:[paper1.pdf]\n---\n\n# Attention\n",
            encoding="utf-8",
        )
        _add_related_link(wiki, "attention", "new-doc", "paper2.pdf")
        text = (concepts / "attention.md").read_text()
        assert "paper2.pdf" in text
        assert "paper1.pdf" in text
        assert "[[summaries/new-doc]]" in text

    def test_frontmatter_without_sources_line_gets_one_inserted(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "attention.md").write_text(
            "---\nbrief: Focus mechanism\n---\n\n# Attention\n",
            encoding="utf-8",
        )
        _add_related_link(wiki, "attention", "new-doc", "paper.pdf")
        text = (concepts / "attention.md").read_text()
        assert 'sources: ["paper.pdf"]' in text
        # Brief was not touched (existing line preserved); only sources was inserted.
        assert "brief: Focus mechanism" in text
        assert "[[summaries/new-doc]]" in text


def _mock_completion(responses: list[str]):
    """Create a mock for litellm.completion that returns responses in order."""
    call_count = {"n": 0}

    def side_effect(*args, **kwargs):
        idx = min(call_count["n"], len(responses) - 1)
        call_count["n"] += 1
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = responses[idx]
        mock_resp.usage = MagicMock(prompt_tokens=100, completion_tokens=50)
        mock_resp.usage.prompt_tokens_details = None
        return mock_resp

    return side_effect


def _mock_acompletion(responses: list[str]):
    """Create an async mock for litellm.acompletion."""
    call_count = {"n": 0}

    async def side_effect(*args, **kwargs):
        idx = min(call_count["n"], len(responses) - 1)
        call_count["n"] += 1
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = responses[idx]
        mock_resp.usage = MagicMock(prompt_tokens=100, completion_tokens=50)
        mock_resp.usage.prompt_tokens_details = None
        return mock_resp

    return side_effect


class TestCompileShortDoc:
    @pytest.mark.asyncio
    async def test_full_pipeline(self, tmp_path):
        # Setup KB structure
        wiki = tmp_path / "wiki"
        (wiki / "sources").mkdir(parents=True)
        (wiki / "summaries").mkdir(parents=True)
        (wiki / "concepts").mkdir(parents=True)
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n\n## Concepts\n\n## Explorations\n",
            encoding="utf-8",
        )
        source_path = wiki / "sources" / "test-doc.md"
        source_path.write_text("# Test Doc\n\nSome content about transformers.", encoding="utf-8")
        (tmp_path / ".openkb").mkdir()
        (tmp_path / "raw").mkdir()
        (tmp_path / "raw" / "test-doc.pdf").write_bytes(b"fake")

        summary_response = json.dumps({
            "brief": "Discusses transformers",
            "content": "# Summary\n\nThis document discusses transformers.",
        })
        concepts_list_response = json.dumps({
            "create": [{"name": "transformer", "title": "Transformer"}],
            "update": [],
            "related": [],
        })
        # The rewrite step (third sync call) returns raw Markdown.
        summary_rewrite_response = (
            "# Summary\n\nThis document discusses [[concepts/transformer]]."
        )
        concept_page_response = json.dumps({
            "brief": "NN architecture using self-attention",
            "content": "# Transformer\n\nA neural network architecture.",
        })

        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.completion = MagicMock(
                side_effect=_mock_completion([
                    summary_response,
                    concepts_list_response,
                    summary_rewrite_response,
                ])
            )
            mock_litellm.acompletion = AsyncMock(
                side_effect=_mock_acompletion([concept_page_response])
            )
            await compile_short_doc("test-doc", source_path, tmp_path, "gpt-4o-mini")

        # Verify summary written
        summary_path = wiki / "summaries" / "test-doc.md"
        assert summary_path.exists()
        summary_text = summary_path.read_text()
        assert "full_text: sources/test-doc.md" in summary_text
        # Summary body comes from the rewrite step
        assert "[[concepts/transformer]]" in summary_text

        # Verify concept written
        concept_path = wiki / "concepts" / "transformer.md"
        assert concept_path.exists()
        assert 'sources: ["summaries/test-doc.md"]' in concept_path.read_text()

        # Verify index updated
        index_text = (wiki / "index.md").read_text()
        assert "[[summaries/test-doc]]" in index_text
        assert "[[concepts/transformer]]" in index_text

    @pytest.mark.asyncio
    async def test_handles_bad_json(self, tmp_path):
        wiki = tmp_path / "wiki"
        (wiki / "sources").mkdir(parents=True)
        (wiki / "summaries").mkdir(parents=True)
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n\n## Concepts\n",
            encoding="utf-8",
        )
        source_path = wiki / "sources" / "doc.md"
        source_path.write_text("Content", encoding="utf-8")
        (tmp_path / ".openkb").mkdir()

        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.completion = MagicMock(
                side_effect=_mock_completion(["Plain summary text", "not valid json"])
            )
            # Should not raise
            await compile_short_doc("doc", source_path, tmp_path, "gpt-4o-mini")

        # Summary should still be written
        assert (wiki / "summaries" / "doc.md").exists()


class TestCompileShortDocFallbacks:
    """Regression tests for the summary-rewrite resilience path.

    The rewrite call can fail (API error, empty response, parse error).
    In every failure mode the v1 summary should be written to disk —
    stripped against the current whitelist so it doesn't reintroduce
    ghost wikilinks — never an empty file or missing file.
    """

    @staticmethod
    def _setup_kb(tmp_path):
        wiki = tmp_path / "wiki"
        (wiki / "sources").mkdir(parents=True)
        (wiki / "summaries").mkdir(parents=True)
        (wiki / "concepts").mkdir(parents=True)
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n\n## Concepts\n\n## Explorations\n",
            encoding="utf-8",
        )
        (tmp_path / ".openkb").mkdir()
        source_path = wiki / "sources" / "doc.md"
        source_path.write_text("Body.", encoding="utf-8")
        return wiki, source_path

    @pytest.mark.asyncio
    async def test_rewrite_empty_response_falls_back_to_v1(self, tmp_path):
        wiki, source_path = self._setup_kb(tmp_path)

        v1_summary_content = (
            "# Summary\n\nDiscusses [[concepts/transformer]] and [[concepts/ghost]]."
        )
        summary_response = json.dumps({
            "brief": "B", "content": v1_summary_content,
        })
        plan_response = json.dumps({
            "create": [{"name": "transformer", "title": "Transformer"}],
            "update": [], "related": [],
        })
        # Rewrite returns an empty string → must fall back to v1
        rewrite_response = ""
        concept_response = json.dumps({"brief": "C", "content": "# T\n\nBody."})

        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.completion = MagicMock(
                side_effect=_mock_completion([
                    summary_response, plan_response, rewrite_response,
                ])
            )
            mock_litellm.acompletion = AsyncMock(
                side_effect=_mock_acompletion([concept_response])
            )
            await compile_short_doc("doc", source_path, tmp_path, "gpt-4o-mini")

        summary_path = wiki / "summaries" / "doc.md"
        assert summary_path.exists()
        text = summary_path.read_text()
        # The v1 content should be on disk (fallback) — stripped of ghosts.
        assert "Discusses" in text
        assert "[[concepts/transformer]]" in text       # valid link kept
        assert "[[concepts/ghost]]" not in text         # ghost stripped
        assert "ghost" in text                          # but plain text remains

    @pytest.mark.asyncio
    async def test_rewrite_exception_falls_back_to_v1(self, tmp_path):
        wiki, source_path = self._setup_kb(tmp_path)

        v1_summary_content = (
            "# Summary\n\nUses [[concepts/transformer]] mechanism."
        )
        summary_response = json.dumps({
            "brief": "B", "content": v1_summary_content,
        })
        plan_response = json.dumps({
            "create": [{"name": "transformer", "title": "Transformer"}],
            "update": [], "related": [],
        })
        concept_response = json.dumps({"brief": "C", "content": "# T\n\nBody."})

        # Third sync call (rewrite) raises a simulated API error.
        sync_call_count = {"n": 0}

        def sync_side_effect(*args, **kwargs):
            idx = sync_call_count["n"]
            sync_call_count["n"] += 1
            if idx == 2:  # the summary-rewrite call
                raise RuntimeError("simulated API failure")
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            mock_resp.choices[0].message.content = [
                summary_response, plan_response,
            ][idx]
            mock_resp.usage = MagicMock(prompt_tokens=1, completion_tokens=1)
            mock_resp.usage.prompt_tokens_details = None
            return mock_resp

        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.completion = MagicMock(side_effect=sync_side_effect)
            mock_litellm.acompletion = AsyncMock(
                side_effect=_mock_acompletion([concept_response])
            )
            # Must NOT raise out of compile_short_doc
            await compile_short_doc("doc", source_path, tmp_path, "gpt-4o-mini")

        summary_path = wiki / "summaries" / "doc.md"
        assert summary_path.exists()
        text = summary_path.read_text()
        assert "Uses" in text
        assert "[[concepts/transformer]]" in text

    @pytest.mark.asyncio
    async def test_plan_parse_failure_strips_v1_summary_ghosts(self, tmp_path):
        wiki, source_path = self._setup_kb(tmp_path)

        v1_summary_content = (
            "# Summary\n\nReferences [[concepts/nonexistent]] heavily."
        )
        summary_response = json.dumps({
            "brief": "B", "content": v1_summary_content,
        })
        # Plan call returns non-JSON garbage → triggers early return
        plan_response = "not valid json at all"

        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.completion = MagicMock(
                side_effect=_mock_completion([summary_response, plan_response])
            )
            await compile_short_doc("doc", source_path, tmp_path, "gpt-4o-mini")

        summary_path = wiki / "summaries" / "doc.md"
        assert summary_path.exists()
        text = summary_path.read_text()
        # Ghost link should be stripped to plain text on fallback path
        assert "[[concepts/nonexistent]]" not in text
        assert "nonexistent" in text  # display text preserved
        assert "References" in text

    @pytest.mark.asyncio
    async def test_empty_plan_strips_v1_summary_ghosts(self, tmp_path):
        wiki, source_path = self._setup_kb(tmp_path)

        v1_summary_content = (
            "# Summary\n\nMentions [[concepts/imaginary]] briefly."
        )
        summary_response = json.dumps({
            "brief": "B", "content": v1_summary_content,
        })
        empty_plan_response = json.dumps({
            "create": [], "update": [], "related": [],
        })

        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.completion = MagicMock(
                side_effect=_mock_completion([summary_response, empty_plan_response])
            )
            await compile_short_doc("doc", source_path, tmp_path, "gpt-4o-mini")

        summary_path = wiki / "summaries" / "doc.md"
        assert summary_path.exists()
        text = summary_path.read_text()
        assert "[[concepts/imaginary]]" not in text
        assert "imaginary" in text  # plain text preserved


class TestCacheControl:
    """Verify cache_control breakpoints are emitted on the right messages
    so Anthropic prompt caching can hit on every reuse of the base context.
    """

    @staticmethod
    def _has_cache_breakpoint(message: dict) -> bool:
        content = message.get("content")
        if not isinstance(content, list):
            return False
        return any(
            isinstance(b, dict) and b.get("cache_control", {}).get("type") == "ephemeral"
            for b in content
        )

    @pytest.mark.asyncio
    async def test_short_doc_marks_doc_and_summary(self, tmp_path):
        wiki = tmp_path / "wiki"
        (wiki / "sources").mkdir(parents=True)
        (wiki / "summaries").mkdir(parents=True)
        (wiki / "concepts").mkdir(parents=True)
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n\n## Concepts\n", encoding="utf-8",
        )
        src = wiki / "sources" / "doc.md"
        src.write_text("Body text about caching.", encoding="utf-8")
        (tmp_path / ".openkb").mkdir()

        summary_response = json.dumps({"brief": "B", "content": "summary body"})
        plan_response = json.dumps({
            "create": [{"name": "topic", "title": "Topic"}],
            "update": [], "related": [],
        })
        # 3rd sync call is the summary-rewrite (raw Markdown, not JSON).
        summary_rewrite_response = "# Summary\n\nrewritten body"
        concept_response = json.dumps({"brief": "C", "content": "page body"})

        captured_sync_calls: list[list[dict]] = []
        captured_async_calls: list[list[dict]] = []

        sync_responses = [
            summary_response,
            plan_response,
            summary_rewrite_response,
        ]

        def sync_side_effect(*args, **kwargs):
            captured_sync_calls.append(kwargs["messages"])
            idx = min(len(captured_sync_calls) - 1, len(sync_responses) - 1)
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            mock_resp.choices[0].message.content = sync_responses[idx]
            mock_resp.usage = MagicMock(prompt_tokens=1, completion_tokens=1)
            mock_resp.usage.prompt_tokens_details = None
            return mock_resp

        async def async_side_effect(*args, **kwargs):
            captured_async_calls.append(kwargs["messages"])
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            mock_resp.choices[0].message.content = concept_response
            mock_resp.usage = MagicMock(prompt_tokens=1, completion_tokens=1)
            mock_resp.usage.prompt_tokens_details = None
            return mock_resp

        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.completion = MagicMock(side_effect=sync_side_effect)
            mock_litellm.acompletion = AsyncMock(side_effect=async_side_effect)
            await compile_short_doc("doc", src, tmp_path, "anthropic/claude-sonnet-4-5")

        # Step 1 (summary): doc_msg carries the breakpoint (BP1).
        summary_call = captured_sync_calls[0]
        assert summary_call[0]["role"] == "system"
        assert summary_call[1]["role"] == "user"
        assert self._has_cache_breakpoint(summary_call[1]), (
            "doc_msg in summary call must carry an ephemeral cache_control marker"
        )

        # Step 2 (plan): doc_msg AND assistant summary both carry breakpoints
        # (BP1 + BP2). Plan does NOT include the known_targets message.
        plan_call = captured_sync_calls[1]
        assert self._has_cache_breakpoint(plan_call[1])
        assert plan_call[2]["role"] == "assistant"
        assert self._has_cache_breakpoint(plan_call[2]), (
            "assistant summary in plan call must carry a cache_control marker"
        )

        # Step 3 (concept generation): BP1 + BP2 + new BP3 (known_targets msg).
        assert captured_async_calls, "expected at least one async concept call"
        concept_call = captured_async_calls[0]
        assert self._has_cache_breakpoint(concept_call[1])
        assert self._has_cache_breakpoint(concept_call[2])
        # New: BP3 is the known_targets user message at index 3, sitting
        # between summary_msg and the per-concept user prompt.
        assert concept_call[3]["role"] == "user"
        assert self._has_cache_breakpoint(concept_call[3]), (
            "known_targets message in concept call must carry a cache_control marker"
        )

        # Step 4 (summary rewrite): same three breakpoints reused — this is
        # the whole point of the BP3 design, the whitelist is cached not
        # re-billed per call.
        rewrite_call = captured_sync_calls[2]
        assert self._has_cache_breakpoint(rewrite_call[1])  # BP1
        assert self._has_cache_breakpoint(rewrite_call[2])  # BP2
        assert rewrite_call[3]["role"] == "user"
        assert self._has_cache_breakpoint(rewrite_call[3]), (  # BP3
            "known_targets message in summary-rewrite call must carry "
            "a cache_control marker"
        )

    @pytest.mark.asyncio
    async def test_long_doc_marks_doc_message(self, tmp_path):
        wiki = tmp_path / "wiki"
        (wiki / "summaries").mkdir(parents=True)
        (wiki / "concepts").mkdir(parents=True)
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n\n## Concepts\n", encoding="utf-8",
        )
        sp = wiki / "summaries" / "big.md"
        sp.write_text("PageIndex tree summary.", encoding="utf-8")
        (tmp_path / ".openkb").mkdir()

        captured: list[list[dict]] = []
        plan_response = json.dumps({"create": [], "update": [], "related": []})

        def sync_side_effect(*args, **kwargs):
            captured.append(kwargs["messages"])
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            # First call: overview (plain text); second: plan (JSON).
            mock_resp.choices[0].message.content = (
                "Overview text" if len(captured) == 1 else plan_response
            )
            mock_resp.usage = MagicMock(prompt_tokens=1, completion_tokens=1)
            mock_resp.usage.prompt_tokens_details = None
            return mock_resp

        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.completion = MagicMock(side_effect=sync_side_effect)
            mock_litellm.acompletion = AsyncMock()
            await compile_long_doc(
                "big", sp, "doc-id-1", tmp_path, "anthropic/claude-sonnet-4-5",
            )

        overview_call = captured[0]
        assert overview_call[1]["role"] == "user"
        assert self._has_cache_breakpoint(overview_call[1])


class TestCompileLongDoc:
    @pytest.mark.asyncio
    async def test_full_pipeline(self, tmp_path):
        wiki = tmp_path / "wiki"
        (wiki / "summaries").mkdir(parents=True)
        (wiki / "concepts").mkdir(parents=True)
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n\n## Concepts\n",
            encoding="utf-8",
        )
        summary_path = wiki / "summaries" / "big-doc.md"
        summary_path.write_text("# Big Doc\n\nPageIndex summary tree.", encoding="utf-8")
        openkb_dir = tmp_path / ".openkb"
        openkb_dir.mkdir()
        (openkb_dir / "config.yaml").write_text("model: gpt-4o-mini\n")
        (tmp_path / "raw").mkdir()
        (tmp_path / "raw" / "big-doc.pdf").write_bytes(b"fake")

        overview_response = "Overview of the big document."
        concepts_list_response = json.dumps({
            "create": [{"name": "deep-learning", "title": "Deep Learning"}],
            "update": [],
            "related": [],
        })
        concept_page_response = json.dumps({
            "brief": "Subfield of ML using neural networks",
            "content": "# Deep Learning\n\nA subfield of ML.",
        })

        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.completion = MagicMock(
                side_effect=_mock_completion([overview_response, concepts_list_response])
            )
            mock_litellm.acompletion = AsyncMock(
                side_effect=_mock_acompletion([concept_page_response])
            )
            await compile_long_doc(
                "big-doc", summary_path, "doc-123", tmp_path, "gpt-4o-mini"
            )

        concept_path = wiki / "concepts" / "deep-learning.md"
        assert concept_path.exists()
        assert "Deep Learning" in concept_path.read_text()

        index_text = (wiki / "index.md").read_text()
        assert "[[summaries/big-doc]]" in index_text
        assert "[[concepts/deep-learning]]" in index_text


class TestCompileConceptsPlan:
    """Integration tests for _compile_concepts with the new plan format."""

    def _setup_wiki(self, tmp_path, existing_concepts=None):
        """Helper to set up a wiki directory with optional existing concepts."""
        wiki = tmp_path / "wiki"
        (wiki / "summaries").mkdir(parents=True)
        (wiki / "concepts").mkdir(parents=True)
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n\n## Concepts\n",
            encoding="utf-8",
        )
        (tmp_path / "raw").mkdir(exist_ok=True)
        (tmp_path / "raw" / "test-doc.pdf").write_bytes(b"fake")

        if existing_concepts:
            for name, content in existing_concepts.items():
                (wiki / "concepts" / f"{name}.md").write_text(
                    content, encoding="utf-8",
                )

        return wiki

    @pytest.mark.asyncio
    async def test_create_and_update_flow(self, tmp_path):
        """Pre-existing 'attention' concept; plan creates 'flash-attention' and updates 'attention'."""
        wiki = self._setup_wiki(tmp_path, existing_concepts={
            "attention": "---\nsources: [old-paper.pdf]\n---\n\n# Attention\n\nOriginal content about attention.",
        })

        plan_response = json.dumps({
            "create": [{"name": "flash-attention", "title": "Flash Attention"}],
            "update": [{"name": "attention", "title": "Attention"}],
            "related": [],
        })
        create_page_response = json.dumps({
            "brief": "Efficient attention algorithm",
            "content": "# Flash Attention\n\nAn efficient attention algorithm.",
        })
        update_page_response = json.dumps({
            "brief": "Updated attention mechanism",
            "content": "# Attention\n\nUpdated content with new info.",
        })

        system_msg = {"role": "system", "content": "You are a wiki agent."}
        doc_msg = {"role": "user", "content": "Document about attention mechanisms."}
        summary = "Summary of the document."

        call_order = {"n": 0}

        async def ordered_acompletion(*args, **kwargs):
            idx = call_order["n"]
            call_order["n"] += 1
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            # create tasks come first, then update tasks
            if idx == 0:
                mock_resp.choices[0].message.content = create_page_response
            else:
                mock_resp.choices[0].message.content = update_page_response
            mock_resp.usage = MagicMock(prompt_tokens=100, completion_tokens=50)
            mock_resp.usage.prompt_tokens_details = None
            return mock_resp

        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.completion = MagicMock(
                side_effect=_mock_completion([plan_response])
            )
            mock_litellm.acompletion = AsyncMock(
                side_effect=ordered_acompletion
            )
            await _compile_concepts(
                wiki, tmp_path, "gpt-4o-mini", system_msg, doc_msg,
                summary, "test-doc", 5,
            )

        # Verify flash-attention created
        fa_path = wiki / "concepts" / "flash-attention.md"
        assert fa_path.exists()
        fa_text = fa_path.read_text()
        assert 'sources: ["summaries/test-doc.md"]' in fa_text
        assert "Flash Attention" in fa_text

        # Verify attention updated (is_update=True path in _write_concept)
        att_path = wiki / "concepts" / "attention.md"
        assert att_path.exists()
        att_text = att_path.read_text()
        assert "summaries/test-doc.md" in att_text
        assert "old-paper.pdf" in att_text

        # Verify index updated
        index_text = (wiki / "index.md").read_text()
        assert "[[concepts/flash-attention]]" in index_text
        assert "[[concepts/attention]]" in index_text

    @pytest.mark.asyncio
    async def test_related_adds_link_no_llm(self, tmp_path):
        """Plan has only related items. No acompletion calls should be made."""
        wiki = self._setup_wiki(tmp_path, existing_concepts={
            "transformer": "---\nsources: [old.pdf]\n---\n\n# Transformer\n\nContent about transformers.",
        })

        plan_response = json.dumps({
            "create": [],
            "update": [],
            "related": ["transformer"],
        })

        system_msg = {"role": "system", "content": "You are a wiki agent."}
        doc_msg = {"role": "user", "content": "Document content."}
        summary = "Summary."

        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.completion = MagicMock(
                side_effect=_mock_completion([plan_response])
            )
            mock_litellm.acompletion = AsyncMock()
            await _compile_concepts(
                wiki, tmp_path, "gpt-4o-mini", system_msg, doc_msg,
                summary, "test-doc", 5,
            )
            # acompletion should never be called — related is code-only
            mock_litellm.acompletion.assert_not_called()

        # Verify link added to transformer page
        transformer_text = (wiki / "concepts" / "transformer.md").read_text()
        assert "[[summaries/test-doc]]" in transformer_text
        assert "summaries/test-doc.md" in transformer_text

    @pytest.mark.asyncio
    async def test_fallback_list_format(self, tmp_path):
        """LLM returns a flat array instead of dict — treated as all create."""
        wiki = self._setup_wiki(tmp_path)

        plan_response = json.dumps([
            {"name": "attention", "title": "Attention"},
        ])
        concept_page_response = json.dumps({
            "brief": "A mechanism for focusing",
            "content": "# Attention\n\nA mechanism for focusing.",
        })

        system_msg = {"role": "system", "content": "You are a wiki agent."}
        doc_msg = {"role": "user", "content": "Document content."}
        summary = "Summary."

        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.completion = MagicMock(
                side_effect=_mock_completion([plan_response])
            )
            mock_litellm.acompletion = AsyncMock(
                side_effect=_mock_acompletion([concept_page_response])
            )
            await _compile_concepts(
                wiki, tmp_path, "gpt-4o-mini", system_msg, doc_msg,
                summary, "test-doc", 5,
            )

        # Verify concept was created (not updated)
        att_path = wiki / "concepts" / "attention.md"
        assert att_path.exists()
        att_text = att_path.read_text()
        assert 'sources: ["summaries/test-doc.md"]' in att_text
        assert "Attention" in att_text


class TestBriefIntegration:
    @pytest.mark.asyncio
    async def test_short_doc_briefs_in_index_and_frontmatter(self, tmp_path):
        wiki = tmp_path / "wiki"
        (wiki / "sources").mkdir(parents=True)
        (wiki / "summaries").mkdir(parents=True)
        (wiki / "concepts").mkdir(parents=True)
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n\n## Concepts\n\n## Explorations\n",
            encoding="utf-8",
        )
        source_path = wiki / "sources" / "test-doc.md"
        source_path.write_text("# Test Doc\n\nContent.", encoding="utf-8")
        (tmp_path / ".openkb").mkdir()
        (tmp_path / "raw").mkdir()
        (tmp_path / "raw" / "test-doc.pdf").write_bytes(b"fake")

        summary_resp = json.dumps({
            "brief": "A paper about transformers",
            "content": "# Summary\n\nThis paper discusses transformers.",
        })
        plan_resp = json.dumps({
            "create": [{"name": "transformer", "title": "Transformer"}],
            "update": [],
            "related": [],
        })
        concept_resp = json.dumps({
            "brief": "NN architecture using self-attention",
            "content": "# Transformer\n\nA neural network architecture.",
        })

        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.completion = MagicMock(
                side_effect=_mock_completion([summary_resp, plan_resp])
            )
            mock_litellm.acompletion = AsyncMock(
                side_effect=_mock_acompletion([concept_resp])
            )
            await compile_short_doc("test-doc", source_path, tmp_path, "gpt-4o-mini")

        # Summary frontmatter has doc_type and full_text
        summary_text = (wiki / "summaries" / "test-doc.md").read_text()
        assert "doc_type: short" in summary_text
        assert "full_text: sources/test-doc.md" in summary_text

        # Concept frontmatter has brief
        concept_text = (wiki / "concepts" / "transformer.md").read_text()
        assert 'brief: "NN architecture using self-attention"' in concept_text

        # Index has briefs
        index_text = (wiki / "index.md").read_text()
        assert "— A paper about transformers" in index_text
        assert "— NN architecture using self-attention" in index_text
