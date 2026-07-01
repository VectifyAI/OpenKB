"""Tests for openkb.agent.compiler pipeline."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openkb.agent.compiler import (
    _ENTITY_TYPE_LIST,
    _add_related_link,
    _backlink_concepts,
    _backlink_entities,
    _backlink_summary,
    _backlink_summary_entities,
    _compile_concepts,
    _filter_entity_items,
    _parse_entities_plan,
    _parse_json,
    _prepend_source_to_frontmatter,
    _read_concept_briefs,
    _read_entity_briefs,
    _read_wiki_context,
    _remove_source_from_frontmatter,
    _sanitize_concept_name,
    _update_index,
    _write_concept,
    _write_entity,
    _write_summary,
    compile_long_doc,
    compile_short_doc,
    remove_doc_from_entity_pages,
)
from openkb.config import resolve_entity_types
from openkb.schema import AGENTS_MD


class TestFrontmatterSourceMutation:
    """``_prepend_source_to_frontmatter``/``_remove_source_from_frontmatter`` must preserve existing
    frontmatter even when the page ends at the closing ``---`` with no trailing
    newline — ``frontmatter.split`` then returns a block ending in a bare
    ``\\n---`` rather than ``\\n---\\n``.
    """

    def test_prepend_preserves_keys_without_trailing_newline(self):
        text = '---\nsources: ["summaries/p1.md"]\ntype: "Concept"\ndescription: "Focus"\n---'
        out = _prepend_source_to_frontmatter(text, "summaries/p2.md")
        assert out.startswith("---\n")  # opening delimiter kept
        assert 'type: "Concept"' in out  # other keys kept
        assert 'description: "Focus"' in out
        assert "summaries/p1.md" in out  # existing source kept
        assert "summaries/p2.md" in out  # new source prepended

    def test_remove_preserves_keys_without_trailing_newline(self):
        text = '---\ntype: "Organization"\nsources: ["summaries/doc.md"]\n---'
        out, now_empty = _remove_source_from_frontmatter(text, "summaries/doc.md")
        assert now_empty is True  # it was the only source
        assert 'type: "Organization"' in out  # other key preserved
        assert "summaries/doc.md" not in out  # source removed

    def test_prepend_with_body_is_unchanged(self):
        text = '---\nsources: ["a.md"]\ntype: "Concept"\n---\n\nBody.\n'
        out = _prepend_source_to_frontmatter(text, "b.md")
        assert out.startswith("---\n")
        assert "b.md" in out and "a.md" in out
        assert out.endswith("\n\nBody.\n")  # body + closing untouched


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
        text = json.dumps(
            {
                "create": [{"name": "foo", "title": "Foo"}],
                "update": [{"name": "bar", "title": "Bar"}],
                "related": ["baz"],
            }
        )
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


class TestParseEntitiesPlan:
    def test_extracts_entities_group(self):
        parsed = {
            "concepts": {"create": [{"name": "x", "title": "X"}], "update": [], "related": []},
            "entities": {
                "create": [{"name": "anthropic", "title": "Anthropic", "type": "organization"}],
                "update": [],
                "related": ["nvidia"],
            },
        }
        ents = _parse_entities_plan(parsed)
        assert ents["create"] == [
            {"name": "anthropic", "title": "Anthropic", "type": "organization"}
        ]
        assert ents["related"] == ["nvidia"]

    def test_missing_entities_key_is_empty(self):
        ents = _parse_entities_plan({"create": [], "update": [], "related": []})
        assert ents == {"create": [], "update": [], "related": []}

    def test_bad_type_falls_back_to_other(self):
        parsed = {
            "entities": {
                "create": [{"name": "x", "title": "X", "type": "alien"}],
                "update": [],
                "related": [],
            }
        }
        ents = _parse_entities_plan(parsed)
        assert ents["create"][0]["type"] == "other"


class TestResolveEntityTypes:
    def test_default_when_key_absent(self):
        assert resolve_entity_types({}) == list(_ENTITY_TYPE_LIST)

    def test_custom_list_is_used_and_normalized(self):
        out = resolve_entity_types({"entity_types": ["Person", " Dataset ", "MODEL"]})
        assert out == ["person", "dataset", "model", "other"]

    def test_always_includes_other(self):
        out = resolve_entity_types({"entity_types": ["person", "dataset"]})
        assert "other" in out
        # already-present "other" is not duplicated
        out2 = resolve_entity_types({"entity_types": ["dataset", "other"]})
        assert out2.count("other") == 1

    def test_dedupes_preserving_order(self):
        out = resolve_entity_types({"entity_types": ["a", "a", "b"]})
        assert out == ["a", "b", "other"]

    def test_malformed_string_falls_back_to_default(self):
        assert resolve_entity_types({"entity_types": "person"}) == list(_ENTITY_TYPE_LIST)

    def test_empty_list_falls_back_to_default(self):
        assert resolve_entity_types({"entity_types": []}) == list(_ENTITY_TYPE_LIST)

    def test_all_empty_strings_falls_back_to_default(self):
        assert resolve_entity_types({"entity_types": ["", "  "]}) == list(_ENTITY_TYPE_LIST)

    def test_sanitizes_punctuation_and_skips_non_strings(self):
        # '{'/'}' and other punctuation are stripped (so they can't leak into a
        # prompt template's .format()); non-string items (YAML null, ints) are
        # skipped (str(None) must NOT become the type "none").
        out = resolve_entity_types({"entity_types": ["Per{son}", None, 123, "data set!"]})
        assert out == ["person", "data set", "other"]


class TestFilterEntityItemsCustomTypes:
    def test_custom_type_in_valid_types_is_kept(self):
        valid = frozenset({"person", "dataset", "other"})
        items = [{"name": "imagenet", "title": "ImageNet", "type": "dataset"}]
        out = _filter_entity_items(items, valid)
        assert out == [{"name": "imagenet", "title": "ImageNet", "type": "dataset"}]

    def test_type_not_in_valid_types_is_coerced_to_other(self):
        valid = frozenset({"person", "dataset", "other"})
        items = [{"name": "x", "title": "X", "type": "organization"}]
        out = _filter_entity_items(items, valid)
        assert out[0]["type"] == "other"

    def test_default_valid_types_backward_compat(self):
        # No valid_types arg → module default enum is used.
        items = [{"name": "x", "title": "X", "type": "organization"}]
        out = _filter_entity_items(items)
        assert out[0]["type"] == "organization"


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
    def test_writes_type_and_description(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        _write_summary(wiki, "my-doc", "# Summary\n\nContent.", description="A one-line summary.")
        text = (wiki / "summaries" / "my-doc.md").read_text()
        assert 'type: "Summary"' in text
        assert 'description: "A one-line summary."' in text
        assert "doc_type: short" in text
        assert 'full_text: "sources/my-doc.md"' in text
        assert "# Summary" in text

    def test_omits_description_when_empty(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        _write_summary(wiki, "my-doc", "# Summary\n\nContent.")
        text = (wiki / "summaries" / "my-doc.md").read_text()
        assert 'type: "Summary"' in text
        assert "description:" not in text


class TestWriteConcept:
    def test_new_concept_with_brief(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        _write_concept(
            wiki,
            "attention",
            "# Attention\n\nDetails.",
            "paper.pdf",
            False,
            brief="Mechanism for selective focus",
        )
        path = wiki / "concepts" / "attention.md"
        assert path.exists()
        text = path.read_text()
        assert 'sources: ["paper.pdf"]' in text
        assert 'description: "Mechanism for selective focus"' in text
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
        assert 'description: "Updated brief"' in text
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

    def test_new_concept_has_type_and_description(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        _write_concept(
            wiki,
            "attention",
            "# Attention\n\nDetails.",
            "summaries/p.md",
            False,
            brief="Mechanism for selective focus",
        )
        text = (wiki / "concepts" / "attention.md").read_text()
        assert 'type: "Concept"' in text
        assert 'description: "Mechanism for selective focus"' in text
        assert "brief:" not in text

    def test_new_concept_without_description_still_has_type(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        _write_concept(wiki, "attention", "# Attention\n\nDetails.", "summaries/p.md", False)
        text = (wiki / "concepts" / "attention.md").read_text()
        assert 'type: "Concept"' in text
        assert "description:" not in text

    def test_update_concept_sets_type_and_description(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "attention.md").write_text(
            '---\nsources: ["p1.pdf"]\ndescription: "Old"\n---\n\n# Attention\n\nOld.',
            encoding="utf-8",
        )
        _write_concept(wiki, "attention", "New.", "summaries/p2.md", True, brief="New one")
        text = (concepts / "attention.md").read_text()
        assert 'type: "Concept"' in text
        assert 'description: "New one"' in text
        assert "Old" not in text


class TestUpdateIndex:
    def test_appends_entries_with_briefs(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n\n## Concepts\n\n## Explorations\n",
            encoding="utf-8",
        )
        _update_index(
            wiki,
            "my-doc",
            ["attention", "transformer"],
            doc_brief="Introduces transformers",
            concept_briefs={"attention": "Focus mechanism", "transformer": "NN architecture"},
        )
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
        _update_index(wiki, "my-doc", ["attention"], concept_briefs={"attention": "Focus"})
        text = (wiki / "index.md").read_text()
        assert "## Concepts" in text
        assert "[[concepts/attention]] — Focus" in text
        assert "[[summaries/my-doc]]" in text

    def test_entities_inserted_before_explorations(self, tmp_path):
        """#8: an old index.md predating ## Entities must get it inserted
        before ## Explorations, not appended after it (canonical order)."""
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        # Old order: no ## Entities section yet.
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n\n## Concepts\n\n## Explorations\n",
            encoding="utf-8",
        )
        _update_index(
            wiki,
            "my-doc",
            [],
            entity_names=["anthropic"],
            entity_meta={"anthropic": ("organization", "AI lab.")},
        )
        text = (wiki / "index.md").read_text()
        assert "## Entities" in text
        # Canonical order: Entities before Explorations.
        assert text.index("## Entities") < text.index("## Explorations")
        assert "[[entities/anthropic]] (organization) — AI lab." in text


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

    def test_reads_description_field(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "attention.md").write_text(
            '---\nsources: ["p.pdf"]\ndescription: "Selective focus"\n---\n\n# A\n',
            encoding="utf-8",
        )
        assert "- attention: Selective focus" in _read_concept_briefs(wiki)


