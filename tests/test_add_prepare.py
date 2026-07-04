from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch


def _setup_kb(tmp_path: Path) -> Path:
    (tmp_path / "raw").mkdir(parents=True)
    (tmp_path / "wiki" / "sources" / "images").mkdir(parents=True)
    (tmp_path / "wiki" / "summaries").mkdir(parents=True)
    (tmp_path / "wiki" / "concepts").mkdir(parents=True)
    (tmp_path / "wiki" / "entities").mkdir(parents=True)
    openkb_dir = tmp_path / ".openkb"
    openkb_dir.mkdir()
    (openkb_dir / "config.yaml").write_text("model: gpt-4o-mini\n", encoding="utf-8")
    (openkb_dir / "hashes.json").write_text(json.dumps({}), encoding="utf-8")
    return tmp_path


def test_prepare_document_writes_only_private_staging(tmp_path):
    from openkb.add_prepare import prepare_document

    kb_dir = _setup_kb(tmp_path / "kb")
    doc = tmp_path / "source.md"
    doc.write_text("# Source\n", encoding="utf-8")

    prepared = prepare_document(doc, kb_dir, input_index=0)

    assert prepared.input_index == 0
    assert prepared.source_path == doc
    assert prepared.staging_dir.is_dir()
    assert prepared.staging_dir.is_relative_to(kb_dir / ".openkb" / "staging" / "prepare")
    assert not (kb_dir / "raw" / "source.md").exists()
    assert not (kb_dir / "wiki" / "sources" / "source.md").exists()
    assert json.loads((kb_dir / ".openkb" / "hashes.json").read_text(encoding="utf-8")) == {}
    assert not (kb_dir / ".openkb" / "journal").exists()


def test_prepare_document_does_not_take_mutation_lock(tmp_path):
    from openkb.add_prepare import prepare_document

    kb_dir = _setup_kb(tmp_path / "kb")
    doc = tmp_path / "source.md"
    doc.write_text("# Source\n", encoding="utf-8")

    with patch("openkb.converter.kb_ingest_lock", side_effect=AssertionError("lock acquired")):
        prepared = prepare_document(doc, kb_dir, input_index=3)

    assert prepared.input_index == 3


