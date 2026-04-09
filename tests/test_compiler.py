"""Tests for openkb.agent.compiler pipeline."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from openkb.agent.compiler import (
    compile_long_doc,
    compile_short_doc,
    _compile_concepts,
    _parse_json,
    _write_summary,
    _write_concept,
    _update_index,
    _read_wiki_context,
    _read_concept_briefs,
    _add_related_link,
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


class TestWriteSummary:
    def test_writes_with_frontmatter(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        _write_summary(wiki, "my-doc", "my-doc.pdf", "# Summary\n\nContent here.")
        path = wiki / "summaries" / "my-doc.md"
        assert path.exists()
        text = path.read_text()
        assert "sources: [my-doc.pdf]" in text
        assert "# Summary" in text


class TestWriteConcept:
    def test_new_concept(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        _write_concept(wiki, "attention", "# Attention\n\nDetails.", "paper.pdf", False)
        path = wiki / "concepts" / "attention.md"
        assert path.exists()
        text = path.read_text()
        assert "sources: [paper.pdf]" in text
        assert "# Attention" in text

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


class TestUpdateIndex:
    def test_appends_entries(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n\n## Concepts\n\n## Explorations\n",
            encoding="utf-8",
        )
        _update_index(wiki, "my-doc", ["attention", "transformer"])
        text = (wiki / "index.md").read_text()
        assert "[[summaries/my-doc]]" in text
        assert "[[concepts/attention]]" in text
        assert "[[concepts/transformer]]" in text

    def test_no_duplicates(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n- [[summaries/my-doc]]\n\n## Concepts\n",
            encoding="utf-8",
        )
        _update_index(wiki, "my-doc", [])
        text = (wiki / "index.md").read_text()
        assert text.count("[[summaries/my-doc]]") == 1


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

        summary_response = "# Summary\n\nThis document discusses transformers."
        concepts_list_response = json.dumps({
            "create": [{"name": "transformer", "title": "Transformer"}],
            "update": [],
            "related": [],
        })
        concept_page_response = "# Transformer\n\nA neural network architecture."

        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.completion = MagicMock(
                side_effect=_mock_completion([summary_response, concepts_list_response])
            )
            mock_litellm.acompletion = AsyncMock(
                side_effect=_mock_acompletion([concept_page_response])
            )
            await compile_short_doc("test-doc", source_path, tmp_path, "gpt-4o-mini")

        # Verify summary written
        summary_path = wiki / "summaries" / "test-doc.md"
        assert summary_path.exists()
        assert "sources: [test-doc.pdf]" in summary_path.read_text()

        # Verify concept written
        concept_path = wiki / "concepts" / "transformer.md"
        assert concept_path.exists()
        assert "sources: [test-doc.pdf]" in concept_path.read_text()

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
                side_effect=_mock_completion(["Summary text", "not valid json"])
            )
            # Should not raise
            await compile_short_doc("doc", source_path, tmp_path, "gpt-4o-mini")

        # Summary should still be written
        assert (wiki / "summaries" / "doc.md").exists()


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
        concept_page_response = "# Deep Learning\n\nA subfield of ML."

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
        create_page_response = "# Flash Attention\n\nAn efficient attention algorithm."
        update_page_response = "# Attention\n\nUpdated content with new info."

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
        assert "sources: [test-doc.pdf]" in fa_text
        assert "Flash Attention" in fa_text

        # Verify attention updated (is_update=True path in _write_concept)
        att_path = wiki / "concepts" / "attention.md"
        assert att_path.exists()
        att_text = att_path.read_text()
        assert "test-doc.pdf" in att_text
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
        assert "test-doc.pdf" in transformer_text

    @pytest.mark.asyncio
    async def test_fallback_list_format(self, tmp_path):
        """LLM returns a flat array instead of dict — treated as all create."""
        wiki = self._setup_wiki(tmp_path)

        plan_response = json.dumps([
            {"name": "attention", "title": "Attention"},
        ])
        concept_page_response = "# Attention\n\nA mechanism for focusing."

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
        assert "sources: [test-doc.pdf]" in att_text
        assert "Attention" in att_text