class TestReadEntityBriefs:
    def test_none_when_missing(self, tmp_path):
        assert _read_entity_briefs(tmp_path) == "(none yet)"

    def test_brief_type_and_source_count(self, tmp_path):
        ent = tmp_path / "entities"
        ent.mkdir()
        (ent / "anthropic.md").write_text(
            "---\n"
            "sources: [summaries/a.md, summaries/b.md]\n"
            "type: organization\n"
            "brief: AI lab behind Claude.\n"
            "---\n\n# Anthropic\n",
            encoding="utf-8",
        )
        out = _read_entity_briefs(tmp_path)
        assert out == "- anthropic (organization, 2 sources) — AI lab behind Claude."

    def test_empty_dir_returns_none(self, tmp_path):
        ent = tmp_path / "entities"
        ent.mkdir()
        assert _read_entity_briefs(tmp_path) == "(none yet)"

    def test_falls_back_to_body_when_no_brief(self, tmp_path):
        ent = tmp_path / "entities"
        ent.mkdir()
        body_text = "OpenAI is a research lab focused on artificial general intelligence."
        (ent / "openai.md").write_text(
            "---\n"
            "type: organization\n"
            "sources: [summaries/a.md, summaries/b.md, summaries/c.md]\n"
            "---\n\n" + body_text,
            encoding="utf-8",
        )
        out = _read_entity_briefs(tmp_path)
        # Should use truncated body (first 150 chars) as the brief
        expected_brief = body_text[:150]
        assert f" — {expected_brief}" in out
        # Should still include type and source count
        assert "(organization, 3 sources)" in out

    def test_sorted_alphabetically(self, tmp_path):
        ent = tmp_path / "entities"
        ent.mkdir()
        (ent / "zeta.md").write_text(
            "---\ntype: person\nsources: [summaries/a.md]\nbrief: Last letter of Greek alphabet.\n---\n",
            encoding="utf-8",
        )
        (ent / "alpha.md").write_text(
            "---\ntype: concept\nsources: [summaries/b.md]\nbrief: First letter of Greek alphabet.\n---\n",
            encoding="utf-8",
        )
        out = _read_entity_briefs(tmp_path)
        lines = out.strip().splitlines()
        assert lines[0].startswith("- alpha ")
        assert lines[1].startswith("- zeta ")

    def test_reads_description_and_lowercases_type(self, tmp_path):
        ent = tmp_path / "entities"
        ent.mkdir()
        (ent / "anthropic.md").write_text(
            "---\n"
            'sources: ["summaries/a.md", "summaries/b.md"]\n'
            'type: "Organization"\n'
            'description: "AI lab behind Claude."\n'
            "---\n\n# Anthropic\n",
            encoding="utf-8",
        )
        out = _read_entity_briefs(tmp_path)
        assert out == "- anthropic (organization, 2 sources) — AI lab behind Claude."

    def test_reads_legacy_brief_when_no_description(self, tmp_path):
        ent = tmp_path / "entities"
        ent.mkdir()
        (ent / "openai.md").write_text(
            "---\ntype: organization\nsources: [summaries/a.md]\n"
            "brief: Legacy one-liner.\n---\n\n# OpenAI\n",
            encoding="utf-8",
        )
        out = _read_entity_briefs(tmp_path)
        assert "— Legacy one-liner." in out