def test_prepare_document_cleans_staging_on_keyboard_interrupt(tmp_path):
    import pytest

    from openkb.add_prepare import prepare_document

    kb_dir = _setup_kb(tmp_path / "kb")
    doc = tmp_path / "source.md"
    doc.write_text("# Source\n", encoding="utf-8")

    with patch("openkb.add_prepare.convert_document_for_prepare", side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            prepare_document(doc, kb_dir, input_index=0)

    prepare_root = kb_dir / ".openkb" / "staging" / "prepare"
    assert not list(prepare_root.glob("*"))


def test_prepare_staging_dir_uses_sanitize_stem_fallback(tmp_path):
    from openkb.add_prepare import _prepare_staging_dir

    kb_dir = tmp_path / "kb"
    # A stem of all non-word characters: the old hand-rolled sanitizer produced
    # an empty segment; the shared _sanitize_stem falls back to "document".
    source = tmp_path / "！！！.md"
    staging = _prepare_staging_dir(kb_dir, 0, source)

    assert "document" in staging.name
    assert staging.is_dir()


def test_commit_prepared_document_resolves_final_name_under_serial_owner(tmp_path):
    from openkb.add_prepare import prepare_document
    from openkb.cli import commit_prepared_document
    from openkb.locks import kb_ingest_lock
    from openkb.state import HashRegistry

    kb_dir = _setup_kb(tmp_path / "kb")
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first = first_dir / "note.md"
    second = second_dir / "note.md"
    first.write_text("# First\n", encoding="utf-8")
    second.write_text("# Second\n", encoding="utf-8")

    HashRegistry(kb_dir / ".openkb" / "hashes.json").add(
        "existing",
        {"name": "note.md", "doc_name": "note", "type": "md", "path": str(first)},
    )
    with patch("openkb.cli.asyncio.run"), patch("openkb.cli._setup_llm_key"):
        with kb_ingest_lock(kb_dir / ".openkb"):
            prepared = prepare_document(second, kb_dir, input_index=0)
            outcome = commit_prepared_document(prepared, kb_dir)

    assert outcome == "added"
    entries = HashRegistry(kb_dir / ".openkb" / "hashes.json").all_entries()
    committed = [meta for meta in entries.values() if meta.get("name") == "note.md"]
    assert any(meta.get("doc_name") == "note" for meta in committed)
    renamed = [meta for meta in committed if meta.get("doc_name", "").startswith("note-")]
    assert renamed
    assert (kb_dir / "raw" / f"{renamed[0]['doc_name']}.md").exists()
    assert (kb_dir / "wiki" / "sources" / f"{renamed[0]['doc_name']}.md").exists()


def test_retarget_preserves_lf_line_endings(tmp_path, monkeypatch):
    """Renaming a prepared source on collision must keep it LF.

    The convert phase writes via atomic_write_text (binary, LF preserved), but
    retarget used Path.write_text without newline=, which on Windows translates
    \\n to \\r\\n — so a collision-renamed source ends up CRLF while every other
    source stays LF (inconsistent KB, noisy git diffs, stray \\r in \\n-split
    parsers). POSIX write_text already leaves LF, so we force the Windows
    translation to prove the contract holds regardless of platform.
    """
    from openkb.add_prepare import PreparedDocument
    from openkb.cli import _retarget_prepared_document_artifacts
    from openkb.converter import ConvertResult

    staging = tmp_path / "staging"
    (staging / "raw").mkdir(parents=True)
    sources = staging / "wiki" / "sources"
    sources.mkdir(parents=True)

    old_name = "orig"
    new_name = "orig-deadbeef"
    old_source = sources / f"{old_name}.md"
    old_source.write_text("# Title\n\nsources/images/orig/x.png\n", encoding="utf-8")
    old_raw = staging / "raw" / f"{old_name}.md"
    old_raw.write_text("raw\n", encoding="utf-8")

    prepared = PreparedDocument(
        input_index=0,
        source_path=Path(f"{old_name}.md"),
        staging_dir=staging,
        result=ConvertResult(doc_name=old_name, raw_path=old_raw, source_path=old_source),
    )

    real_write_text = Path.write_text

    def windows_translate(self, data, *args, **kwargs):
        # Mimic Windows default text-mode write (newline=None): \n -> \r\n.
        return real_write_text(self, data.replace("\n", "\r\n"), *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", windows_translate)

    _retarget_prepared_document_artifacts(prepared, new_name)

    new_source = sources / f"{new_name}.md"
    assert new_source.exists()
    assert b"\r\n" not in new_source.read_bytes()


def test_commit_prepared_document_requires_serial_owner_lock(tmp_path):
    import pytest

    from openkb.add_prepare import prepare_document
    from openkb.cli import commit_prepared_document

    kb_dir = _setup_kb(tmp_path / "kb")
    doc = tmp_path / "source.md"
    doc.write_text("# Source\n", encoding="utf-8")
    prepared = prepare_document(doc, kb_dir, input_index=0)

    with pytest.raises(RuntimeError, match="requires the caller to hold kb_ingest_lock"):
        commit_prepared_document(prepared, kb_dir)

    assert prepared.staging_dir.exists()
    assert not (kb_dir / "raw" / "source.md").exists()


def test_commit_reprepares_when_prepare_skip_is_stale(tmp_path):
    """Prepare saw the hash as known (skipped, no artifacts); the entry is then
    removed before commit. Commit must re-decide skip authoritatively and
    re-prepare, not silently drop the document."""
    from openkb.add_prepare import prepare_document
    from openkb.cli import commit_prepared_document
    from openkb.locks import kb_ingest_lock
    from openkb.state import HashRegistry

    kb_dir = _setup_kb(tmp_path / "kb")
    doc = tmp_path / "source.md"
    doc.write_text("# Source\n", encoding="utf-8")
    registry_path = kb_dir / ".openkb" / "hashes.json"
    real_hash = HashRegistry.hash_file(doc)
    HashRegistry(registry_path).add(
        real_hash, {"name": "source.md", "doc_name": "source", "type": "md"}
    )

    with patch("openkb.cli.asyncio.run"), patch("openkb.cli._setup_llm_key"):
        with kb_ingest_lock(kb_dir / ".openkb"):
            prepared = prepare_document(doc, kb_dir, input_index=0)
            assert prepared.result.skipped is True
            assert prepared.result.raw_path is None  # prepare short-circuited
            # The hash is removed between prepare and commit (e.g. `openkb remove`).
            HashRegistry(registry_path).remove_by_hash(real_hash)
            outcome = commit_prepared_document(prepared, kb_dir)

    assert outcome == "added"  # not silently skipped
    assert (kb_dir / "wiki" / "sources" / "source.md").exists()
    assert HashRegistry(registry_path).is_known(real_hash)


def test_commit_reprepares_when_source_changed_after_prepare(tmp_path):
    """The source is edited between the lock-free prepare and commit. The KB must
    hold the NEW content registered under the NEW hash, not the stale prepare-time
    bytes/hash (the TOCTOU regression)."""
    from openkb.add_prepare import prepare_document
    from openkb.cli import commit_prepared_document
    from openkb.locks import kb_ingest_lock
    from openkb.state import HashRegistry

    kb_dir = _setup_kb(tmp_path / "kb")
    doc = tmp_path / "source.md"
    doc.write_text("# Original\n", encoding="utf-8")

    with patch("openkb.cli.asyncio.run"), patch("openkb.cli._setup_llm_key"):
        with kb_ingest_lock(kb_dir / ".openkb"):
            prepared = prepare_document(doc, kb_dir, input_index=0)
            original_hash = prepared.result.file_hash
            doc.write_text("# Tampered\n", encoding="utf-8")  # edited mid-flight
            outcome = commit_prepared_document(prepared, kb_dir)

    assert outcome == "added"
    tampered_hash = HashRegistry.hash_file(doc)
    assert tampered_hash != original_hash
    registered = HashRegistry(kb_dir / ".openkb" / "hashes.json").get(tampered_hash)
    assert registered is not None
    assert registered["doc_name"] == "source"
    assert (kb_dir / "raw" / "source.md").read_text(encoding="utf-8") == "# Tampered\n"
