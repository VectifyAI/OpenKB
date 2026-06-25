"""Tests for the `openkb recompile` CLI command.

`recompile` re-runs the current compile pipeline (compile_short_doc /
compile_long_doc) on already-indexed docs so pre-feature KBs gain the
entities/ layer and refresh to the current format. It does NOT re-run
PageIndex or re-convert raw files.

Covers:
- short-doc dispatch (compile_short_doc called with the right args)
- long-doc dispatch (compile_long_doc called with doc_id; PageIndex not invoked)
- --all confirmation + --yes bypass
- --dry-run: no compile calls, no writes
- skip+warn paths (missing source, missing summary/doc_id) with others
  still processed
- unknown <doc_name> / empty registry friendly error
- --refresh-schema backs up + overwrites only when AGENTS.md differs
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from openkb.agent import compiler
from openkb.cli import cli
from openkb.schema import AGENTS_MD


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _invoke(kb_dir, args, input_text=None):
    return CliRunner().invoke(
        cli, ["--kb-dir", str(kb_dir), *args], input=input_text,
    )


def _seed_short(kb_dir: Path) -> None:
    """One short doc with a source file on disk."""
    (kb_dir / ".openkb" / "hashes.json").write_text(json.dumps({
        "h_s": {"name": "notes.md", "doc_name": "notes-h_s", "type": "md"},
    }))
    (kb_dir / "wiki" / "sources" / "notes-h_s.md").write_text(
        "# Notes\n\nbody\n", encoding="utf-8",
    )
    (kb_dir / "wiki" / "log.md").write_text("# Log\n\n", encoding="utf-8")


def _seed_long(kb_dir: Path) -> None:
    """One long (PageIndex) doc with a summary file + doc_id on disk."""
    (kb_dir / ".openkb" / "hashes.json").write_text(json.dumps({
        "h_l": {
            "name": "paper.pdf", "doc_name": "paper-h_l",
            "type": "long_pdf", "doc_id": "doc-abc123",
        },
    }))
    (kb_dir / "wiki" / "summaries" / "paper-h_l.md").write_text(
        "---\nsources: [raw/paper.pdf]\nbrief: P\n---\n# Paper\n",
        encoding="utf-8",
    )
    (kb_dir / "wiki" / "log.md").write_text("# Log\n\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# short-doc dispatch
# ---------------------------------------------------------------------------


def test_recompile_short_dispatches_compile_short_doc(kb_dir):
    _seed_short(kb_dir)
    with patch("openkb.agent.compiler.compile_short_doc", new_callable=AsyncMock) as short, \
         patch("openkb.agent.compiler.compile_long_doc", new_callable=AsyncMock) as long_:
        result = _invoke(kb_dir, ["recompile", "notes.md"])

    assert result.exit_code == 0, result.output
    short.assert_called_once()
    args = short.call_args.args
    assert args[0] == "notes-h_s"  # doc_name
    assert args[1] == kb_dir / "wiki" / "sources" / "notes-h_s.md"  # source_path
    assert args[2] == kb_dir  # kb_dir
    long_.assert_not_called()
    assert "recompiled 1" in result.output


# ---------------------------------------------------------------------------
# long-doc dispatch
# ---------------------------------------------------------------------------


def test_recompile_long_dispatches_compile_long_doc_with_doc_id(kb_dir):
    _seed_long(kb_dir)
    with patch("openkb.agent.compiler.compile_long_doc", new_callable=AsyncMock) as long_, \
         patch("openkb.agent.compiler.compile_short_doc", new_callable=AsyncMock) as short, \
         patch("openkb.indexer.index_long_document") as index:
        result = _invoke(kb_dir, ["recompile", "paper.pdf"])

    assert result.exit_code == 0, result.output
    long_.assert_called_once()
    args = long_.call_args.args
    assert args[0] == "paper-h_l"  # doc_name
    assert args[1] == kb_dir / "wiki" / "summaries" / "paper-h_l.md"
    assert args[2] == "doc-abc123"  # doc_id
    assert args[3] == kb_dir
    short.assert_not_called()
    # PageIndex must NOT be re-run
    index.assert_not_called()
    assert "recompiled 1" in result.output


# ---------------------------------------------------------------------------
# cloud-import dispatch (type=pageindex_cloud) — must use the LONG path
# ---------------------------------------------------------------------------


def _seed_cloud(kb_dir: Path) -> None:
    """A pageindex_cloud import: long-doc layout (summary + doc_id + .json
    source), and NO .md source (the trap the short path would fall into)."""
    (kb_dir / ".openkb" / "hashes.json").write_text(json.dumps({
        "h_c": {
            "name": "Cloud Paper.pdf", "doc_name": "cloud-h_c",
            "type": "pageindex_cloud", "origin": "cloud", "doc_id": "pi-cloud1",
            "path": "pageindex-cloud:pi-cloud1",
        },
    }))
    (kb_dir / "wiki" / "summaries" / "cloud-h_c.md").write_text(
        "---\nsources: [pageindex-cloud:pi-cloud1]\n---\n# Cloud\n", encoding="utf-8",
    )
    (kb_dir / "wiki" / "sources" / "cloud-h_c.json").write_text("[]", encoding="utf-8")
    (kb_dir / "wiki" / "log.md").write_text("# Log\n\n", encoding="utf-8")


def test_recompile_cloud_doc_dispatches_compile_long_doc(kb_dir):
    """A pageindex_cloud doc must recompile via compile_long_doc (it has a .json
    source + doc_id), not be misrouted to the short path that looks for a .md."""
    _seed_cloud(kb_dir)
    with patch("openkb.agent.compiler.compile_long_doc", new_callable=AsyncMock) as long_, \
         patch("openkb.agent.compiler.compile_short_doc", new_callable=AsyncMock) as short:
        result = _invoke(kb_dir, ["recompile", "cloud-h_c"])

    assert result.exit_code == 0, result.output
    long_.assert_called_once()
    args = long_.call_args.args
    assert args[0] == "cloud-h_c"            # doc_name
    assert args[2] == "pi-cloud1"            # the cloud doc_id flows through
    short.assert_not_called()
    assert "recompiled 1" in result.output


def test_recompile_dry_run_classifies_cloud_as_long(kb_dir):
    _seed_cloud(kb_dir)
    result = _invoke(kb_dir, ["recompile", "cloud-h_c", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "(long)" in result.output and "(short)" not in result.output


def test_is_long_doc_and_display_type_cover_cloud():
    """pageindex_cloud is treated as a long doc and displayed like a pageindex
    doc in `openkb list` (no raw internal type string leaking)."""
    from openkb.cli import _is_long_doc, _display_type

    assert _is_long_doc({"type": "pageindex_cloud"}) is True
    assert _is_long_doc({"type": "long_pdf"}) is True
    assert _is_long_doc({"type": "md"}) is False
    assert _display_type("pageindex_cloud") == "pageindex"


# ---------------------------------------------------------------------------
# --all confirmation + --yes
# ---------------------------------------------------------------------------


def test_recompile_all_requires_confirmation(kb_dir):
    _seed_short(kb_dir)
    with patch("openkb.agent.compiler.compile_short_doc") as short:
        result = _invoke(kb_dir, ["recompile", "--all"], input_text="n\n")

    assert result.exit_code == 0, result.output
    assert "Aborted" in result.output
    short.assert_not_called()


def test_recompile_all_yes_bypasses_confirmation(kb_dir):
    _seed_short(kb_dir)
    with patch("openkb.agent.compiler.compile_short_doc", new_callable=AsyncMock) as short:
        result = _invoke(kb_dir, ["recompile", "--all", "--yes"])

    assert result.exit_code == 0, result.output
    short.assert_called_once()
    assert "recompiled 1" in result.output


# ---------------------------------------------------------------------------
# --dry-run
# ---------------------------------------------------------------------------


def test_recompile_dry_run_no_calls_no_writes(kb_dir):
    _seed_short(kb_dir)
    log_before = (kb_dir / "wiki" / "log.md").read_text()
    with patch("openkb.agent.compiler.compile_short_doc") as short, \
         patch("openkb.agent.compiler.compile_long_doc") as long_:
        result = _invoke(kb_dir, ["recompile", "--all", "--dry-run"])

    assert result.exit_code == 0, result.output
    short.assert_not_called()
    long_.assert_not_called()
    assert "notes-h_s" in result.output
    assert "short" in result.output
    # No writes: log.md unchanged
    assert (kb_dir / "wiki" / "log.md").read_text() == log_before


# ---------------------------------------------------------------------------
# skip + warn paths
# ---------------------------------------------------------------------------


def test_recompile_skips_short_missing_source(kb_dir):
    """Short doc with no source on disk is warned + skipped; others run."""
    (kb_dir / ".openkb" / "hashes.json").write_text(json.dumps({
        "h_ok": {"name": "ok.md", "doc_name": "ok-h_ok", "type": "md"},
        "h_miss": {"name": "gone.md", "doc_name": "gone-h_miss", "type": "md"},
    }))
    (kb_dir / "wiki" / "sources" / "ok-h_ok.md").write_text("# ok\n")
    (kb_dir / "wiki" / "log.md").write_text("# Log\n\n", encoding="utf-8")

    with patch("openkb.agent.compiler.compile_short_doc", new_callable=AsyncMock) as short:
        result = _invoke(kb_dir, ["recompile", "--all", "--yes"])

    assert result.exit_code == 0, result.output
    # only the doc with a present source compiled
    assert short.call_count == 1
    assert short.call_args.args[0] == "ok-h_ok"
    assert "recompiled 1" in result.output
    assert "skipped 1" in result.output


def test_recompile_skips_long_missing_doc_id(kb_dir):
    """Long doc lacking doc_id is warned + skipped; others run."""
    (kb_dir / ".openkb" / "hashes.json").write_text(json.dumps({
        "h_l": {"name": "legacy.pdf", "doc_name": "legacy-h_l", "type": "long_pdf"},
    }))
    (kb_dir / "wiki" / "summaries" / "legacy-h_l.md").write_text("# legacy\n")
    (kb_dir / "wiki" / "log.md").write_text("# Log\n\n", encoding="utf-8")

    with patch("openkb.agent.compiler.compile_long_doc") as long_:
        result = _invoke(kb_dir, ["recompile", "--all", "--yes"])

    assert result.exit_code == 0, result.output
    long_.assert_not_called()
    assert "skipped 1" in result.output
    assert "recompiled 0" in result.output


def test_recompile_skips_long_missing_summary(kb_dir):
    """Long doc with doc_id but no summary on disk is warned + skipped."""
    (kb_dir / ".openkb" / "hashes.json").write_text(json.dumps({
        "h_l": {
            "name": "paper.pdf", "doc_name": "paper-h_l",
            "type": "long_pdf", "doc_id": "doc-x",
        },
    }))
    (kb_dir / "wiki" / "log.md").write_text("# Log\n\n", encoding="utf-8")

    with patch("openkb.agent.compiler.compile_long_doc") as long_:
        result = _invoke(kb_dir, ["recompile", "--all", "--yes"])

    assert result.exit_code == 0, result.output
    long_.assert_not_called()
    assert "skipped 1" in result.output


# ---------------------------------------------------------------------------
# error paths
# ---------------------------------------------------------------------------


def test_recompile_requires_doc_or_all(kb_dir):
    _seed_short(kb_dir)
    with patch("openkb.agent.compiler.compile_short_doc", new_callable=AsyncMock) as short:
        result = _invoke(kb_dir, ["recompile"])
    # Usage guard echoes a message and returns (exit 0); no compile runs.
    assert "Specify a document name or pass --all" in result.output
    short.assert_not_called()


def test_recompile_doc_and_all_conflict(kb_dir):
    _seed_short(kb_dir)
    with patch("openkb.agent.compiler.compile_short_doc", new_callable=AsyncMock) as short:
        result = _invoke(kb_dir, ["recompile", "notes.md", "--all"])
    assert "not both" in result.output.lower()
    short.assert_not_called()


def test_recompile_unknown_doc_friendly_error(kb_dir):
    _seed_short(kb_dir)
    with patch("openkb.agent.compiler.compile_short_doc") as short:
        result = _invoke(kb_dir, ["recompile", "no-such-doc"])
    assert result.exit_code == 0, result.output
    assert "no-such-doc" in result.output
    short.assert_not_called()


def test_recompile_empty_registry_friendly_error(kb_dir):
    (kb_dir / ".openkb" / "hashes.json").write_text(json.dumps({}))
    with patch("openkb.agent.compiler.compile_short_doc") as short:
        result = _invoke(kb_dir, ["recompile", "--all"], input_text="y\n")
    assert result.exit_code == 0, result.output
    short.assert_not_called()
    assert "No documents" in result.output or "no documents" in result.output


# ---------------------------------------------------------------------------
# --refresh-schema
# ---------------------------------------------------------------------------


def test_recompile_refresh_schema_overwrites_when_differing(kb_dir):
    _seed_short(kb_dir)
    agents = kb_dir / "wiki" / "AGENTS.md"
    agents.write_text("OLD CUSTOM SCHEMA\n", encoding="utf-8")
    with patch("openkb.agent.compiler.compile_short_doc", new_callable=AsyncMock) as short:
        result = _invoke(kb_dir, ["recompile", "notes.md", "--refresh-schema"])

    assert result.exit_code == 0, result.output
    bak = kb_dir / "wiki" / "AGENTS.md.bak"
    assert bak.exists()
    assert bak.read_text(encoding="utf-8") == "OLD CUSTOM SCHEMA\n"
    assert agents.read_text(encoding="utf-8") == AGENTS_MD


def test_recompile_refresh_schema_noop_when_identical(kb_dir):
    _seed_short(kb_dir)
    agents = kb_dir / "wiki" / "AGENTS.md"
    agents.write_text(AGENTS_MD, encoding="utf-8")
    with patch("openkb.agent.compiler.compile_short_doc", new_callable=AsyncMock) as short:
        result = _invoke(kb_dir, ["recompile", "notes.md", "--refresh-schema"])

    assert result.exit_code == 0, result.output
    assert not (kb_dir / "wiki" / "AGENTS.md.bak").exists()


def test_recompile_no_refresh_schema_by_default(kb_dir):
    _seed_short(kb_dir)
    agents = kb_dir / "wiki" / "AGENTS.md"
    agents.write_text("OLD CUSTOM SCHEMA\n", encoding="utf-8")
    with patch("openkb.agent.compiler.compile_short_doc", new_callable=AsyncMock) as short:
        result = _invoke(kb_dir, ["recompile", "notes.md"])

    assert result.exit_code == 0, result.output
    # Untouched without the flag
    assert agents.read_text(encoding="utf-8") == "OLD CUSTOM SCHEMA\n"
    assert not (kb_dir / "wiki" / "AGENTS.md.bak").exists()


def test_recompile_refresh_schema_noop_when_agents_missing(kb_dir):
    """Spec: --refresh-schema is a no-op when AGENTS.md is absent (runtime
    already falls back to the bundled default), so nothing is written."""
    _seed_short(kb_dir)
    agents = kb_dir / "wiki" / "AGENTS.md"
    agents.unlink(missing_ok=True)
    with patch("openkb.agent.compiler.compile_short_doc", new_callable=AsyncMock) as short:
        result = _invoke(kb_dir, ["recompile", "notes.md", "--refresh-schema"])

    assert result.exit_code == 0, result.output
    assert not agents.exists()  # not materialized
    assert not (kb_dir / "wiki" / "AGENTS.md.bak").exists()


# ---------------------------------------------------------------------------
# compile_long_doc backfills type + description on recompile
# ---------------------------------------------------------------------------


def test_compile_long_doc_backfills_summary_frontmatter(tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "summaries").mkdir(parents=True)
    (wiki / "concepts").mkdir(parents=True)
    (tmp_path / ".openkb").mkdir()
    (tmp_path / ".openkb" / "config.yaml").write_text(
        "model: gpt-4o-mini\nlanguage: en\n", encoding="utf-8")
    summary_path = wiki / "summaries" / "long.md"
    summary_path.write_text(
        "---\ndoc_type: pageindex\nfull_text: sources/long.json\n---\n\n# Long\n",
        encoding="utf-8",
    )
    with patch.object(compiler, "_llm_call", return_value="overview"), \
         patch.object(compiler, "_compile_concepts", new=AsyncMock()), \
         patch.object(compiler, "_close_async_llm_clients", new=AsyncMock()):
        asyncio.run(compiler.compile_long_doc(
            "long", summary_path, "doc-1", tmp_path, "gpt-4o-mini",
            doc_description="A long report.",
        ))
    text = summary_path.read_text(encoding="utf-8")
    assert 'type: "Summary"' in text
    assert 'description: "A long report."' in text
    # canonical order: type before description
    assert text.index('type:') < text.index('description:')
