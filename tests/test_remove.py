"""Tests for the `openkb remove` feature.

Covers:
- compiler helpers (`_remove_source_from_frontmatter`,
  `remove_doc_from_concept_pages`, `remove_doc_from_index`)
- `HashRegistry.remove_by_doc_name`
- The `openkb remove` CLI: identifier resolution, dry-run, --yes,
  --keep-raw, --keep-empty-concepts, error paths, and the auto
  `lint --fix` post-pass.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from openkb.agent.compiler import (
    _remove_source_from_frontmatter,
    remove_doc_from_concept_pages,
    remove_doc_from_index,
)
from openkb.cli import _resolve_doc_identifier, cli
from openkb.state import HashRegistry


# ---------------------------------------------------------------------------
# _remove_source_from_frontmatter
# ---------------------------------------------------------------------------


def test_remove_source_drops_only_target_and_marks_empty():
    text = "---\nsources: [summaries/a.md]\nbrief: x\n---\n\nbody\n"
    rewritten, empty = _remove_source_from_frontmatter(text, "summaries/a.md")
    assert empty is True
    assert "sources: []" in rewritten
    assert rewritten.endswith("\nbody\n")


def test_remove_source_keeps_others():
    text = (
        "---\nsources: [summaries/a.md, summaries/b.md, summaries/c.md]\n"
        "brief: x\n---\n\nbody\n"
    )
    rewritten, empty = _remove_source_from_frontmatter(text, "summaries/b.md")
    assert empty is False
    assert "summaries/a.md" in rewritten
    assert "summaries/c.md" in rewritten
    assert "summaries/b.md" not in rewritten


def test_remove_source_noop_when_not_present():
    text = "---\nsources: [summaries/a.md]\n---\n\nbody\n"
    rewritten, empty = _remove_source_from_frontmatter(text, "summaries/z.md")
    assert rewritten == text
    assert empty is False


def test_remove_source_noop_without_frontmatter():
    text = "# No frontmatter\n\nbody only\n"
    rewritten, empty = _remove_source_from_frontmatter(text, "summaries/a.md")
    assert rewritten == text
    assert empty is False


def test_remove_source_noop_malformed_brackets():
    text = "---\nsources: summaries/a.md\n---\nbody\n"
    rewritten, empty = _remove_source_from_frontmatter(text, "summaries/a.md")
    assert rewritten == text
    assert empty is False


# ---------------------------------------------------------------------------
# remove_doc_from_concept_pages
# ---------------------------------------------------------------------------


def _write_concept(wiki_dir: Path, slug: str, sources: list[str], body: str = "") -> Path:
    src_inline = "[" + ", ".join(sources) + "]"
    related = "\n".join(
        f"- [[{s.replace('.md', '')}]]" for s in sources
    )
    text = (
        f"---\nsources: {src_inline}\nbrief: stub\n---\n\n"
        f"# {slug}\n\n{body}\n\n"
        f"## Related Documents\n{related}\n"
    )
    path = wiki_dir / "concepts" / f"{slug}.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_remove_doc_from_concept_pages_deletes_single_source(kb_dir):
    wiki = kb_dir / "wiki"
    p = _write_concept(wiki, "transformer", ["summaries/attn-x.md"])

    result = remove_doc_from_concept_pages(wiki, "attn-x")

    assert result == {"modified": [], "deleted": ["transformer"]}
    assert not p.exists()


def test_remove_doc_from_concept_pages_keeps_with_flag(kb_dir):
    wiki = kb_dir / "wiki"
    p = _write_concept(wiki, "transformer", ["summaries/attn-x.md"])

    result = remove_doc_from_concept_pages(wiki, "attn-x", keep_empty=True)

    assert result == {"modified": ["transformer"], "deleted": []}
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    assert "sources: []" in text
    assert "[[summaries/attn-x]]" not in text


def test_remove_doc_from_concept_pages_edits_multi_source(kb_dir):
    wiki = kb_dir / "wiki"
    p = _write_concept(
        wiki, "attention",
        ["summaries/attn-x.md", "summaries/survey-y.md"],
    )

    result = remove_doc_from_concept_pages(wiki, "attn-x")

    assert result == {"modified": ["attention"], "deleted": []}
    text = p.read_text(encoding="utf-8")
    assert "summaries/attn-x.md" not in text
    assert "summaries/survey-y.md" in text
    assert "[[summaries/attn-x]]" not in text
    assert "[[summaries/survey-y]]" in text


def test_remove_doc_from_concept_pages_strips_see_also(kb_dir):
    wiki = kb_dir / "wiki"
    text = (
        "---\nsources: [summaries/a.md, summaries/b.md]\n---\n"
        "# c\n\nbody\n\nSee also: [[summaries/a]]\n"
    )
    p = wiki / "concepts" / "c.md"
    p.write_text(text, encoding="utf-8")

    remove_doc_from_concept_pages(wiki, "a")

    out = p.read_text(encoding="utf-8")
    assert "See also: [[summaries/a]]" not in out


def test_remove_doc_from_concept_pages_skips_unrelated(kb_dir):
    wiki = kb_dir / "wiki"
    p = _write_concept(wiki, "other", ["summaries/unrelated-z.md"])
    before = p.read_text(encoding="utf-8")

    result = remove_doc_from_concept_pages(wiki, "attn-x")

    assert result == {"modified": [], "deleted": []}
    assert p.read_text(encoding="utf-8") == before


def test_remove_doc_from_concept_pages_missing_dir(tmp_path):
    # No concepts/ directory exists at all — should return empty result.
    result = remove_doc_from_concept_pages(tmp_path / "nope", "anything")
    assert result == {"modified": [], "deleted": []}


# ---------------------------------------------------------------------------
# remove_doc_from_index
# ---------------------------------------------------------------------------


def test_remove_doc_from_index_drops_doc_and_deleted_concepts(kb_dir):
    wiki = kb_dir / "wiki"
    (wiki / "index.md").write_text(
        "# Knowledge Base Index\n\n"
        "## Documents\n"
        "- [[summaries/attn-x]] (short) - foo\n"
        "- [[summaries/survey-y]] (short) - bar\n\n"
        "## Concepts\n"
        "- [[concepts/transformer]] - Architecture\n"
        "- [[concepts/attention]] - Mechanism\n\n"
        "## Explorations\n",
        encoding="utf-8",
    )

    remove_doc_from_index(wiki, "attn-x", concept_slugs_deleted=["transformer"])

    text = (wiki / "index.md").read_text(encoding="utf-8")
    assert "[[summaries/attn-x]]" not in text
    assert "[[summaries/survey-y]]" in text
    assert "[[concepts/transformer]]" not in text
    assert "[[concepts/attention]]" in text
    # Section headings preserved even when last item removed
    assert "## Documents" in text and "## Concepts" in text


def test_remove_doc_from_index_noop_when_missing(tmp_path):
    # Should not raise when index.md doesn't exist.
    remove_doc_from_index(tmp_path / "wiki", "anything", [])


# ---------------------------------------------------------------------------
# HashRegistry.remove_by_doc_name
# ---------------------------------------------------------------------------


def test_hash_registry_remove_by_doc_name(tmp_path):
    path = tmp_path / "hashes.json"
    path.write_text(json.dumps({
        "h1": {"name": "a.pdf", "doc_name": "a-h1", "type": "short"},
        "h2": {"name": "b.pdf", "doc_name": "b-h2", "type": "short"},
    }))

    reg = HashRegistry(path)
    assert reg.remove_by_doc_name("a-h1") is True
    assert reg.remove_by_doc_name("a-h1") is False  # already gone
    assert "h2" in reg.all_entries() and "h1" not in reg.all_entries()

    # Persisted to disk
    saved = json.loads(path.read_text())
    assert list(saved.keys()) == ["h2"]


# ---------------------------------------------------------------------------
# _resolve_doc_identifier
# ---------------------------------------------------------------------------


def _make_registry(tmp_path: Path, entries: dict[str, dict]) -> HashRegistry:
    p = tmp_path / "hashes.json"
    p.write_text(json.dumps(entries))
    return HashRegistry(p)


def test_resolve_identifier_exact_name_wins(tmp_path):
    reg = _make_registry(tmp_path, {
        "h1": {"name": "attention.pdf", "doc_name": "attention-h1"},
        "h2": {"name": "attention-survey.pdf", "doc_name": "attention-survey-h2"},
    })
    matches = _resolve_doc_identifier(reg, "attention.pdf")
    assert [h for h, _ in matches] == ["h1"]


def test_resolve_identifier_exact_doc_name(tmp_path):
    reg = _make_registry(tmp_path, {
        "h1": {"name": "a.pdf", "doc_name": "a-h1"},
        "h2": {"name": "b.pdf", "doc_name": "b-h2"},
    })
    matches = _resolve_doc_identifier(reg, "b-h2")
    assert [h for h, _ in matches] == ["h2"]


def test_resolve_identifier_fuzzy_returns_all(tmp_path):
    reg = _make_registry(tmp_path, {
        "h1": {"name": "attention-paper.pdf", "doc_name": "attention-paper-h1"},
        "h2": {"name": "llm-attention.pdf", "doc_name": "llm-attention-h2"},
        "h3": {"name": "unrelated.pdf", "doc_name": "unrelated-h3"},
    })
    matches = _resolve_doc_identifier(reg, "attention")
    assert sorted(h for h, _ in matches) == ["h1", "h2"]


def test_resolve_identifier_empty(tmp_path):
    reg = _make_registry(tmp_path, {
        "h1": {"name": "a.pdf", "doc_name": "a-h1"},
    })
    assert _resolve_doc_identifier(reg, "nope") == []


# ---------------------------------------------------------------------------
# CLI: openkb remove
# ---------------------------------------------------------------------------


def _seed_two_doc_kb(kb_dir: Path) -> None:
    """Build a KB with two summaries and three concepts spanning them.

    Layout:
      raw/attention.pdf, raw/llm-survey.pdf
      wiki/summaries/{attention-h_a.md, llm-h_l.md}
      wiki/concepts/transformer.md (sources: attention only — single-source)
      wiki/concepts/attention.md   (sources: both — multi-source)
      wiki/concepts/llm.md         (sources: llm only — single-source)
      wiki/index.md with both Documents and all three Concepts entries
    """
    (kb_dir / ".openkb" / "hashes.json").write_text(json.dumps({
        "h_a": {
            "name": "attention.pdf", "doc_name": "attention-h_a",
            "type": "short", "path": "raw/attention.pdf",
        },
        "h_l": {
            "name": "llm-survey.pdf", "doc_name": "llm-h_l",
            "type": "short", "path": "raw/llm-survey.pdf",
        },
    }))
    (kb_dir / "raw" / "attention.pdf").write_bytes(b"%PDF-attention")
    (kb_dir / "raw" / "llm-survey.pdf").write_bytes(b"%PDF-llm")

    (kb_dir / "wiki" / "summaries" / "attention-h_a.md").write_text(
        "---\nsources: [raw/attention.pdf]\nbrief: Attn\n---\n"
        "# Attention\n\nLinks [[concepts/transformer]] and [[concepts/attention]].\n",
        encoding="utf-8",
    )
    (kb_dir / "wiki" / "summaries" / "llm-h_l.md").write_text(
        "---\nsources: [raw/llm-survey.pdf]\nbrief: LLM\n---\n"
        "# LLM Survey\n\nLinks [[concepts/llm]] and [[concepts/attention]].\n",
        encoding="utf-8",
    )

    (kb_dir / "wiki" / "concepts" / "transformer.md").write_text(
        "---\nsources: [summaries/attention-h_a.md]\nbrief: T\n---\n"
        "# Transformer\n\n## Related Documents\n- [[summaries/attention-h_a]]\n",
        encoding="utf-8",
    )
    (kb_dir / "wiki" / "concepts" / "attention.md").write_text(
        "---\nsources: [summaries/attention-h_a.md, summaries/llm-h_l.md]\nbrief: A\n---\n"
        "# Attention\n\n## Related Documents\n"
        "- [[summaries/attention-h_a]]\n- [[summaries/llm-h_l]]\n",
        encoding="utf-8",
    )
    (kb_dir / "wiki" / "concepts" / "llm.md").write_text(
        "---\nsources: [summaries/llm-h_l.md]\nbrief: L\n---\n"
        "# LLM\n\n## Related Documents\n- [[summaries/llm-h_l]]\n",
        encoding="utf-8",
    )

    (kb_dir / "wiki" / "index.md").write_text(
        "# Knowledge Base Index\n\n"
        "## Documents\n"
        "- [[summaries/attention-h_a]] (short) - Attn paper\n"
        "- [[summaries/llm-h_l]] (short) - LLM survey\n\n"
        "## Concepts\n"
        "- [[concepts/transformer]] - Architecture\n"
        "- [[concepts/attention]] - Mechanism\n"
        "- [[concepts/llm]] - General LLM\n\n"
        "## Explorations\n",
        encoding="utf-8",
    )

    (kb_dir / "wiki" / "log.md").write_text("# Log\n", encoding="utf-8")


def _invoke(kb_dir, args, input_text=None):
    return CliRunner().invoke(
        cli, ["--kb-dir", str(kb_dir), *args], input=input_text,
    )


def test_cli_remove_dry_run_does_nothing(kb_dir):
    _seed_two_doc_kb(kb_dir)
    result = _invoke(kb_dir, ["remove", "attention.pdf", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "dry-run" in result.output
    assert "DELETE" in result.output
    # All files still present
    assert (kb_dir / "wiki" / "summaries" / "attention-h_a.md").exists()
    assert (kb_dir / "wiki" / "concepts" / "transformer.md").exists()
    assert (kb_dir / "raw" / "attention.pdf").exists()
    hashes = json.loads((kb_dir / ".openkb" / "hashes.json").read_text())
    assert "h_a" in hashes


def test_cli_remove_yes_executes_full_plan(kb_dir):
    _seed_two_doc_kb(kb_dir)
    result = _invoke(kb_dir, ["remove", "attention.pdf", "--yes"])

    assert result.exit_code == 0, result.output

    # Summary + single-source concept gone
    assert not (kb_dir / "wiki" / "summaries" / "attention-h_a.md").exists()
    assert not (kb_dir / "wiki" / "concepts" / "transformer.md").exists()

    # Multi-source concept kept, but source dropped
    attn = (kb_dir / "wiki" / "concepts" / "attention.md").read_text()
    assert "attention-h_a" not in attn
    assert "llm-h_l" in attn

    # Untouched concept stays
    assert (kb_dir / "wiki" / "concepts" / "llm.md").exists()

    # Raw file gone (no --keep-raw)
    assert not (kb_dir / "raw" / "attention.pdf").exists()

    # Hash registry pruned
    hashes = json.loads((kb_dir / ".openkb" / "hashes.json").read_text())
    assert "h_a" not in hashes and "h_l" in hashes

    # Index updated
    index = (kb_dir / "wiki" / "index.md").read_text()
    assert "summaries/attention-h_a" not in index
    assert "concepts/transformer" not in index
    assert "summaries/llm-h_l" in index
    assert "concepts/attention" in index

    # Log appended
    assert "remove" in (kb_dir / "wiki" / "log.md").read_text()


def test_cli_remove_keep_raw_preserves_file(kb_dir):
    _seed_two_doc_kb(kb_dir)
    result = _invoke(kb_dir, ["remove", "attention.pdf", "--keep-raw", "--yes"])

    assert result.exit_code == 0, result.output
    assert (kb_dir / "raw" / "attention.pdf").exists()
    assert not (kb_dir / "wiki" / "summaries" / "attention-h_a.md").exists()


def test_cli_remove_keep_empty_concepts(kb_dir):
    _seed_two_doc_kb(kb_dir)
    result = _invoke(
        kb_dir, ["remove", "attention.pdf", "--keep-empty-concepts", "--yes"],
    )

    assert result.exit_code == 0, result.output
    # transformer.md retained with empty sources
    transformer = kb_dir / "wiki" / "concepts" / "transformer.md"
    assert transformer.exists()
    assert "sources: []" in transformer.read_text()


def test_cli_remove_by_doc_name_slug(kb_dir):
    _seed_two_doc_kb(kb_dir)
    result = _invoke(kb_dir, ["remove", "attention-h_a", "--yes"])

    assert result.exit_code == 0, result.output
    assert not (kb_dir / "wiki" / "summaries" / "attention-h_a.md").exists()


def test_cli_remove_unknown_identifier(kb_dir):
    _seed_two_doc_kb(kb_dir)
    result = _invoke(kb_dir, ["remove", "no-such-doc", "--yes"])

    assert result.exit_code == 0
    assert "No document matching" in result.output
    # Nothing modified
    assert (kb_dir / "wiki" / "summaries" / "attention-h_a.md").exists()


def test_cli_remove_ambiguous_identifier(kb_dir):
    _seed_two_doc_kb(kb_dir)
    # "h_" substring matches both doc_names; should refuse to act.
    result = _invoke(kb_dir, ["remove", "h_", "--yes"])

    assert result.exit_code == 0
    assert "matches multiple" in result.output
    assert (kb_dir / "wiki" / "summaries" / "attention-h_a.md").exists()
    assert (kb_dir / "wiki" / "summaries" / "llm-h_l.md").exists()


def test_cli_remove_confirm_no_aborts(kb_dir):
    _seed_two_doc_kb(kb_dir)
    # No --yes; reply "n" to the confirm prompt.
    result = _invoke(kb_dir, ["remove", "attention.pdf"], input_text="n\n")

    assert result.exit_code == 0
    assert "Aborted" in result.output
    assert (kb_dir / "wiki" / "summaries" / "attention-h_a.md").exists()


def test_cli_remove_lint_cleans_dangling_links(kb_dir):
    """`openkb remove` must auto-run lint --fix so wikilinks pointing at
    the deleted summary/concept get stripped from sibling pages.
    """
    _seed_two_doc_kb(kb_dir)
    # Plant a stray reference to the about-to-be-deleted summary inside
    # the surviving concept's body (not in any structured section).
    llm_path = kb_dir / "wiki" / "concepts" / "llm.md"
    llm_path.write_text(
        llm_path.read_text() + "\nSee also [[summaries/attention-h_a]] for background.\n",
        encoding="utf-8",
    )

    result = _invoke(kb_dir, ["remove", "attention.pdf", "--yes"])

    assert result.exit_code == 0, result.output
    cleaned = llm_path.read_text()
    assert "[[summaries/attention-h_a]]" not in cleaned
