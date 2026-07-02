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
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from openkb.agent.compiler import (
    _remove_section_entry,
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
    text = "---\nsources: [summaries/a.md, summaries/b.md, summaries/c.md]\nbrief: x\n---\n\nbody\n"
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
    related = "\n".join(f"- [[{s.replace('.md', '')}]]" for s in sources)
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
        wiki,
        "attention",
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
    path.write_text(
        json.dumps(
            {
                "h1": {"name": "a.pdf", "doc_name": "a-h1", "type": "short"},
                "h2": {"name": "b.pdf", "doc_name": "b-h2", "type": "short"},
            }
        )
    )

    reg = HashRegistry(path)
    assert reg.remove_by_doc_name("a-h1") is True
    assert reg.remove_by_doc_name("a-h1") is False  # already gone
    assert "h2" in reg.all_entries() and "h1" not in reg.all_entries()

    # Persisted to disk
    saved = json.loads(path.read_text())
    assert list(saved.keys()) == ["h2"]


def test_hash_registry_remove_by_hash(tmp_path):
    """Issue #58 / Bug 1: `remove_by_hash` is the hash-keyed sibling of
    `remove_by_doc_name`. It exists so callers that already have the
    file_hash in hand (e.g. the CLI after `_resolve_doc_identifier`)
    don't need to round-trip through the `doc_name` slug — that round
    trip is what silently no-ops for legacy registry entries lacking a
    `doc_name` key (ingested before commit c504e26).
    """
    path = tmp_path / "hashes.json"
    path.write_text(
        json.dumps(
            {
                "h_modern": {"name": "a.pdf", "doc_name": "a-h_modern", "type": "short"},
                "h_legacy": {"name": "b.pdf", "type": "short"},  # no doc_name
            }
        )
    )

    reg = HashRegistry(path)

    # Modern entry: removable by hash.
    assert reg.remove_by_hash("h_modern") is True
    assert reg.remove_by_hash("h_modern") is False  # idempotent
    # Legacy entry (no doc_name): removable by hash too — that's the point.
    assert reg.remove_by_hash("h_legacy") is True
    # Unknown hash: returns False, doesn't raise.
    assert reg.remove_by_hash("never-existed") is False

    assert reg.all_entries() == {}
    assert json.loads(path.read_text()) == {}


# ---------------------------------------------------------------------------
# _resolve_doc_identifier
# ---------------------------------------------------------------------------


def _make_registry(tmp_path: Path, entries: dict[str, dict]) -> HashRegistry:
    p = tmp_path / "hashes.json"
    p.write_text(json.dumps(entries))
    return HashRegistry(p)


def test_resolve_identifier_exact_name_wins(tmp_path):
    reg = _make_registry(
        tmp_path,
        {
            "h1": {"name": "attention.pdf", "doc_name": "attention-h1"},
            "h2": {"name": "attention-survey.pdf", "doc_name": "attention-survey-h2"},
        },
    )
    matches = _resolve_doc_identifier(reg, "attention.pdf")
    assert [h for h, _ in matches] == ["h1"]


def test_resolve_identifier_exact_doc_name(tmp_path):
    reg = _make_registry(
        tmp_path,
        {
            "h1": {"name": "a.pdf", "doc_name": "a-h1"},
            "h2": {"name": "b.pdf", "doc_name": "b-h2"},
        },
    )
    matches = _resolve_doc_identifier(reg, "b-h2")
    assert [h for h, _ in matches] == ["h2"]


def test_resolve_identifier_fuzzy_returns_all(tmp_path):
    reg = _make_registry(
        tmp_path,
        {
            "h1": {"name": "attention-paper.pdf", "doc_name": "attention-paper-h1"},
            "h2": {"name": "llm-attention.pdf", "doc_name": "llm-attention-h2"},
            "h3": {"name": "unrelated.pdf", "doc_name": "unrelated-h3"},
        },
    )
    matches = _resolve_doc_identifier(reg, "attention")
    assert sorted(h for h, _ in matches) == ["h1", "h2"]


def test_resolve_identifier_empty(tmp_path):
    reg = _make_registry(
        tmp_path,
        {
            "h1": {"name": "a.pdf", "doc_name": "a-h1"},
        },
    )
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
    (kb_dir / ".openkb" / "hashes.json").write_text(
        json.dumps(
            {
                "h_a": {
                    "name": "attention.pdf",
                    "doc_name": "attention-h_a",
                    "type": "short",
                    "path": "raw/attention.pdf",
                },
                "h_l": {
                    "name": "llm-survey.pdf",
                    "doc_name": "llm-h_l",
                    "type": "short",
                    "path": "raw/llm-survey.pdf",
                },
            }
        )
    )
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
        cli,
        ["--kb-dir", str(kb_dir), *args],
        input=input_text,
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


def test_cli_remove_preview_lists_entity_actions(kb_dir):
    """The dry-run preview must enumerate entity-page DELETE/MODIFY actions
    and report an 'N entity(s) will be DELETED' summary line."""
    _seed_two_doc_kb(kb_dir)
    (kb_dir / "wiki" / "entities").mkdir(parents=True)
    # Single-source entity (only attention) -> will be DELETED
    (kb_dir / "wiki" / "entities" / "vaswani.md").write_text(
        "---\nsources: [summaries/attention-h_a.md]\ntype: person\nbrief: V\n---\n"
        "# Vaswani\n\n## Related Documents\n- [[summaries/attention-h_a]]\n",
        encoding="utf-8",
    )
    # Multi-source entity (both) -> will be MODIFIED
    (kb_dir / "wiki" / "entities" / "google.md").write_text(
        "---\nsources: [summaries/attention-h_a.md, summaries/llm-h_l.md]\n"
        "type: organization\nbrief: G\n---\n# Google\n",
        encoding="utf-8",
    )

    result = _invoke(kb_dir, ["remove", "attention.pdf", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "DELETE   wiki/entities/vaswani.md" in result.output
    assert "MODIFY   wiki/entities/google.md" in result.output
    assert "1 entity(s) will be DELETED" in result.output
    # Nothing actually removed in dry-run.
    assert (kb_dir / "wiki" / "entities" / "vaswani.md").exists()


def test_cli_remove_preview_handles_json_quoted_sources(kb_dir):
    """Regression: the real compiler writes sources JSON-quoted
    (sources: ["summaries/x.md"]). The old preview parser comma-split the line
    keeping the quotes, so the marker never matched and the preview silently
    reported 0 affected pages even though the executor would delete/edit them."""
    _seed_two_doc_kb(kb_dir)
    (kb_dir / "wiki" / "entities").mkdir(parents=True)
    # JSON-quoted single source (exactly how _yaml_list_line writes it) -> DELETE
    (kb_dir / "wiki" / "entities" / "vaswani.md").write_text(
        '---\nsources: ["summaries/attention-h_a.md"]\ntype: person\nbrief: V\n---\n# Vaswani\n',
        encoding="utf-8",
    )
    # JSON-quoted multi-source concept -> MODIFY
    (kb_dir / "wiki" / "concepts" / "quoted-concept.md").write_text(
        '---\nsources: ["summaries/attention-h_a.md", "summaries/llm-h_l.md"]\nbrief: Q\n---\n# Q\n',
        encoding="utf-8",
    )

    result = _invoke(kb_dir, ["remove", "attention.pdf", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "DELETE   wiki/entities/vaswani.md" in result.output
    assert "MODIFY   wiki/concepts/quoted-concept.md" in result.output


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
    """The --keep-empty-concepts alias is still accepted (backward compat)."""
    _seed_two_doc_kb(kb_dir)
    result = _invoke(
        kb_dir,
        ["remove", "attention.pdf", "--keep-empty-concepts", "--yes"],
    )

    assert result.exit_code == 0, result.output
    # transformer.md retained with empty sources
    transformer = kb_dir / "wiki" / "concepts" / "transformer.md"
    assert transformer.exists()
    assert "sources: []" in transformer.read_text()


def test_cli_remove_keep_empty_retains_concepts_and_entities(kb_dir):
    """The unified --keep-empty flag retains BOTH concept and entity pages
    whose only source was the removed doc (not just concepts)."""
    _seed_two_doc_kb(kb_dir)
    (kb_dir / "wiki" / "entities").mkdir(parents=True)
    (kb_dir / "wiki" / "entities" / "vaswani.md").write_text(
        '---\nsources: ["summaries/attention-h_a.md"]\ntype: person\nbrief: V\n---\n# Vaswani\n',
        encoding="utf-8",
    )

    result = _invoke(kb_dir, ["remove", "attention.pdf", "--keep-empty", "--yes"])

    assert result.exit_code == 0, result.output
    # single-source entity retained (not deleted), with emptied sources
    vaswani = kb_dir / "wiki" / "entities" / "vaswani.md"
    assert vaswani.exists()
    assert "sources: []" in vaswani.read_text()
    # single-source concept retained too
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


def _seed_legacy_kb(kb_dir: Path) -> None:
    """Seed a KB whose registry entry pre-dates PR #51.

    Layout reflects the issue #58 / Bug 1 repro: ``hashes.json`` only
    has ``{name, type}`` (no ``doc_name`` key), and the wiki paths use
    the bare stem of the original filename — which is also what
    ``cli.py``'s ``Path(name).stem`` fallback produces on the read path.
    """
    (kb_dir / ".openkb" / "hashes.json").write_text(
        json.dumps(
            {
                "h_legacy": {"name": "ollama.md", "type": "md"},
                "h_keep": {"name": "other.md", "type": "md"},  # untouched bystander
            }
        )
    )
    (kb_dir / "raw" / "ollama.md").write_text("# Ollama\n", encoding="utf-8")
    (kb_dir / "raw" / "other.md").write_text("# Other\n", encoding="utf-8")

    (kb_dir / "wiki" / "summaries" / "ollama.md").write_text(
        "---\nsources: [raw/ollama.md]\nbrief: x\n---\n# Ollama\n",
        encoding="utf-8",
    )
    (kb_dir / "wiki" / "summaries" / "other.md").write_text(
        "---\nsources: [raw/other.md]\nbrief: y\n---\n# Other\n",
        encoding="utf-8",
    )
    (kb_dir / "wiki" / "index.md").write_text(
        "# Knowledge Base Index\n\n## Documents\n"
        "- [[summaries/ollama]] (md) - Ollama notes\n"
        "- [[summaries/other]] (md) - Other notes\n\n"
        "## Concepts\n\n## Explorations\n",
        encoding="utf-8",
    )
    (kb_dir / "wiki" / "log.md").write_text("# Log\n", encoding="utf-8")


def test_cli_remove_prunes_legacy_registry_entry_without_doc_name(kb_dir):
    """Issue #58 / Bug 1 regression: a registry entry written before
    PR #51 has only ``{name, type}``. The earlier remove path called
    ``remove_by_doc_name(meta.get('doc_name') or Path(name).stem)``,
    which silently no-op'd because ``meta.get('doc_name')`` is None for
    every legacy row — leaving an orphan hash entry that then re-bound
    the next ``openkb add`` of the same file via SHA dedup.

    Fix: the CLI now prunes the registry by the already-resolved
    ``file_hash`` (returned by ``_resolve_doc_identifier``), so the
    metadata shape doesn't matter.
    """
    _seed_legacy_kb(kb_dir)
    hashes_path = kb_dir / ".openkb" / "hashes.json"

    result = _invoke(kb_dir, ["remove", "ollama.md", "--yes"])

    assert result.exit_code == 0, result.output

    remaining = json.loads(hashes_path.read_text())
    assert "h_legacy" not in remaining, (
        "legacy registry entry survived remove — see issue #58 Bug 1"
    )
    # Sibling legacy entry is untouched.
    assert "h_keep" in remaining

    # Wiki side-effects of the remove still happened (sanity).
    assert not (kb_dir / "wiki" / "summaries" / "ollama.md").exists()
    assert not (kb_dir / "raw" / "ollama.md").exists()


def test_cli_remove_lint_cleans_dangling_links_in_modified_page(kb_dir):
    """`openkb remove` must auto-run a scoped lint --fix so wikilinks
    pointing at the just-deleted summary get stripped from concept
    pages that this removal modified.

    Scope (issue #58 / Bug 2): only files in ``concept_result["modified"]``
    plus ``index.md`` — see ``test_cli_remove_preserves_ghosts_in_unrelated_pages``
    for the complementary contract.
    """
    _seed_two_doc_kb(kb_dir)
    # Plant a stray free-text reference in the body of the MULTI-source
    # concept page — `concepts/attention.md` has both attention-h_a and
    # llm-h_l as sources, so the remove flow modifies it (drops
    # attention-h_a from the frontmatter). The lint pass should also
    # strip the dangling free-text link.
    attn_path = kb_dir / "wiki" / "concepts" / "attention.md"
    attn_path.write_text(
        attn_path.read_text() + "\nSee also [[summaries/attention-h_a]] for background.\n",
        encoding="utf-8",
    )

    result = _invoke(kb_dir, ["remove", "attention.pdf", "--yes"])

    assert result.exit_code == 0, result.output
    cleaned = attn_path.read_text()
    assert "[[summaries/attention-h_a]]" not in cleaned


def test_cli_remove_preserves_ghosts_in_unrelated_pages(kb_dir):
    """Issue #58 / Bug 2 regression: `openkb remove` must NOT strip
    pre-existing dangling wikilinks from concept pages that don't have
    the removed doc in their frontmatter sources.

    Before the fix, ``fix_broken_links(wiki_dir)`` swept the whole wiki
    on every remove, producing 39-file / 1254-line diffs and silently
    deleting links the user had hand-written to not-yet-added concepts.
    """
    _seed_two_doc_kb(kb_dir)
    # llm.md's frontmatter sources include only ``summaries/llm-h_l`` —
    # the removal of ``attention.pdf`` does NOT touch its sources list,
    # so it is out of scope for the post-remove lint pass.
    llm_path = kb_dir / "wiki" / "concepts" / "llm.md"
    original = llm_path.read_text() + (
        "\nFollow-up: [[concepts/agent-loops]] (concept I'll add next week).\n"
        "Also a hand-added back-ref [[summaries/attention-h_a]] users may want.\n"
    )
    llm_path.write_text(original, encoding="utf-8")

    result = _invoke(kb_dir, ["remove", "attention.pdf", "--yes"])

    assert result.exit_code == 0, result.output
    surviving = llm_path.read_text()
    # Pre-existing ghost to a not-yet-added concept survives.
    assert "[[concepts/agent-loops]]" in surviving
    # And a hand-added link to the just-deleted summary also survives
    # because llm.md is OUT OF SCOPE for this removal's cleanup. Users
    # who want a wiki-wide sweep can run `openkb lint --fix` explicitly.
    assert "[[summaries/attention-h_a]]" in surviving


# ---------------------------------------------------------------------------
# Regression: code-review issue #1
# `openkb add` must persist `doc_name` so `remove_by_doc_name` can prune
# the registry entry. Earlier add_single_file only stored `name` + `type`,
# so the new remove flow silently no-op'd on the registry write.
# ---------------------------------------------------------------------------


def test_add_persists_doc_name_for_later_remove(tmp_path):
    """End-to-end: `openkb add` writes a registry entry with `doc_name`,
    and a subsequent `openkb remove` actually prunes that entry.
    """
    from openkb.converter import ConvertResult

    # Minimal KB scaffolding (mirrors conftest.kb_dir but localised so we
    # can fully control the add pipeline via mocks below).
    (tmp_path / "raw").mkdir()
    (tmp_path / "wiki" / "summaries").mkdir(parents=True)
    (tmp_path / "wiki" / "sources" / "images").mkdir(parents=True)
    (tmp_path / "wiki" / "concepts").mkdir(parents=True)
    (tmp_path / "wiki" / "explorations").mkdir(parents=True)
    (tmp_path / "wiki" / "reports").mkdir(parents=True)
    (tmp_path / "wiki" / "index.md").write_text(
        "# Knowledge Base Index\n\n## Documents\n\n## Concepts\n\n## Explorations\n",
        encoding="utf-8",
    )
    (tmp_path / "wiki" / "log.md").write_text("# Log\n", encoding="utf-8")
    openkb_dir = tmp_path / ".openkb"
    openkb_dir.mkdir()
    (openkb_dir / "config.yaml").write_text("model: gpt-4o-mini\n")
    (openkb_dir / "hashes.json").write_text("{}")

    doc = tmp_path / "paper.md"
    doc.write_text("# Hello", encoding="utf-8")
    raw_path = tmp_path / "raw" / "paper.md"
    raw_path.write_text("# Hello", encoding="utf-8")
    source_path = tmp_path / "wiki" / "sources" / "paper.md"
    source_path.write_text("# Hello converted", encoding="utf-8")
    summary_path = tmp_path / "wiki" / "summaries" / "paper.md"
    summary_path.write_text(
        "---\nsources: [raw/paper.md]\nbrief: x\n---\n# Paper\n",
        encoding="utf-8",
    )

    mock_result = ConvertResult(
        raw_path=raw_path,
        source_path=source_path,
        is_long_doc=False,
        file_hash="deadbeef" * 8,  # 64 hex chars
    )

    runner = CliRunner()
    # Mock convert_document + asyncio.run to skip the LLM-driven compile.
    with (
        patch("openkb.cli._find_kb_dir", return_value=tmp_path),
        patch("openkb.cli.convert_document", return_value=mock_result),
        patch("openkb.cli.asyncio.run"),
    ):
        add_res = runner.invoke(cli, ["add", str(doc)])
    assert add_res.exit_code == 0, add_res.output

    # The registry write contract: doc_name must be present.
    hashes = json.loads((openkb_dir / "hashes.json").read_text())
    assert len(hashes) == 1
    ((_, meta),) = hashes.items()
    assert meta["name"] == "paper.md"
    assert meta["doc_name"] == "paper"
    assert meta["type"] == "md"

    # And the remove command must actually drop that entry — not silently no-op.
    rm_res = runner.invoke(
        cli,
        ["--kb-dir", str(tmp_path), "remove", "paper.md", "--keep-raw", "--yes"],
    )
    assert rm_res.exit_code == 0, rm_res.output
    assert json.loads((openkb_dir / "hashes.json").read_text()) == {}


# ---------------------------------------------------------------------------
# Regression: code-review issue #2
# `_remove_section_entry` must match the canonical `- {link}` bullet form
# strictly; a substring fallback wrongly deleted sibling entries whose
# brief text referenced the removed link.
# ---------------------------------------------------------------------------


def test_remove_section_entry_strict_prefix_no_sibling_overdelete():
    lines = [
        "## Documents",
        "- [[summaries/attn-x]] (short) - The attn paper",
        "- [[summaries/survey-y]] (short) - supersedes [[summaries/attn-x]]",
        "- [[summaries/other-z]] (short) - unrelated",
        "",
        "## Concepts",
    ]
    removed = _remove_section_entry(lines, "## Documents", "[[summaries/attn-x]]")

    assert removed is True
    # Only the actual attn-x bullet is gone; the survey-y entry that
    # *mentions* attn-x in its brief survives intact.
    joined = "\n".join(lines)
    assert "- [[summaries/attn-x]]" not in joined
    assert "- [[summaries/survey-y]]" in joined
    assert "supersedes [[summaries/attn-x]]" in joined
    assert "- [[summaries/other-z]]" in joined


def test_remove_section_entry_skips_non_bullet_mentions():
    """A wikilink embedded in a paragraph (no bullet prefix) must not
    be deleted — the helper only manages canonical bullet entries.
    """
    lines = [
        "## Related Documents",
        "This concept is mostly covered by [[summaries/attn-x]] in spirit.",
        "- [[summaries/survey-y]]",
        "",
    ]
    removed = _remove_section_entry(lines, "## Related Documents", "[[summaries/attn-x]]")

    assert removed is False  # No `- [[summaries/attn-x]]` bullet in section.
    assert any("This concept is mostly covered by [[summaries/attn-x]]" in line for line in lines)


# ---------------------------------------------------------------------------
# Regression: code-review issue #3
# CLI plan-builder must classify concept pages by frontmatter `sources:`
# membership only — body-only references (e.g. a stray "See also:" or an
# unrelated wikilink in the body) should not flip the page into DELETE.
# ---------------------------------------------------------------------------


def test_cli_remove_ignores_body_only_reference_in_plan(kb_dir):
    """A concept whose frontmatter sources do NOT include the removed doc
    but whose body has a `See also:` line should not appear in the DELETE
    or MODIFY action list for that doc.
    """
    _seed_two_doc_kb(kb_dir)

    # Plant a concept whose frontmatter source is llm-h_l only, but whose
    # body mentions attention-h_a via "See also:". When we remove
    # attention.pdf, this page should NOT be reported as affected.
    stray = kb_dir / "wiki" / "concepts" / "stray.md"
    stray.write_text(
        "---\nsources: [summaries/llm-h_l.md]\nbrief: stray\n---\n"
        "# Stray\n\nbody text\n\nSee also: [[summaries/attention-h_a]]\n",
        encoding="utf-8",
    )

    result = _invoke(kb_dir, ["remove", "attention.pdf", "--dry-run"])

    assert result.exit_code == 0, result.output
    # Plan must not announce any action on `stray` for this removal.
    assert "concepts/stray.md" not in result.output
    # Sanity: the regular affected concepts are still listed.
    assert "concepts/transformer.md" in result.output
    assert "concepts/attention.md" in result.output


# ---------------------------------------------------------------------------
# Regression: code-review issue #4
# `See also:` stripping must preserve surrounding paragraph spacing — the
# earlier `\s*` greediness collapsed blank lines and left orphan blank
# lines at end-of-file.
# ---------------------------------------------------------------------------


def test_remove_doc_strips_see_also_without_collapsing_paragraphs(kb_dir):
    wiki = kb_dir / "wiki"
    path = wiki / "concepts" / "c.md"
    path.write_text(
        "---\nsources: [summaries/a.md, summaries/b.md]\n---\n"
        "# c\n\npara1\n\nSee also: [[summaries/a]]\n\npara2\n",
        encoding="utf-8",
    )

    remove_doc_from_concept_pages(wiki, "a")

    out = path.read_text(encoding="utf-8")
    # The See also line is gone.
    assert "See also: [[summaries/a]]" not in out
    # And the surrounding blank-line separator between para1 and para2
    # is preserved (markdown paragraph break still intact).
    assert "para1\n\npara2" in out


def test_remove_doc_strips_trailing_see_also_cleanly(kb_dir):
    """When `_add_related_link` appends `\\n\\nSee also: link` and removal
    is requested, the trailing See also block should disappear cleanly
    without leaving a dangling double-blank line at end-of-file.
    """
    wiki = kb_dir / "wiki"
    path = wiki / "concepts" / "c.md"
    path.write_text(
        "---\nsources: [summaries/a.md, summaries/b.md]\n---\n"
        "# c\n\nbody\n\nSee also: [[summaries/a]]",
        encoding="utf-8",
    )

    remove_doc_from_concept_pages(wiki, "a")

    out = path.read_text(encoding="utf-8")
    assert "See also" not in out
    # Body content survives; the file does not end with two consecutive
    # newlines from a leftover blank line.
    assert out.rstrip().endswith("body")


# ---------------------------------------------------------------------------
# Functional-completeness fix: per-doc images directory cleanup
# `openkb add` writes images into wiki/sources/images/<doc_name>/. Remove
# must take that whole tree with it — otherwise image-heavy docs leak
# tens to hundreds of MB per add → remove cycle.
# ---------------------------------------------------------------------------


def test_cli_remove_deletes_per_doc_images_directory(kb_dir):
    _seed_two_doc_kb(kb_dir)

    # Plant a populated images directory for attention.pdf.
    images_dir = kb_dir / "wiki" / "sources" / "images" / "attention-h_a"
    images_dir.mkdir(parents=True)
    (images_dir / "p1_img1.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    (images_dir / "p2_img1.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    # Unrelated doc's images dir must survive.
    other_images = kb_dir / "wiki" / "sources" / "images" / "llm-h_l"
    other_images.mkdir(parents=True)
    (other_images / "p1_img1.png").write_bytes(b"\x89PNG")

    result = _invoke(kb_dir, ["remove", "attention.pdf", "--yes"])

    assert result.exit_code == 0, result.output
    assert "images directory" in result.output  # mentioned in plan
    assert not images_dir.exists()
    # Sibling doc's image dir is untouched.
    assert other_images.exists()
    assert (other_images / "p1_img1.png").exists()


def test_cli_remove_dry_run_does_not_touch_images(kb_dir):
    _seed_two_doc_kb(kb_dir)
    images_dir = kb_dir / "wiki" / "sources" / "images" / "attention-h_a"
    images_dir.mkdir(parents=True)
    (images_dir / "p1_img1.png").write_bytes(b"\x89PNG")

    result = _invoke(kb_dir, ["remove", "attention.pdf", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "images directory" in result.output
    # Directory still on disk after dry-run.
    assert images_dir.exists()
    assert (images_dir / "p1_img1.png").exists()


# ---------------------------------------------------------------------------
# Functional-completeness fix: `doc_id` is persisted for long PDFs so a
# later `openkb remove` can call PageIndex's delete_document API.
# ---------------------------------------------------------------------------


def test_add_long_pdf_persists_doc_id_to_registry(tmp_path):
    """Long-doc ingest must record `doc_id` in the registry. Without it,
    the remove path has no handle to feed `Collection.delete_document`.
    """
    from openkb.converter import ConvertResult
    from openkb.indexer import IndexResult

    # Minimal KB
    (tmp_path / "raw").mkdir()
    (tmp_path / "wiki" / "summaries").mkdir(parents=True)
    (tmp_path / "wiki" / "sources" / "images").mkdir(parents=True)
    (tmp_path / "wiki" / "concepts").mkdir(parents=True)
    (tmp_path / "wiki" / "explorations").mkdir(parents=True)
    (tmp_path / "wiki" / "reports").mkdir(parents=True)
    (tmp_path / "wiki" / "index.md").write_text(
        "# Knowledge Base Index\n\n## Documents\n\n## Concepts\n\n## Explorations\n",
        encoding="utf-8",
    )
    (tmp_path / "wiki" / "log.md").write_text("# Log\n", encoding="utf-8")
    openkb_dir = tmp_path / ".openkb"
    openkb_dir.mkdir()
    (openkb_dir / "config.yaml").write_text("model: gpt-4o-mini\n")
    (openkb_dir / "hashes.json").write_text("{}")

    pdf = tmp_path / "long.pdf"
    pdf.write_bytes(b"%PDF-1.4\n" + b"\x00" * 200)
    raw_path = tmp_path / "raw" / "long.pdf"
    raw_path.write_bytes(pdf.read_bytes())

    convert_mock = ConvertResult(
        raw_path=raw_path,
        source_path=None,
        is_long_doc=True,
        file_hash="cafebabe" * 8,
    )
    index_mock = IndexResult(
        doc_id="pi-doc-abc123",
        description="A long PDF",
        tree={},
    )

    runner = CliRunner()
    with (
        patch("openkb.cli._find_kb_dir", return_value=tmp_path),
        patch("openkb.cli.convert_document", return_value=convert_mock),
        patch("openkb.indexer.index_long_document", return_value=index_mock),
        patch("openkb.cli.asyncio.run"),
    ):
        result = runner.invoke(cli, ["add", str(pdf)])

    assert result.exit_code == 0, result.output
    hashes = json.loads((openkb_dir / "hashes.json").read_text())
    ((_, meta),) = hashes.items()
    assert meta["type"] == "long_pdf"
    assert meta["doc_id"] == "pi-doc-abc123"


# ---------------------------------------------------------------------------
# Functional-completeness fix: PageIndex local state cleanup on remove.
# ---------------------------------------------------------------------------


def _seed_long_pdf_kb(kb_dir: Path, doc_id: str | None = "pi-doc-xyz") -> None:
    """Seed a KB with a single long-PDF entry plus a stub pageindex.db
    file so the remove command treats the doc as PageIndex-backed.
    """
    meta = {
        "name": "paper.pdf",
        "doc_name": "paper",
        "type": "long_pdf",
        "path": "raw/paper.pdf",
    }
    if doc_id is not None:
        meta["doc_id"] = doc_id
    (kb_dir / ".openkb" / "hashes.json").write_text(json.dumps({"h_paper": meta}))
    (kb_dir / "raw" / "paper.pdf").write_bytes(b"%PDF-fake")
    (kb_dir / "wiki" / "summaries" / "paper.md").write_text(
        "---\nsources: [raw/paper.pdf]\nbrief: x\n---\n# Paper\n",
        encoding="utf-8",
    )
    (kb_dir / "wiki" / "sources" / "paper.json").write_text("[]", encoding="utf-8")
    (kb_dir / "wiki" / "index.md").write_text(
        "# Knowledge Base Index\n\n## Documents\n- [[summaries/paper]] (long_pdf) - x\n\n"
        "## Concepts\n\n## Explorations\n",
        encoding="utf-8",
    )
    (kb_dir / "wiki" / "log.md").write_text("# Log\n", encoding="utf-8")
    # Stub PageIndex state — its mere existence flips the cleanup path on.
    (kb_dir / ".openkb" / "pageindex.db").write_bytes(b"SQLite format 3\x00")


def test_cli_remove_calls_pageindex_delete_with_stored_doc_id(kb_dir):
    """When the registry entry has a `doc_id`, remove must call
    `Collection.delete_document(doc_id)` directly — no list_documents
    lookup needed.
    """
    _seed_long_pdf_kb(kb_dir, doc_id="pi-doc-xyz")

    fake_col = MagicMock()
    fake_client = MagicMock()
    fake_client.collection.return_value = fake_col

    with (
        patch("pageindex.PageIndexClient", return_value=fake_client) as mock_cls,
        patch("openkb.cli._setup_llm_key"),
    ):
        result = _invoke(kb_dir, ["remove", "paper.pdf", "--keep-raw", "--yes"])

    assert result.exit_code == 0, result.output
    mock_cls.assert_called_once()
    # Storage path must point at the KB's .openkb directory.
    _, kwargs = mock_cls.call_args
    assert kwargs.get("storage_path") == str(kb_dir / ".openkb")
    fake_col.delete_document.assert_called_once_with("pi-doc-xyz")
    fake_col.list_documents.assert_not_called()  # No fallback needed
    assert "PageIndex" in result.output


def test_cli_remove_pageindex_fallback_lookup_by_doc_name(kb_dir):
    """Legacy registry entries (added before PR #51) have no `doc_id`.
    The remove path must fall back to matching by doc_name via
    list_documents() so existing KBs aren't permanently leaking.
    """
    _seed_long_pdf_kb(kb_dir, doc_id=None)

    fake_col = MagicMock()
    fake_col.list_documents.return_value = [
        {"doc_id": "pi-found-id", "doc_name": "paper", "doc_type": "pdf"},
        {"doc_id": "pi-other-id", "doc_name": "other", "doc_type": "pdf"},
    ]
    fake_client = MagicMock()
    fake_client.collection.return_value = fake_col

    with (
        patch("pageindex.PageIndexClient", return_value=fake_client),
        patch("openkb.cli._setup_llm_key"),
    ):
        result = _invoke(kb_dir, ["remove", "paper.pdf", "--keep-raw", "--yes"])

    assert result.exit_code == 0, result.output
    fake_col.list_documents.assert_called_once()
    fake_col.delete_document.assert_called_once_with("pi-found-id")


def test_cli_remove_pageindex_fallback_skips_on_ambiguous_match(kb_dir):
    """Two PageIndex docs share doc_name='paper' (different ingests). The
    fallback must refuse to guess; the rest of the remove flow still
    completes and the WARN is surfaced.
    """
    _seed_long_pdf_kb(kb_dir, doc_id=None)

    fake_col = MagicMock()
    fake_col.list_documents.return_value = [
        {"doc_id": "pi-a", "doc_name": "paper"},
        {"doc_id": "pi-b", "doc_name": "paper"},
    ]
    fake_client = MagicMock()
    fake_client.collection.return_value = fake_col

    with (
        patch("pageindex.PageIndexClient", return_value=fake_client),
        patch("openkb.cli._setup_llm_key"),
    ):
        result = _invoke(kb_dir, ["remove", "paper.pdf", "--keep-raw", "--yes"])

    assert result.exit_code == 0, result.output
    fake_col.delete_document.assert_not_called()
    assert "skipping" in result.output
    # The wiki-side cleanup still ran.
    assert not (kb_dir / "wiki" / "summaries" / "paper.md").exists()


def test_cli_remove_skips_pageindex_when_no_state_file(kb_dir):
    """Short-doc-only KBs never created `.openkb/pageindex.db`. The
    remove flow must not import or instantiate PageIndexClient in that
    case — opening it would unnecessarily require an LLM key.
    """
    _seed_two_doc_kb(kb_dir)
    assert not (kb_dir / ".openkb" / "pageindex.db").exists()

    with patch("pageindex.PageIndexClient") as mock_cls:
        result = _invoke(kb_dir, ["remove", "attention.pdf", "--yes"])

    assert result.exit_code == 0, result.output
    mock_cls.assert_not_called()
    assert "PageIndex" not in result.output


# ---------------------------------------------------------------------------
# Consistency-fix regression: PageIndex failure must NOT clear the registry,
# so the user can retry. The previous order removed the registry entry first
# and then attempted PageIndex cleanup — leaving an orphan SQLite row that
# silently re-bound on the next `openkb add` via PageIndex's SHA-256 dedup.
# ---------------------------------------------------------------------------


def test_cli_remove_pageindex_failure_preserves_registry_for_retry(kb_dir):
    """When `_cleanup_pageindex` raises, the registry entry (and its
    `doc_id`) must survive so a subsequent `openkb remove` invocation
    has the handle needed to retry. Wiki-side cleanup still happens
    because every step is idempotent across retries.
    """
    _seed_long_pdf_kb(kb_dir, doc_id="pi-doc-xyz")

    fake_client = MagicMock()
    fake_client.collection.side_effect = RuntimeError("LLM key missing")

    with (
        patch("pageindex.PageIndexClient", return_value=fake_client),
        patch("openkb.cli._setup_llm_key"),
    ):
        result = _invoke(kb_dir, ["remove", "paper.pdf", "--keep-raw", "--yes"])

    # Command exits cleanly with a WARN — not an error code — because the
    # user has a clear path forward (re-run).
    assert result.exit_code == 0, result.output
    assert "[WARN]" in result.output
    assert "re-run" in result.output

    # Registry entry is intact for retry.
    hashes = json.loads((kb_dir / ".openkb" / "hashes.json").read_text())
    assert "h_paper" in hashes
    assert hashes["h_paper"]["doc_id"] == "pi-doc-xyz"

    # Wiki side was cleaned (idempotent on retry).
    assert not (kb_dir / "wiki" / "summaries" / "paper.md").exists()
    assert not (kb_dir / "wiki" / "sources" / "paper.json").exists()


def test_cli_remove_retry_after_pageindex_failure_completes(kb_dir):
    """First attempt fails at PageIndex; second attempt with a working
    PageIndex completes the removal. Validates that the post-failure
    state is fully retryable.
    """
    _seed_long_pdf_kb(kb_dir, doc_id="pi-doc-xyz")

    # First attempt: PageIndex raises.
    failing_client = MagicMock()
    failing_client.collection.side_effect = RuntimeError("transient")
    with (
        patch("pageindex.PageIndexClient", return_value=failing_client),
        patch("openkb.cli._setup_llm_key"),
    ):
        first = _invoke(kb_dir, ["remove", "paper.pdf", "--keep-raw", "--yes"])
    assert first.exit_code == 0
    assert "[WARN]" in first.output
    # Registry entry survived for retry.
    assert "h_paper" in json.loads((kb_dir / ".openkb" / "hashes.json").read_text())

    # Second attempt: PageIndex succeeds. Same doc_id must drive the
    # delete since it's still in the registry.
    working_col = MagicMock()
    working_client = MagicMock()
    working_client.collection.return_value = working_col
    with (
        patch("pageindex.PageIndexClient", return_value=working_client),
        patch("openkb.cli._setup_llm_key"),
    ):
        second = _invoke(kb_dir, ["remove", "paper.pdf", "--keep-raw", "--yes"])

    assert second.exit_code == 0, second.output
    working_col.delete_document.assert_called_once_with("pi-doc-xyz")

    # Registry now empty; wiki cleanup remains complete.
    assert json.loads((kb_dir / ".openkb" / "hashes.json").read_text()) == {}
    assert not (kb_dir / "wiki" / "summaries" / "paper.md").exists()


# ---------------------------------------------------------------------------
# Regression: doc-name collision fix — raw copies are renamed to doc_name
# on copy (raw/{doc_name}{suffix}), so remove must locate them via the
# recorded `raw_path` in registry meta, not `raw_dir / name`.
# ---------------------------------------------------------------------------


def test_cli_remove_deletes_renamed_raw_copy(kb_dir):
    """Raw copies are now named by doc_name; remove must use meta raw_path."""
    # Collided doc: original filename report.md, doc_name report-aabbccdd
    raw_file = kb_dir / "raw" / "report-aabbccdd.md"
    raw_file.write_text("# R", encoding="utf-8")
    (kb_dir / "wiki" / "sources" / "report-aabbccdd.md").write_text("# R", encoding="utf-8")
    HashRegistry(kb_dir / ".openkb" / "hashes.json").add(
        "h-collide",
        {
            "name": "report.md",
            "doc_name": "report-aabbccdd",
            "type": "md",
            "path": "inputs/second/report.md",
            "raw_path": "raw/report-aabbccdd.md",
            "source_path": "wiki/sources/report-aabbccdd.md",
        },
    )

    result = _invoke(kb_dir, ["remove", "report-aabbccdd", "--yes"])

    assert result.exit_code == 0, result.output
    assert not raw_file.exists()


# ---------------------------------------------------------------------------
# Cloud-imported docs (type=pageindex_cloud, origin=cloud) own no local raw
# file and the user's cloud corpus is their asset — remove must clean up ONLY
# local wiki artifacts and must NEVER contact PageIndex Cloud.
# ---------------------------------------------------------------------------


def test_remove_cloud_doc_never_touches_pageindex(tmp_path):
    """A pageindex_cloud doc removes only local artifacts; the cloud is
    never contacted even when a pageindex.db happens to exist."""
    from unittest.mock import patch

    from click.testing import CliRunner

    from openkb.cli import cli
    from openkb.state import HashRegistry

    # Minimal KB
    (tmp_path / "raw").mkdir()
    for sub in ("sources/images", "summaries", "concepts", "entities", "reports"):
        (tmp_path / "wiki" / sub).mkdir(parents=True)
    openkb_dir = tmp_path / ".openkb"
    openkb_dir.mkdir()
    (openkb_dir / "config.yaml").write_text("model: gpt-4o-mini\n")
    # A stray pageindex.db to prove the cloud path is gated by type, not just
    # by the DB's absence.
    (openkb_dir / "pageindex.db").write_bytes(b"")
    (tmp_path / "wiki" / "index.md").write_text("# Index\n")

    # Cloud artifacts + registry entry
    (tmp_path / "wiki" / "summaries" / "cloud-doc.md").write_text("---\n---\n# s\n")
    (tmp_path / "wiki" / "sources" / "cloud-doc.json").write_text("[]")
    registry = HashRegistry(openkb_dir / "hashes.json")
    registry.add(
        "synthhash",
        {
            "name": "Cloud Paper.pdf",
            "doc_name": "cloud-doc",
            "type": "pageindex_cloud",
            "origin": "cloud",
            "path": "pageindex-cloud:cloud-1",
            "source_path": "wiki/sources/cloud-doc.json",
            "doc_id": "cloud-1",
        },
    )

    runner = CliRunner()
    with (
        patch("openkb.cli._find_kb_dir", return_value=tmp_path),
        patch("pageindex.PageIndexClient") as mock_client,
    ):
        result = runner.invoke(cli, ["remove", "cloud-doc", "--yes"])

    assert result.exit_code == 0, result.output
    # The cloud client must never be constructed.
    mock_client.assert_not_called()
    # Local artifacts are gone and the registry entry is removed.
    assert not (tmp_path / "wiki" / "summaries" / "cloud-doc.md").exists()
    assert not (tmp_path / "wiki" / "sources" / "cloud-doc.json").exists()
    assert HashRegistry(openkb_dir / "hashes.json").get("synthhash") is None