class TestWriteEntity:
    def test_new_entity_frontmatter(self, tmp_path):
        _write_entity(
            tmp_path,
            "anthropic",
            "# Anthropic\n\nAI lab.",
            "summaries/a.md",
            is_update=False,
            brief="AI lab behind Claude.",
            type_="organization",
            aliases=["Anthropic PBC"],
        )
        text = (tmp_path / "entities" / "anthropic.md").read_text(encoding="utf-8")
        assert 'type: "Organization"' in text
        assert 'description: "AI lab behind Claude."' in text
        assert "sources:" in text and "summaries/a.md" in text
        assert "Anthropic PBC" in text
        assert text.count("---") == 2  # exactly one frontmatter block

    def test_update_prepends_source_keeps_type(self, tmp_path):
        _write_entity(
            tmp_path,
            "anthropic",
            "# Anthropic\n\nv1.",
            "summaries/a.md",
            is_update=False,
            brief="b1",
            type_="organization",
            aliases=None,
        )
        _write_entity(
            tmp_path,
            "anthropic",
            "# Anthropic\n\nv2 richer.",
            "summaries/b.md",
            is_update=True,
            brief="b2",
            type_="organization",
            aliases=None,
        )
        text = (tmp_path / "entities" / "anthropic.md").read_text(encoding="utf-8")
        assert "summaries/b.md" in text and "summaries/a.md" in text
        # _yaml_list_line uses json.dumps: b prepended before a, double-quoted
        assert '"summaries/b.md", "summaries/a.md"' in text
        assert 'type: "Organization"' in text
        assert "v2 richer." in text
        assert "v1." not in text
        assert 'description: "b2"' in text

    def test_update_rebuilds_frontmatter_when_no_closing_delim(self, tmp_path):
        """#11: malformed existing file (opening --- but no closing ---) must
        not drop frontmatter; rebuild valid sources/type/brief on update."""
        entities = tmp_path / "entities"
        entities.mkdir(parents=True)
        # Opening delimiter, NO closing delimiter — find("---", 3) == -1.
        (entities / "anthropic.md").write_text(
            '---\nsources: ["summaries/a.md"]\ntype: organization\n'
            "# Anthropic (no closing fence)\n\nOld body.",
            encoding="utf-8",
        )
        _write_entity(
            tmp_path,
            "anthropic",
            "# Anthropic\n\nv2 rewritten.",
            "summaries/b.md",
            is_update=True,
            brief="AI lab.",
            type_="organization",
            aliases=None,
        )
        text = (entities / "anthropic.md").read_text(encoding="utf-8")
        # Frontmatter rebuilt with a proper closing delimiter, not body-only.
        assert text.startswith("---\n")
        assert text.count("---") == 2
        assert "sources:" in text and "summaries/b.md" in text
        # The PRE-EXISTING source must be preserved, not dropped when rebuilding.
        assert "summaries/a.md" in text
        assert 'type: "Organization"' in text
        assert 'description: "AI lab."' in text
        assert "v2 rewritten." in text

    def test_new_entity_type_capitalized_and_description(self, tmp_path):
        _write_entity(
            tmp_path,
            "anthropic",
            "# Anthropic\n\nAI lab.",
            "summaries/a.md",
            is_update=False,
            brief="AI lab behind Claude.",
            type_="organization",
        )
        text = (tmp_path / "entities" / "anthropic.md").read_text(encoding="utf-8")
        assert 'type: "Organization"' in text  # capitalized
        assert 'description: "AI lab behind Claude."' in text
        assert "brief:" not in text  # renamed, not duplicated

    def test_update_entity_capitalizes_type_and_writes_description(self, tmp_path):
        _write_entity(
            tmp_path,
            "anthropic",
            "# A\n\nv1.",
            "summaries/a.md",
            is_update=False,
            brief="b1",
            type_="organization",
        )
        _write_entity(
            tmp_path,
            "anthropic",
            "# A\n\nv2.",
            "summaries/b.md",
            is_update=True,
            brief="b2",
            type_="organization",
        )
        text = (tmp_path / "entities" / "anthropic.md").read_text(encoding="utf-8")
        assert 'type: "Organization"' in text
        assert 'description: "b2"' in text
        assert "brief:" not in text

    def test_update_entity_strips_legacy_brief(self, tmp_path):
        entities = tmp_path / "entities"
        entities.mkdir(parents=True)
        (entities / "anthropic.md").write_text(
            '---\nsources: ["summaries/a.md"]\ntype: organization\n'
            "brief: Old brief.\n---\n\n# Anthropic\n\nOld.",
            encoding="utf-8",
        )
        _write_entity(
            tmp_path,
            "anthropic",
            "# Anthropic\n\nv2.",
            "summaries/b.md",
            is_update=True,
            brief="New desc.",
            type_="organization",
        )
        text = (entities / "anthropic.md").read_text(encoding="utf-8")
        assert "brief:" not in text
        assert "Old brief." not in text
        assert 'description: "New desc."' in text

    def test_entity_type_multiword_title_cased(self, tmp_path):
        _write_entity(
            tmp_path,
            "acme",
            "# Acme\n\nx.",
            "summaries/a.md",
            is_update=False,
            brief="b",
            type_="real estate",
        )
        text = (tmp_path / "entities" / "acme.md").read_text(encoding="utf-8")
        assert 'type: "Real Estate"' in text


def test_update_keeps_single_blank_line_after_frontmatter(tmp_path):
    """Regression: the update path must not accumulate a 3rd newline after ---."""
    wiki = tmp_path / "wiki"
    (wiki / "concepts").mkdir(parents=True)
    (wiki / "concepts" / "x.md").write_text(
        '---\ntype: "Concept"\nsources: ["a"]\ndescription: "old"\n---\n\n# X\n', encoding="utf-8"
    )
    _write_concept(wiki, "x", "# X\n\nNew.", "summaries/b.md", True, brief="new")
    ctext = (wiki / "concepts" / "x.md").read_text(encoding="utf-8")
    assert "---\n\n\n" not in ctext and "---\n\n" in ctext

    (wiki / "entities").mkdir(parents=True)
    (wiki / "entities" / "e.md").write_text(
        '---\nsources: ["a"]\ntype: "Person"\ndescription: "old"\n---\n\n# E\n', encoding="utf-8"
    )
    _write_entity(wiki, "e", "# E\n\nNew.", "summaries/b.md", True, brief="new", type_="person")
    etext = (wiki / "entities" / "e.md").read_text(encoding="utf-8")
    assert "---\n\n\n" not in etext and "---\n\n" in etext


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

        summary_response = json.dumps(
            {
                "description": "Discusses transformers",
                "content": "# Summary\n\nThis document discusses transformers.",
            }
        )
        concepts_list_response = json.dumps(
            {
                "create": [{"name": "transformer", "title": "Transformer"}],
                "update": [],
                "related": [],
            }
        )
        # The rewrite step (third sync call) returns raw Markdown.
        summary_rewrite_response = "# Summary\n\nThis document discusses [[concepts/transformer]]."
        concept_page_response = json.dumps(
            {
                "brief": "NN architecture using self-attention",
                "content": "# Transformer\n\nA neural network architecture.",
            }
        )

        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.completion = MagicMock(
                side_effect=_mock_completion(
                    [
                        summary_response,
                        concepts_list_response,
                        summary_rewrite_response,
                    ]
                )
            )
            mock_litellm.acompletion = AsyncMock(
                side_effect=_mock_acompletion([concept_page_response])
            )
            await compile_short_doc("test-doc", source_path, tmp_path, "gpt-4o-mini")

        # Verify summary written
        summary_path = wiki / "summaries" / "test-doc.md"
        assert summary_path.exists()
        summary_text = summary_path.read_text()
        assert 'full_text: "sources/test-doc.md"' in summary_text
        assert 'type: "Summary"' in summary_text
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
        summary_response = json.dumps(
            {
                "brief": "B",
                "content": v1_summary_content,
            }
        )
        plan_response = json.dumps(
            {
                "create": [{"name": "transformer", "title": "Transformer"}],
                "update": [],
                "related": [],
            }
        )
        # Rewrite returns an empty string → must fall back to v1
        rewrite_response = ""
        concept_response = json.dumps({"brief": "C", "content": "# T\n\nBody."})

        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.completion = MagicMock(
                side_effect=_mock_completion(
                    [
                        summary_response,
                        plan_response,
                        rewrite_response,
                    ]
                )
            )
            mock_litellm.acompletion = AsyncMock(side_effect=_mock_acompletion([concept_response]))
            await compile_short_doc("doc", source_path, tmp_path, "gpt-4o-mini")

        summary_path = wiki / "summaries" / "doc.md"
        assert summary_path.exists()
        text = summary_path.read_text()
        # The v1 content should be on disk (fallback) — stripped of ghosts.
        assert "Discusses" in text
        assert "[[concepts/transformer]]" in text  # valid link kept
        assert "[[concepts/ghost]]" not in text  # ghost stripped
        assert "ghost" in text  # but plain text remains

    @pytest.mark.asyncio
    async def test_rewrite_exception_falls_back_to_v1(self, tmp_path):
        wiki, source_path = self._setup_kb(tmp_path)

        v1_summary_content = "# Summary\n\nUses [[concepts/transformer]] mechanism."
        summary_response = json.dumps(
            {
                "brief": "B",
                "content": v1_summary_content,
            }
        )
        plan_response = json.dumps(
            {
                "create": [{"name": "transformer", "title": "Transformer"}],
                "update": [],
                "related": [],
            }
        )
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
                summary_response,
                plan_response,
            ][idx]
            mock_resp.usage = MagicMock(prompt_tokens=1, completion_tokens=1)
            mock_resp.usage.prompt_tokens_details = None
            return mock_resp

        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.completion = MagicMock(side_effect=sync_side_effect)
            mock_litellm.acompletion = AsyncMock(side_effect=_mock_acompletion([concept_response]))
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

        v1_summary_content = "# Summary\n\nReferences [[concepts/nonexistent]] heavily."
        summary_response = json.dumps(
            {
                "brief": "B",
                "content": v1_summary_content,
            }
        )
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

        v1_summary_content = "# Summary\n\nMentions [[concepts/imaginary]] briefly."
        summary_response = json.dumps(
            {
                "brief": "B",
                "content": v1_summary_content,
            }
        )
        empty_plan_response = json.dumps(
            {
                "create": [],
                "update": [],
                "related": [],
            }
        )

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

    @pytest.mark.asyncio
    async def test_scalar_plan_handled_gracefully(self, tmp_path):
        """#10: a JSON scalar plan (valid JSON, not object/array) must not
        crash with AttributeError; it takes the graceful empty-plan path —
        v1 summary written, index updated, no concept/entity pages."""
        wiki, source_path = self._setup_kb(tmp_path)

        summary_response = json.dumps(
            {
                "brief": "B",
                "content": "# Summary\n\nPlain body, no links.",
            }
        )
        # Plan call returns a bare JSON scalar (an integer).
        scalar_plan_response = "42"

        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.completion = MagicMock(
                side_effect=_mock_completion([summary_response, scalar_plan_response])
            )
            # Must not raise (AttributeError) and must complete.
            await compile_short_doc("doc", source_path, tmp_path, "gpt-4o-mini")

        # Summary still written, index updated with the document.
        assert (wiki / "summaries" / "doc.md").exists()
        index_text = (wiki / "index.md").read_text()
        assert "[[summaries/doc]]" in index_text
        # No concept pages produced from the unusable plan.
        assert not list((wiki / "concepts").glob("*.md"))


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
            "# Index\n\n## Documents\n\n## Concepts\n",
            encoding="utf-8",
        )
        src = wiki / "sources" / "doc.md"
        src.write_text("Body text about caching.", encoding="utf-8")
        (tmp_path / ".openkb").mkdir()

        summary_response = json.dumps({"brief": "B", "content": "summary body"})
        plan_response = json.dumps(
            {
                "create": [{"name": "topic", "title": "Topic"}],
                "update": [],
                "related": [],
            }
        )
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
            "known_targets message in summary-rewrite call must carry a cache_control marker"
        )

    @pytest.mark.asyncio
    async def test_long_doc_marks_doc_message(self, tmp_path):
        wiki = tmp_path / "wiki"
        (wiki / "summaries").mkdir(parents=True)
        (wiki / "concepts").mkdir(parents=True)
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n\n## Concepts\n",
            encoding="utf-8",
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
                "big",
                sp,
                "doc-id-1",
                tmp_path,
                "anthropic/claude-sonnet-4-5",
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
        concepts_list_response = json.dumps(
            {
                "create": [{"name": "deep-learning", "title": "Deep Learning"}],
                "update": [],
                "related": [],
            }
        )
        concept_page_response = json.dumps(
            {
                "brief": "Subfield of ML using neural networks",
                "content": "# Deep Learning\n\nA subfield of ML.",
            }
        )

        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.completion = MagicMock(
                side_effect=_mock_completion([overview_response, concepts_list_response])
            )
            mock_litellm.acompletion = AsyncMock(
                side_effect=_mock_acompletion([concept_page_response])
            )
            await compile_long_doc("big-doc", summary_path, "doc-123", tmp_path, "gpt-4o-mini")

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
                    content,
                    encoding="utf-8",
                )

        return wiki

    @pytest.mark.asyncio
    async def test_create_and_update_flow(self, tmp_path):
        """Pre-existing 'attention' concept; plan creates 'flash-attention' and updates 'attention'."""
        wiki = self._setup_wiki(
            tmp_path,
            existing_concepts={
                "attention": "---\nsources: [old-paper.pdf]\n---\n\n# Attention\n\nOriginal content about attention.",
            },
        )

        plan_response = json.dumps(
            {
                "create": [{"name": "flash-attention", "title": "Flash Attention"}],
                "update": [{"name": "attention", "title": "Attention"}],
                "related": [],
            }
        )
        create_page_response = json.dumps(
            {
                "brief": "Efficient attention algorithm",
                "content": "# Flash Attention\n\nAn efficient attention algorithm.",
            }
        )
        update_page_response = json.dumps(
            {
                "brief": "Updated attention mechanism",
                "content": "# Attention\n\nUpdated content with new info.",
            }
        )

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
            mock_litellm.completion = MagicMock(side_effect=_mock_completion([plan_response]))
            mock_litellm.acompletion = AsyncMock(side_effect=ordered_acompletion)
            await _compile_concepts(
                wiki,
                tmp_path,
                "gpt-4o-mini",
                system_msg,
                doc_msg,
                summary,
                "test-doc",
                5,
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
    async def test_empty_content_skips_page_no_json_body(self, tmp_path):
        """#9: when the page LLM returns parseable JSON with empty content
        ({"content": ""}), the page is skipped (not written as raw JSON)."""
        wiki = self._setup_wiki(tmp_path)

        plan_response = json.dumps(
            {
                "create": [{"name": "ghost-concept", "title": "Ghost Concept"}],
                "update": [],
                "related": [],
            }
        )
        # Parseable JSON, but empty content — old code fell back to raw JSON.
        empty_content_response = json.dumps({"brief": "B", "content": ""})

        system_msg = {"role": "system", "content": "You are a wiki agent."}
        doc_msg = {"role": "user", "content": "Document content."}

        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.completion = MagicMock(side_effect=_mock_completion([plan_response]))
            mock_litellm.acompletion = AsyncMock(
                side_effect=_mock_completion([empty_content_response])
            )
            await _compile_concepts(
                wiki,
                tmp_path,
                "gpt-4o-mini",
                system_msg,
                doc_msg,
                "Summary.",
                "test-doc",
                5,
            )

        # The concept page must NOT be written (generation raised + dropped).
        page = wiki / "concepts" / "ghost-concept.md"
        assert not page.exists()
        # And no concept index entry either.
        index_text = (wiki / "index.md").read_text()
        assert "[[concepts/ghost-concept]]" not in index_text
        # Definitely no raw JSON written anywhere as a body.
        assert not any('"content":' in p.read_text() for p in (wiki / "concepts").glob("*.md"))

    @pytest.mark.asyncio
    async def test_related_adds_link_no_llm(self, tmp_path):
        """Plan has only related items. No acompletion calls should be made."""
        wiki = self._setup_wiki(
            tmp_path,
            existing_concepts={
                "transformer": "---\nsources: [old.pdf]\n---\n\n# Transformer\n\nContent about transformers.",
            },
        )

        plan_response = json.dumps(
            {
                "create": [],
                "update": [],
                "related": ["transformer"],
            }
        )

        system_msg = {"role": "system", "content": "You are a wiki agent."}
        doc_msg = {"role": "user", "content": "Document content."}
        summary = "Summary."

        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.completion = MagicMock(side_effect=_mock_completion([plan_response]))
            mock_litellm.acompletion = AsyncMock()
            await _compile_concepts(
                wiki,
                tmp_path,
                "gpt-4o-mini",
                system_msg,
                doc_msg,
                summary,
                "test-doc",
                5,
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

        plan_response = json.dumps(
            [
                {"name": "attention", "title": "Attention"},
            ]
        )
        concept_page_response = json.dumps(
            {
                "brief": "A mechanism for focusing",
                "content": "# Attention\n\nA mechanism for focusing.",
            }
        )

        system_msg = {"role": "system", "content": "You are a wiki agent."}
        doc_msg = {"role": "user", "content": "Document content."}
        summary = "Summary."

        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.completion = MagicMock(side_effect=_mock_completion([plan_response]))
            mock_litellm.acompletion = AsyncMock(
                side_effect=_mock_acompletion([concept_page_response])
            )
            await _compile_concepts(
                wiki,
                tmp_path,
                "gpt-4o-mini",
                system_msg,
                doc_msg,
                summary,
                "test-doc",
                5,
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

        summary_resp = json.dumps(
            {
                "description": "A paper about transformers",
                "content": "# Summary\n\nThis paper discusses transformers.",
            }
        )
        plan_resp = json.dumps(
            {
                "create": [{"name": "transformer", "title": "Transformer"}],
                "update": [],
                "related": [],
            }
        )
        concept_resp = json.dumps(
            {
                "description": "NN architecture using self-attention",
                "content": "# Transformer\n\nA neural network architecture.",
            }
        )

        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.completion = MagicMock(
                side_effect=_mock_completion([summary_resp, plan_resp])
            )
            mock_litellm.acompletion = AsyncMock(side_effect=_mock_acompletion([concept_resp]))
            await compile_short_doc("test-doc", source_path, tmp_path, "gpt-4o-mini")

        # Summary frontmatter has doc_type and full_text
        summary_text = (wiki / "summaries" / "test-doc.md").read_text()
        assert "doc_type: short" in summary_text
        assert 'full_text: "sources/test-doc.md"' in summary_text

        # Concept frontmatter has type and description
        concept_text = (wiki / "concepts" / "transformer.md").read_text()
        assert 'description: "NN architecture using self-attention"' in concept_text

        # Index has briefs
        index_text = (wiki / "index.md").read_text()
        assert "— A paper about transformers" in index_text
        assert "— NN architecture using self-attention" in index_text


class TestIndexEntities:
    def test_entities_section_written(self, tmp_path):
        _update_index(
            tmp_path,
            "doc",
            [],
            doc_brief="d",
            entity_names=["anthropic"],
            entity_meta={"anthropic": ("organization", "AI lab behind Claude.")},
        )
        text = (tmp_path / "index.md").read_text(encoding="utf-8")
        assert "## Entities" in text
        assert "- [[entities/anthropic]] (organization) — AI lab behind Claude." in text

    def test_entity_entry_replaced_on_update(self, tmp_path):
        _update_index(
            tmp_path,
            "doc",
            [],
            entity_names=["anthropic"],
            entity_meta={"anthropic": ("organization", "old")},
        )
        _update_index(
            tmp_path,
            "doc2",
            [],
            entity_names=["anthropic"],
            entity_meta={"anthropic": ("organization", "new")},
        )
        text = (tmp_path / "index.md").read_text(encoding="utf-8")
        assert text.count("[[entities/anthropic]]") == 1
        assert "new" in text and "old" not in text


class TestEntityBacklinks:
    def _seed(self, tmp_path):
        (tmp_path / "summaries").mkdir()
        (tmp_path / "summaries" / "doc.md").write_text(
            "---\nsources: []\n---\n\n# Doc\n", encoding="utf-8"
        )
        (tmp_path / "entities").mkdir()
        (tmp_path / "entities" / "anthropic.md").write_text(
            "---\ntype: organization\nsources: [summaries/doc.md]\n---\n\n# Anthropic\n",
            encoding="utf-8",
        )

    def test_summary_gets_entities_section(self, tmp_path):
        self._seed(tmp_path)
        _backlink_summary_entities(tmp_path, "doc", ["anthropic"])
        text = (tmp_path / "summaries" / "doc.md").read_text(encoding="utf-8")
        assert "## Entities" in text
        assert "[[entities/anthropic]]" in text

    def test_entity_gets_related_documents(self, tmp_path):
        self._seed(tmp_path)
        _backlink_entities(tmp_path, "doc", ["anthropic"])
        text = (tmp_path / "entities" / "anthropic.md").read_text(encoding="utf-8")
        assert "## Related Documents" in text
        assert "[[summaries/doc]]" in text

    def test_idempotent(self, tmp_path):
        self._seed(tmp_path)
        _backlink_summary_entities(tmp_path, "doc", ["anthropic"])
        _backlink_summary_entities(tmp_path, "doc", ["anthropic"])
        text = (tmp_path / "summaries" / "doc.md").read_text(encoding="utf-8")
        assert text.count("[[entities/anthropic]]") == 1


class TestRemoveEntityPages:
    def test_strip_source_and_delete_when_empty(self, tmp_path):
        ent = tmp_path / "entities"
        ent.mkdir()
        (ent / "solo.md").write_text(
            "---\ntype: organization\nsources: [summaries/doc.md]\n---\n\n"
            "# Solo\n\n## Related Documents\n- [[summaries/doc]]\n",
            encoding="utf-8",
        )
        (ent / "shared.md").write_text(
            "---\ntype: organization\nsources: [summaries/doc.md, summaries/other.md]\n---\n\n"
            "# Shared\n\n## Related Documents\n- [[summaries/doc]]\n- [[summaries/other]]\n",
            encoding="utf-8",
        )
        result = remove_doc_from_entity_pages(tmp_path, "doc")
        assert result == {"modified": ["shared"], "deleted": ["solo"]}
        assert not (ent / "solo.md").exists()
        shared = (ent / "shared.md").read_text(encoding="utf-8")
        assert "summaries/doc" not in shared
        assert "summaries/other" in shared

    def test_strips_standalone_see_also_line(self, tmp_path):
        # A related entity (linked via _add_related_link) carries a
        # standalone "See also:" paragraph, not a "## Related Documents"
        # section. Removing the doc must strip it so no dangling wikilink
        # survives on an entity that has other sources.
        ent = tmp_path / "entities"
        ent.mkdir()
        (ent / "shared.md").write_text(
            "---\ntype: organization\nsources: [summaries/doc.md, summaries/other.md]\n---\n\n"
            "# Shared\n\nSee also: [[summaries/doc]]",
            encoding="utf-8",
        )
        result = remove_doc_from_entity_pages(tmp_path, "doc")
        assert result == {"modified": ["shared"], "deleted": []}
        shared = (ent / "shared.md").read_text(encoding="utf-8")
        assert "summaries/doc" not in shared
        assert "See also" not in shared
        assert "summaries/other" in shared


class TestCompileEntitiesEndToEnd:
    @pytest.mark.asyncio
    async def test_entity_and_concept_split(self, tmp_path, monkeypatch):
        wiki = tmp_path / "wiki"
        (wiki / "summaries").mkdir(parents=True)
        (wiki / "summaries" / "doc.md").write_text(
            "---\nsources: []\n---\n\n# Doc\n", encoding="utf-8"
        )

        # Mocked LLM: plan call returns one concept + one entity; each
        # generation call returns a tiny page.
        def fake_llm(model, messages, label, **kw):
            if label == "concepts-plan":
                return json.dumps(
                    {
                        "concepts": {
                            "create": [{"name": "ai-demand", "title": "AI Demand"}],
                            "update": [],
                            "related": [],
                        },
                        "entities": {
                            "create": [
                                {"name": "nvidia", "title": "NVIDIA", "type": "organization"}
                            ],
                            "update": [],
                            "related": [],
                        },
                    }
                )
            return json.dumps({"description": "b", "type": "organization", "content": "# Page\n"})

        async def fake_llm_async(model, messages, label, **kw):
            return fake_llm(model, messages, label, **kw)

        monkeypatch.setattr("openkb.agent.compiler._llm_call", fake_llm)
        monkeypatch.setattr("openkb.agent.compiler._llm_call_async", fake_llm_async)

        from openkb.agent.compiler import _compile_concepts

        sys_msg = {"role": "system", "content": "x"}
        doc_msg = {"role": "user", "content": "x"}
        await _compile_concepts(
            wiki,
            tmp_path,
            "m",
            sys_msg,
            doc_msg,
            "summary text",
            "doc",
            max_concurrency=2,
            doc_type="short",
            rewrite_summary=False,
        )

        assert (wiki / "concepts" / "ai-demand.md").exists()
        assert (wiki / "entities" / "nvidia.md").exists()
        ent = (wiki / "entities" / "nvidia.md").read_text(encoding="utf-8")
        assert 'type: "Organization"' in ent
        index = (wiki / "index.md").read_text(encoding="utf-8")
        assert "[[entities/nvidia]]" in index
        summary = (wiki / "summaries" / "doc.md").read_text(encoding="utf-8")
        assert "[[entities/nvidia]]" in summary  # backlink

    @pytest.mark.asyncio
    async def test_related_entity_does_not_downgrade_index_label(self, tmp_path, monkeypatch):
        """Related-only entities must not overwrite a correct index entry with (other)."""
        wiki = tmp_path / "wiki"
        (wiki / "summaries").mkdir(parents=True)
        (wiki / "entities").mkdir(parents=True)

        # Pre-seed summaries/doc.md
        (wiki / "summaries" / "doc.md").write_text(
            "---\nsources: []\n---\n\n# Doc\n", encoding="utf-8"
        )

        # Pre-seed index.md with a correct entry for anthropic
        (wiki / "index.md").write_text(
            "## Documents\n\n## Concepts\n\n## Entities\n\n"
            "- [[entities/anthropic]] (organization) — AI safety lab\n",
            encoding="utf-8",
        )

        # Pre-seed entities/anthropic.md with type frontmatter and a source
        (wiki / "entities" / "anthropic.md").write_text(
            "---\ntype: organization\nsources: []\n---\n\n# Anthropic\n",
            encoding="utf-8",
        )

        # LLM plan: anthropic is ONLY under entities.related, not create/update
        def fake_llm(model, messages, label, **kw):
            if label == "concepts-plan":
                return json.dumps(
                    {
                        "concepts": {"create": [], "update": [], "related": []},
                        "entities": {"create": [], "update": [], "related": ["anthropic"]},
                    }
                )
            return json.dumps({"brief": "b", "type": "organization", "content": "# Page\n"})

        async def fake_llm_async(model, messages, label, **kw):
            return fake_llm(model, messages, label, **kw)

        monkeypatch.setattr("openkb.agent.compiler._llm_call", fake_llm)
        monkeypatch.setattr("openkb.agent.compiler._llm_call_async", fake_llm_async)

        from openkb.agent.compiler import _compile_concepts

        sys_msg = {"role": "system", "content": "x"}
        doc_msg = {"role": "user", "content": "x"}
        await _compile_concepts(
            wiki,
            tmp_path,
            "m",
            sys_msg,
            doc_msg,
            "summary text",
            "doc",
            max_concurrency=2,
            doc_type="short",
            rewrite_summary=False,
        )

        index = (wiki / "index.md").read_text(encoding="utf-8")
        # The pre-existing correct line must NOT have been downgraded to (other)
        assert "(organization)" in index, (
            "index entry was downgraded from (organization) to (other)"
        )
        assert "AI safety lab" in index, "index brief was stripped from the entry"

    @pytest.mark.asyncio
    async def test_related_to_nonexistent_concept_does_not_create_dangling_links(
        self, tmp_path, monkeypatch
    ):
        """A plan 'related' slug whose page does NOT exist must be dropped, not
        whitelisted+back-linked — otherwise every page gets a dangling
        [[concepts/<ghost>]] link to a page that is never created."""
        wiki = tmp_path / "wiki"
        (wiki / "summaries").mkdir(parents=True)
        (wiki / "summaries" / "doc.md").write_text(
            "---\nsources: []\n---\n\n# Doc\n", encoding="utf-8"
        )

        def fake_llm(model, messages, label, **kw):
            if label == "concepts-plan":
                return json.dumps(
                    {
                        "concepts": {
                            "create": [{"name": "real-concept", "title": "Real"}],
                            "update": [],
                            "related": ["ghost-concept"],
                        },
                        "entities": {"create": [], "update": [], "related": []},
                    }
                )
            if label == "summary-rewrite":
                return "# Doc\n\nSee [[concepts/real-concept]] and [[concepts/ghost-concept]].\n"
            # concept generation body references the non-existent ghost concept
            return json.dumps(
                {"brief": "b", "content": "# Real\n\nLinks [[concepts/ghost-concept]].\n"}
            )

        async def fake_llm_async(model, messages, label, **kw):
            return fake_llm(model, messages, label, **kw)

        monkeypatch.setattr("openkb.agent.compiler._llm_call", fake_llm)
        monkeypatch.setattr("openkb.agent.compiler._llm_call_async", fake_llm_async)

        from openkb.agent.compiler import _compile_concepts

        await _compile_concepts(
            wiki,
            tmp_path,
            "m",
            {"role": "system", "content": "x"},
            {"role": "user", "content": "x"},
            "summary text",
            "doc",
            max_concurrency=2,
            doc_type="short",
            rewrite_summary=True,
        )

        # ghost-concept never existed and was only "related" → never created
        assert not (wiki / "concepts" / "ghost-concept.md").exists()
        # ...and no page should link to it (stripped as a ghost, since not whitelisted)
        real = (wiki / "concepts" / "real-concept.md").read_text(encoding="utf-8")
        assert "[[concepts/ghost-concept]]" not in real
        summary = (wiki / "summaries" / "doc.md").read_text(encoding="utf-8")
        assert "[[concepts/ghost-concept]]" not in summary
        # the genuinely-created concept must still be linked
        assert "[[concepts/real-concept]]" in summary

    @pytest.mark.asyncio
    async def test_custom_entity_type_is_not_coerced(self, tmp_path, monkeypatch):
        """With a config-driven entity_types that includes 'dataset', a plan
        entity typed 'dataset' is written as 'dataset' (not coerced to other),
        and the plan prompt the mock receives advertises the custom type."""
        wiki = tmp_path / "wiki"
        (wiki / "summaries").mkdir(parents=True)
        (wiki / "summaries" / "doc.md").write_text(
            "---\nsources: []\n---\n\n# Doc\n", encoding="utf-8"
        )

        seen_messages: list = []

        def fake_llm(model, messages, label, **kw):
            seen_messages.append((label, messages))
            if label == "concepts-plan":
                return json.dumps(
                    {
                        "concepts": {"create": [], "update": [], "related": []},
                        "entities": {
                            "create": [
                                {"name": "imagenet", "title": "ImageNet", "type": "dataset"}
                            ],
                            "update": [],
                            "related": [],
                        },
                    }
                )
            return json.dumps({"description": "b", "type": "dataset", "content": "# Page\n"})

        async def fake_llm_async(model, messages, label, **kw):
            seen_messages.append((label, messages))
            return fake_llm(model, messages, label, **kw)

        monkeypatch.setattr("openkb.agent.compiler._llm_call", fake_llm)
        monkeypatch.setattr("openkb.agent.compiler._llm_call_async", fake_llm_async)

        from openkb.agent.compiler import _compile_concepts

        sys_msg = {"role": "system", "content": "x"}
        doc_msg = {"role": "user", "content": "x"}
        await _compile_concepts(
            wiki,
            tmp_path,
            "m",
            sys_msg,
            doc_msg,
            "summary text",
            "doc",
            max_concurrency=2,
            doc_type="short",
            rewrite_summary=False,
            entity_types=["person", "organization", "dataset", "other"],
        )

        ent = (wiki / "entities" / "imagenet.md").read_text(encoding="utf-8")
        assert 'type: "Dataset"' in ent
        # The custom type must reach the plan prompt the mock saw.
        plan_msgs = [m for (label, m) in seen_messages if label == "concepts-plan"]
        assert plan_msgs, "plan call was not made"
        plan_user = plan_msgs[0][-1]["content"]
        assert "dataset" in plan_user
        assert "__ENTITY_TYPES__" not in plan_user  # token was substituted

    @pytest.mark.asyncio
    async def test_brace_in_entity_type_does_not_crash_format(self, tmp_path, monkeypatch):
        """Defense-in-depth: even if a '{'/'}' reaches types_str (bypassing
        resolve_entity_types sanitization), the prompt build must not raise —
        the token is substituted AFTER .format(), so braces are inert."""
        wiki = tmp_path / "wiki"
        (wiki / "summaries").mkdir(parents=True)
        (wiki / "summaries" / "doc.md").write_text(
            "---\nsources: []\n---\n\n# Doc\n", encoding="utf-8"
        )

        def fake_llm(model, messages, label, **kw):
            return json.dumps(
                {
                    "concepts": {"create": [], "update": [], "related": []},
                    "entities": {"create": [], "update": [], "related": []},
                }
            )

        async def fake_llm_async(model, messages, label, **kw):
            return fake_llm(model, messages, label, **kw)

        monkeypatch.setattr("openkb.agent.compiler._llm_call", fake_llm)
        monkeypatch.setattr("openkb.agent.compiler._llm_call_async", fake_llm_async)

        from openkb.agent.compiler import _compile_concepts

        # entity_types deliberately contains brace chars to exercise the
        # format/replace ordering — this must NOT raise KeyError/ValueError.
        await _compile_concepts(
            wiki,
            tmp_path,
            "m",
            {"role": "system", "content": "x"},
            {"role": "user", "content": "x"},
            "summary text",
            "doc",
            max_concurrency=2,
            doc_type="short",
            rewrite_summary=False,
            entity_types=["wei{rd}", "other"],
        )  # reaching here without an exception is the assertion

    @pytest.mark.asyncio
    async def test_default_path_plan_prompt_has_default_types(self, tmp_path, monkeypatch):
        """When entity_types is omitted, the plan prompt still advertises the
        default enum at call time (byte-identical to today)."""
        wiki = tmp_path / "wiki"
        (wiki / "summaries").mkdir(parents=True)
        (wiki / "summaries" / "doc.md").write_text(
            "---\nsources: []\n---\n\n# Doc\n", encoding="utf-8"
        )

        seen_messages: list = []

        def fake_llm(model, messages, label, **kw):
            seen_messages.append((label, messages))
            return json.dumps(
                {
                    "concepts": {"create": [], "update": [], "related": []},
                    "entities": {"create": [], "update": [], "related": []},
                }
            )

        async def fake_llm_async(model, messages, label, **kw):
            seen_messages.append((label, messages))
            return fake_llm(model, messages, label, **kw)

        monkeypatch.setattr("openkb.agent.compiler._llm_call", fake_llm)
        monkeypatch.setattr("openkb.agent.compiler._llm_call_async", fake_llm_async)

        from openkb.agent.compiler import _compile_concepts

        await _compile_concepts(
            wiki,
            tmp_path,
            "m",
            {"role": "system", "content": "x"},
            {"role": "user", "content": "x"},
            "summary text",
            "doc",
            max_concurrency=2,
            doc_type="short",
            rewrite_summary=False,
        )

        plan_msgs = [m for (label, m) in seen_messages if label == "concepts-plan"]
        plan_user = plan_msgs[0][-1]["content"]
        for t in _ENTITY_TYPE_LIST:
            assert t in plan_user
        assert "__ENTITY_TYPES__" not in plan_user


# ---------------------------------------------------------------------------
# Task 9: schema declares entities
# ---------------------------------------------------------------------------


def test_schema_declares_entities():
    assert "entities/" in AGENTS_MD
    assert "Entity Page" in AGENTS_MD
    for t in ("person", "organization", "place", "product", "work", "event", "other"):
        assert t in AGENTS_MD


def test_ensure_h2_section_quiet_suppresses_drift_warning(caplog):
    """Backlink helpers create sections as a normal operation, so quiet=True
    must not emit the 'hand-edited' drift warning; default still warns."""
    import logging

    from openkb.agent.compiler import _ensure_h2_section

    with caplog.at_level(logging.WARNING, logger="openkb.agent.compiler"):
        lines = ["# Doc", ""]
        _ensure_h2_section(lines, "## Entities", quiet=True)
        assert "## Entities" in lines
        assert caplog.records == []

        _ensure_h2_section(["# Doc", ""], "## Entities")  # default warns
        assert any("missing" in r.getMessage() for r in caplog.records)


def test_known_targets_prompt_has_entities_rule():
    """The whitelist message must tell the LLM the [[entities/X]] rule, since
    entity-page prompts instruct writing such links; otherwise entity links
    are generated freely and then stripped as ghosts."""
    from openkb.agent.compiler import _KNOWN_TARGETS_USER

    assert "[[entities/" in _KNOWN_TARGETS_USER


def test_plan_prompt_keeps_topic_itself_guard():
    """The concept-plan prompt must retain the guard against creating a concept
    that merely mirrors the document's own topic."""
    from openkb.agent.compiler import _CONCEPTS_PLAN_USER

    assert "just the document topic itself" in _CONCEPTS_PLAN_USER


class TestLLMCallExtraHeaders:
    """Config-driven extra headers reach the litellm calls (issue #93)."""

    def test_llm_call_injects_extra_headers(self):
        from openkb.agent.compiler import _llm_call
        from openkb.config import set_extra_headers

        set_extra_headers({"Editor-Version": "vscode/1.95.0"})
        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.completion = MagicMock(side_effect=_mock_completion(["ok"]))
            out = _llm_call("m", [{"role": "user", "content": "hi"}], "step")
        assert out == "ok"
        kwargs = mock_litellm.completion.call_args.kwargs
        assert kwargs["extra_headers"] == {"Editor-Version": "vscode/1.95.0"}

    def test_llm_call_no_extra_headers_by_default(self):
        from openkb.agent.compiler import _llm_call

        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.completion = MagicMock(side_effect=_mock_completion(["ok"]))
            _llm_call("m", [{"role": "user", "content": "hi"}], "step")
        assert "extra_headers" not in mock_litellm.completion.call_args.kwargs

    def test_llm_call_explicit_kwarg_wins_over_config(self):
        from openkb.agent.compiler import _llm_call
        from openkb.config import set_extra_headers

        set_extra_headers({"Editor-Version": "from-config"})
        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.completion = MagicMock(side_effect=_mock_completion(["ok"]))
            _llm_call(
                "m",
                [{"role": "user", "content": "hi"}],
                "step",
                extra_headers={"Editor-Version": "explicit"},
            )
        kwargs = mock_litellm.completion.call_args.kwargs
        assert kwargs["extra_headers"] == {"Editor-Version": "explicit"}

    @pytest.mark.asyncio
    async def test_llm_call_async_injects_extra_headers(self):
        from openkb.agent.compiler import _llm_call_async
        from openkb.config import set_extra_headers

        set_extra_headers({"Copilot-Integration-Id": "vscode-chat"})
        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.acompletion = AsyncMock(side_effect=_mock_acompletion(["ok"]))
            out = await _llm_call_async("m", [{"role": "user", "content": "hi"}], "step")
        assert out == "ok"
        kwargs = mock_litellm.acompletion.call_args.kwargs
        assert kwargs["extra_headers"] == {"Copilot-Integration-Id": "vscode-chat"}


class TestCacheControlStripping:
    """cache_control markers must only reach providers that honour them.

    ``_cached_text`` tags payloads with an Anthropic ``cache_control`` marker.
    LiteLLM turns that marker into a hard 400 for Gemini ("CachedContent can not
    be used with system_instruction/tools") and silently wastes it on other
    non-Anthropic providers, so ``_llm_call``/``_llm_call_async`` strip it for
    every non-Anthropic model. Regression for the all-Gemini-compiles-fail bug.
    """

    def test_accepts_for_anthropic_providers(self):
        from openkb.agent.compiler import _accepts_cache_control

        assert _accepts_cache_control("anthropic/claude-sonnet-4-6")
        assert _accepts_cache_control("claude-opus-4-6")
        # Claude served via OpenRouter still honours the marker.
        assert _accepts_cache_control("openrouter/anthropic/claude-3.5-sonnet")

    def test_rejects_for_non_anthropic_providers(self):
        from openkb.agent.compiler import _accepts_cache_control

        assert not _accepts_cache_control("gemini/gemini-2.5-pro")
        assert not _accepts_cache_control("gpt-4o")

    def test_strip_removes_marker_without_mutating_input(self):
        from openkb.agent.compiler import _cached_text, _strip_cache_control

        messages = [
            {"role": "system", "content": "plain string stays"},
            {"role": "user", "content": _cached_text("doc")},
        ]
        cleaned = _strip_cache_control(messages)
        # Plain-string content passes through untouched.
        assert cleaned[0]["content"] == "plain string stays"
        # Marker gone, text preserved.
        assert cleaned[1]["content"] == [{"type": "text", "text": "doc"}]
        # Original input is not mutated.
        assert "cache_control" in messages[1]["content"][0]

    def test_llm_call_strips_marker_for_gemini(self):
        from openkb.agent.compiler import _cached_text, _llm_call

        with patch(
            "openkb.agent.compiler.litellm.completion",
            MagicMock(side_effect=_mock_completion(["ok"])),
        ) as mock_completion:
            _llm_call(
                "gemini/gemini-2.5-pro", [{"role": "user", "content": _cached_text("doc")}], "step"
            )
        sent = mock_completion.call_args.kwargs["messages"]
        block = sent[0]["content"][0]
        assert "cache_control" not in block
        assert block["text"] == "doc"

    def test_llm_call_keeps_marker_for_anthropic(self):
        from openkb.agent.compiler import _cached_text, _llm_call

        with patch(
            "openkb.agent.compiler.litellm.completion",
            MagicMock(side_effect=_mock_completion(["ok"])),
        ) as mock_completion:
            _llm_call(
                "anthropic/claude-sonnet-4-6",
                [{"role": "user", "content": _cached_text("doc")}],
                "step",
            )
        sent = mock_completion.call_args.kwargs["messages"]
        assert sent[0]["content"][0]["cache_control"] == {"type": "ephemeral"}


class TestFrontmatterDashBoundary:
    """Regression: description containing '---' must not truncate frontmatter."""

    def test_concept_round_trip_with_dashes_in_brief(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        # Write concept with a brief containing '---'.
        brief = "--- note ---"
        _write_concept(
            wiki, "tricky", "# Body\n\nContent.", "summaries/doc.md", is_update=False, brief=brief
        )
        # Round-trip: _read_concept_briefs must return the brief intact.
        result = _read_concept_briefs(wiki)
        assert "--- note ---" in result
        # The body must not be corrupted.
        text = (wiki / "concepts" / "tricky.md").read_text(encoding="utf-8")
        assert "# Body" in text
        assert "Content." in text

    def test_entity_round_trip_with_dashes_in_brief(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        brief = "--- note ---"
        _write_entity(
            wiki,
            "tricky-org",
            "# Body\n\nContent.",
            "summaries/doc.md",
            is_update=False,
            brief=brief,
            type_="organization",
        )
        result = _read_entity_briefs(wiki)
        assert "--- note ---" in result
        text = (wiki / "entities" / "tricky-org.md").read_text(encoding="utf-8")
        assert "# Body" in text
        assert "Content." in text

    def test_concept_update_malformed_frontmatter_rebuilds(self, tmp_path):
        """_write_concept(is_update=True) on a file with malformed frontmatter
        must rebuild valid frontmatter, not write a bare body."""
        concepts = tmp_path / "concepts"
        concepts.mkdir(parents=True)
        # Opening '---' with no closing delimiter.
        malformed = "---\nsources: [x]\nno close\n\nbody"
        (concepts / "tricky.md").write_text(malformed, encoding="utf-8")
        _write_concept(
            tmp_path,
            "tricky",
            "# New\n\nNew body.",
            "summaries/doc.md",
            is_update=True,
            brief="brief text",
        )
        text = (concepts / "tricky.md").read_text(encoding="utf-8")
        assert text.startswith("---\n")
        assert 'type: "Concept"' in text
        # Must have a properly closed frontmatter block (two '---' occurrences).
        assert text.count("---") >= 2
