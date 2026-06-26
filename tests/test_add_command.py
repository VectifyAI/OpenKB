"""Tests for the `add` CLI command (Task 10)."""
from __future__ import annotations

import json
from unittest.mock import patch

from click.testing import CliRunner

from openkb.cli import SUPPORTED_EXTENSIONS, _find_kb_dir, cli


class TestSupportedExtensions:
    def test_pdf_supported(self):
        assert ".pdf" in SUPPORTED_EXTENSIONS

    def test_md_supported(self):
        assert ".md" in SUPPORTED_EXTENSIONS

    def test_docx_supported(self):
        assert ".docx" in SUPPORTED_EXTENSIONS

    def test_txt_supported(self):
        assert ".txt" in SUPPORTED_EXTENSIONS

    def test_unknown_not_supported(self):
        assert ".xyz" not in SUPPORTED_EXTENSIONS


class TestFindKbDir:
    def test_finds_openkb_dir(self, tmp_path, monkeypatch):
        (tmp_path / ".openkb").mkdir()
        monkeypatch.chdir(tmp_path)
        result = _find_kb_dir()
        assert result is not None

    def test_returns_none_if_no_openkb(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("openkb.cli.load_global_config", return_value={}):
            result = _find_kb_dir()
            assert result is None


class TestAddCommand:
    def _setup_kb(self, tmp_path):
        """Create a minimal KB structure."""
        (tmp_path / "raw").mkdir()
        (tmp_path / "wiki" / "sources" / "images").mkdir(parents=True)
        (tmp_path / "wiki" / "summaries").mkdir(parents=True)
        (tmp_path / "wiki" / "concepts").mkdir(parents=True)
        (tmp_path / "wiki" / "reports").mkdir(parents=True)
        openkb_dir = tmp_path / ".openkb"
        openkb_dir.mkdir()
        (openkb_dir / "config.yaml").write_text("model: gpt-4o-mini\n")
        (openkb_dir / "hashes.json").write_text(json.dumps({}))
        return tmp_path

    def test_add_missing_init(self, tmp_path):
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path), \
             patch("openkb.cli._find_kb_dir", return_value=None):
            result = runner.invoke(cli, ["add", "somefile.pdf"])
            assert "No knowledge base found" in result.output

    def test_add_single_file_calls_helper(self, tmp_path):
        kb_dir = self._setup_kb(tmp_path)
        doc = tmp_path / "test.md"
        doc.write_text("# Hello")

        runner = CliRunner()
        with patch("openkb.cli.add_single_file") as mock_add, \
             patch("openkb.cli._find_kb_dir", return_value=kb_dir):
            runner.invoke(cli, ["add", str(doc)])
            mock_add.assert_called_once_with(doc, kb_dir)

    def test_add_directory_calls_helper_for_each_file(self, tmp_path):
        kb_dir = self._setup_kb(tmp_path)
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "a.md").write_text("# A")
        (docs_dir / "b.txt").write_text("B content")
        (docs_dir / "ignore.xyz").write_text("skip me")

        from openkb.cli import _PreparedAdd

        def fake_prepare(file_path, kb_dir_arg, staging_dir, doc_name=None):
            return _PreparedAdd(file_path=file_path, staging_dir=staging_dir)

        runner = CliRunner()
        with patch("openkb.cli._prepare_add_file", side_effect=fake_prepare) as mock_prepare, \
             patch("openkb.cli._commit_prepared_add", return_value="added") as mock_commit, \
             patch("openkb.cli._find_kb_dir", return_value=kb_dir):
            runner.invoke(cli, ["add", str(docs_dir)])
            # Should be prepared/committed for .md and .txt but not .xyz
            assert mock_prepare.call_count == 2
            assert mock_commit.call_count == 2
            called_names = {call.args[0].name for call in mock_prepare.call_args_list}
            assert "a.md" in called_names
            assert "b.txt" in called_names
            assert "ignore.xyz" not in called_names

    def test_add_directory_prefilters_known_hashes_before_prepare(self, tmp_path):
        kb_dir = self._setup_kb(tmp_path)
        (kb_dir / ".openkb" / "config.yaml").write_text(
            "model: gpt-4o-mini\nfile_processing_jobs: 2\n",
            encoding="utf-8",
        )
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        known = docs_dir / "known.md"
        unknown = docs_dir / "unknown.md"
        known.write_text("# Known", encoding="utf-8")
        unknown.write_text("# Unknown", encoding="utf-8")

        from openkb.cli import _PreparedAdd
        from openkb.state import HashRegistry

        HashRegistry(kb_dir / ".openkb" / "hashes.json").add(
            HashRegistry.hash_file(known),
            {
                "name": "known.md",
                "doc_name": "known",
                "type": "md",
                "path": "docs/known.md",
            },
        )

        def fake_prepare(file_path, kb_dir_arg, staging_dir, doc_name=None):
            return _PreparedAdd(file_path=file_path, staging_dir=staging_dir)

        runner = CliRunner()
        with patch("openkb.cli._prepare_add_file", side_effect=fake_prepare) as mock_prepare, \
             patch("openkb.cli._commit_prepared_add", return_value="added") as mock_commit, \
             patch("openkb.cli._find_kb_dir", return_value=kb_dir):
            result = runner.invoke(cli, ["add", str(docs_dir)])

        assert result.exception is None
        assert [call.args[0].name for call in mock_prepare.call_args_list] == ["unknown.md"]
        assert [call.args[0].file_path.name for call in mock_commit.call_args_list] == [
            "known.md",
            "unknown.md",
        ]
        assert mock_commit.call_args_list[0].args[0].outcome == "skipped"

    def test_add_unsupported_extension(self, tmp_path):
        kb_dir = self._setup_kb(tmp_path)
        doc = tmp_path / "file.xyz"
        doc.write_text("content")

        runner = CliRunner()
        with patch("openkb.cli._find_kb_dir", return_value=kb_dir):
            result = runner.invoke(cli, ["add", str(doc)])
            assert "Unsupported file type" in result.output

    def test_add_nonexistent_path(self, tmp_path):
        kb_dir = self._setup_kb(tmp_path)

        runner = CliRunner()
        with patch("openkb.cli._find_kb_dir", return_value=kb_dir):
            result = runner.invoke(cli, ["add", str(tmp_path / "nonexistent.pdf")])
            assert "does not exist" in result.output

    def test_add_skipped_file(self, tmp_path):
        kb_dir = self._setup_kb(tmp_path)
        doc = tmp_path / "test.md"
        doc.write_text("# Hello")

        from openkb.converter import ConvertResult
        mock_result = ConvertResult(skipped=True)

        runner = CliRunner()
        with patch("openkb.cli._find_kb_dir", return_value=kb_dir), \
             patch("openkb.cli._convert_document_locked", return_value=mock_result), \
             patch("openkb.cli.asyncio.run") as mock_arun:
            result = runner.invoke(cli, ["add", str(doc)])
            assert "SKIP" in result.output
            mock_arun.assert_not_called()

    def test_add_short_doc_runs_compiler(self, tmp_path):
        kb_dir = self._setup_kb(tmp_path)
        doc = tmp_path / "test.md"
        doc.write_text("# Hello")

        source_path = kb_dir / "wiki" / "sources" / "test.md"
        source_path.write_text("# Hello converted")

        from openkb.converter import ConvertResult
        mock_result = ConvertResult(
            raw_path=kb_dir / "raw" / "test.md",
            source_path=source_path,
            is_long_doc=False,
            file_hash="deadbeef00" * 8,
            doc_name="test",
        )

        # An edited doc arrives with a new content hash; the stale entry
        # for the same doc_name must be replaced, leaving exactly ONE entry.
        from openkb.state import HashRegistry
        HashRegistry(kb_dir / ".openkb" / "hashes.json").add(
            "stale-old-hash", {"name": "test.md", "doc_name": "test", "type": "md"}
        )

        runner = CliRunner()
        with patch("openkb.cli._find_kb_dir", return_value=kb_dir), \
             patch("openkb.cli._convert_document_locked", return_value=mock_result), \
             patch("openkb.cli.asyncio.run") as mock_arun:
            result = runner.invoke(cli, ["add", str(doc)])
            mock_arun.assert_called_once()
            assert "OK" in result.output

        import json as json_mod
        hashes = json_mod.loads(
            (kb_dir / ".openkb" / "hashes.json").read_text(encoding="utf-8")
        )
        meta = hashes[mock_result.file_hash]
        assert meta["doc_name"] == "test"
        assert meta["raw_path"] == "raw/test.md"
        assert meta["source_path"] == "wiki/sources/test.md"
        assert "path" in meta
        assert "stale-old-hash" not in hashes

    def test_commit_keeps_journal_when_rollback_fails(self, tmp_path):
        from openkb.cli import _PreparedAdd, _commit_prepared_add
        from openkb.converter import ConvertResult

        kb_dir = self._setup_kb(tmp_path)
        source_path = kb_dir / "wiki" / "sources" / "broken.md"
        source_path.write_text("# Broken", encoding="utf-8")
        prepared = _PreparedAdd(
            file_path=tmp_path / "broken.md",
            result=ConvertResult(
                raw_path=kb_dir / "raw" / "broken.md",
                source_path=source_path,
                file_hash="beadfeed00" * 8,
                doc_name="broken",
            ),
        )

        class FakeSnapshot:
            journal_path = kb_dir / ".openkb" / "journal" / "broken.json"

            def __init__(self):
                self.discard_called = False

            def rollback_best_effort(self):
                return RuntimeError("rollback failed")

            def discard_best_effort(self):
                self.discard_called = True

        fake_snapshot = FakeSnapshot()

        with patch("openkb.cli.snapshot_paths", return_value=fake_snapshot), \
             patch("openkb.cli.publish_staged_tree"), \
             patch("openkb.cli.asyncio.run", side_effect=RuntimeError("compile failed")):
            outcome = _commit_prepared_add(prepared, kb_dir, "gpt-4o-mini")

        assert outcome == "failed"
        assert fake_snapshot.discard_called is False

    def test_add_oldest_legacy_entry_converges_to_single_entry(self, tmp_path):
        """Editing a pre-doc_name-era document must not fork the registry.

        convert_document backfills doc_name/path onto the legacy entry on
        disk; the cli's registry instance must see that backfill (i.e. be
        constructed after convert), otherwise its full-file rewrite clobbers
        the backfill and leaves two entries for one document.
        """
        import json as json_mod

        from openkb.state import HashRegistry

        kb_dir = self._setup_kb(tmp_path)
        # oldest-generation entry: name only, no doc_name, no path
        HashRegistry(kb_dir / ".openkb" / "hashes.json").add(
            "old-hash", {"name": "notes.md", "type": "md"}
        )
        doc = tmp_path / "notes.md"
        doc.write_text("# Notes, edited")  # new content hash != "old-hash"

        # Compilation mocked out (asyncio.run), but convert_document REAL so
        # the legacy backfill actually happens on disk mid-pipeline.
        runner = CliRunner()
        with patch("openkb.cli._find_kb_dir", return_value=kb_dir), \
             patch("openkb.cli.asyncio.run"):
            result = runner.invoke(cli, ["add", str(doc)])
            assert "OK" in result.output

        hashes = json_mod.loads(
            (kb_dir / ".openkb" / "hashes.json").read_text(encoding="utf-8")
        )
        assert "old-hash" not in hashes          # stale entry replaced…
        new_entries = [m for m in hashes.values() if m.get("doc_name") == "notes"]
        assert len(new_entries) == 1             # …exactly one entry survives
        assert new_entries[0]["path"]            # with path identity persisted

    def test_add_directory_legacy_entry_converges_to_single_entry(self, tmp_path):
        import json as json_mod

        from openkb.state import HashRegistry

        kb_dir = self._setup_kb(tmp_path)
        (kb_dir / ".openkb" / "config.yaml").write_text(
            "model: gpt-4o-mini\nfile_processing_jobs: 2\n",
            encoding="utf-8",
        )
        HashRegistry(kb_dir / ".openkb" / "hashes.json").add(
            "old-hash", {"name": "notes.md", "type": "md"}
        )
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "notes.md").write_text("# Notes, edited", encoding="utf-8")

        runner = CliRunner()
        with patch("openkb.cli._find_kb_dir", return_value=kb_dir), \
             patch("openkb.cli.asyncio.run"):
            result = runner.invoke(cli, ["add", str(docs_dir)])
            assert "OK" in result.output

        hashes = json_mod.loads(
            (kb_dir / ".openkb" / "hashes.json").read_text(encoding="utf-8")
        )
        assert "old-hash" not in hashes
        new_entries = [m for m in hashes.values() if m.get("doc_name") == "notes"]
        assert len(new_entries) == 1
        assert new_entries[0]["path"]

    def test_add_directory_same_stem_files_get_reserved_names(self, tmp_path):
        import json as json_mod

        kb_dir = self._setup_kb(tmp_path)
        (kb_dir / ".openkb" / "config.yaml").write_text(
            "model: gpt-4o-mini\nfile_processing_jobs: 2\n",
            encoding="utf-8",
        )
        docs_dir = tmp_path / "docs"
        (docs_dir / "a").mkdir(parents=True)
        (docs_dir / "b").mkdir(parents=True)
        (docs_dir / "a" / "report.md").write_text("# A", encoding="utf-8")
        (docs_dir / "b" / "report.md").write_text("# B", encoding="utf-8")

        runner = CliRunner()
        with patch("openkb.cli._find_kb_dir", return_value=kb_dir), \
             patch("openkb.cli.asyncio.run"):
            result = runner.invoke(cli, ["add", str(docs_dir)])
            assert "Document name conflict" not in result.output
            assert result.output.count("[OK]") == 2

        hashes = json_mod.loads(
            (kb_dir / ".openkb" / "hashes.json").read_text(encoding="utf-8")
        )
        doc_names = {meta["doc_name"] for meta in hashes.values()}
        assert len(doc_names) == 2
        assert "report" in doc_names
        assert any(name.startswith("report-") for name in doc_names)

    def test_add_directory_same_stem_with_legacy_entry_no_duplicate(self, tmp_path):
        """Two same-stem files plus a legacy (path-less) entry must not both
        reserve the legacy doc_name. ``find_legacy_by_stem`` must be consumed
        (idempotent) across the batch so the second file gets a suffixed name
        instead of colliding with the first.
        """
        import json as json_mod

        from openkb.state import HashRegistry

        kb_dir = self._setup_kb(tmp_path)
        (kb_dir / ".openkb" / "config.yaml").write_text(
            "model: gpt-4o-mini\nfile_processing_jobs: 2\n",
            encoding="utf-8",
        )
        # Legacy entry: name + doc_name but NO path → find_legacy_by_stem matches "report".
        HashRegistry(kb_dir / ".openkb" / "hashes.json").add(
            "legacy-hash", {"name": "report.md", "doc_name": "report", "type": "md"}
        )
        docs_dir = tmp_path / "docs"
        (docs_dir / "a").mkdir(parents=True)
        (docs_dir / "b").mkdir(parents=True)
        (docs_dir / "a" / "report.md").write_text("# A", encoding="utf-8")
        (docs_dir / "b" / "report.md").write_text("# B", encoding="utf-8")

        runner = CliRunner()
        with patch("openkb.cli._find_kb_dir", return_value=kb_dir), \
             patch("openkb.cli.asyncio.run"):
            result = runner.invoke(cli, ["add", str(docs_dir)])
        assert "Document name conflict" not in result.output
        assert result.output.count("[OK]") == 2

        hashes = json_mod.loads(
            (kb_dir / ".openkb" / "hashes.json").read_text(encoding="utf-8")
        )
        report_names = [
            m["doc_name"] for m in hashes.values() if str(m.get("doc_name", "")).startswith("report")
        ]
        assert len(report_names) == 2
        assert len(set(report_names)) == 2  # no silent overwrite

    def test_commit_rejects_same_filename_different_path_conflict(self, tmp_path):
        """A path-indexed entry sharing doc_name + filename but with a
        different path (a concurrent add of a same-named file in the
        reservation/commit window) must be rejected, not silently
        overwritten via the filename escape.
        """
        from openkb.cli import _PreparedAdd, _commit_prepared_add
        from openkb.converter import ConvertResult
        from openkb.state import HashRegistry

        kb_dir = self._setup_kb(tmp_path)
        source_path = kb_dir / "wiki" / "sources" / "report.md"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text("# mine", encoding="utf-8")

        # A DIFFERENT document already owns doc_name "report": different path,
        # different hash, same filename.
        HashRegistry(kb_dir / ".openkb" / "hashes.json").add(
            "other-hash",
            {"name": "report.md", "doc_name": "report", "type": "md",
             "path": "elsewhere/report.md"},
        )

        prepared = _PreparedAdd(
            file_path=tmp_path / "report.md",
            result=ConvertResult(
                raw_path=kb_dir / "raw" / "report.md",
                source_path=source_path,
                file_hash="myhash" + "0" * 59,
                doc_name="report",
            ),
        )

        with patch("openkb.cli.publish_staged_tree"), \
             patch("openkb.cli.asyncio.run"):
            outcome = _commit_prepared_add(prepared, kb_dir, "gpt-4o-mini")

        assert outcome == "failed"
        hashes = json.loads((kb_dir / ".openkb" / "hashes.json").read_text(encoding="utf-8"))
        # The pre-existing document is untouched.
        assert "other-hash" in hashes
        assert hashes["other-hash"]["path"] == "elsewhere/report.md"
        assert "myhash" + "0" * 59 not in hashes

    def test_add_directory_jobs1_stages_each_file(self, tmp_path):
        """jobs==1 must stage each file (pass a real staging_dir) instead of
        writing the live KB unlocked via staging_dir=None.
        """
        kb_dir = self._setup_kb(tmp_path)
        (kb_dir / ".openkb" / "config.yaml").write_text(
            "model: gpt-4o-mini\nfile_processing_jobs: 1\n",
            encoding="utf-8",
        )
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "a.md").write_text("# A", encoding="utf-8")
        (docs_dir / "b.md").write_text("# B", encoding="utf-8")

        from openkb.cli import _PreparedAdd

        seen_staging: list = []

        def fake_prepare(file_path, kb_dir_arg, staging_dir, doc_name=None):
            seen_staging.append(staging_dir)
            return _PreparedAdd(file_path=file_path, staging_dir=staging_dir)

        runner = CliRunner()
        with patch("openkb.cli._prepare_add_file", side_effect=fake_prepare), \
             patch("openkb.cli._commit_prepared_add", return_value="added"), \
             patch("openkb.cli._find_kb_dir", return_value=kb_dir):
            runner.invoke(cli, ["add", str(docs_dir)])

        assert len(seen_staging) == 2
        assert all(s is not None for s in seen_staging)  # regression: was None

    def test_commit_returns_added_when_post_commit_cleanup_fails(self, tmp_path):
        """Once the registry write lands, a failure in journal cleanup must
        NOT roll back the completed ingest (regression: the discard/log used
        to live inside the rollback try).
        """
        from openkb.cli import _PreparedAdd, _commit_prepared_add
        from openkb.converter import ConvertResult

        kb_dir = self._setup_kb(tmp_path)
        source_path = kb_dir / "wiki" / "sources" / "ok.md"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text("# OK", encoding="utf-8")
        prepared = _PreparedAdd(
            file_path=tmp_path / "ok.md",
            result=ConvertResult(
                raw_path=kb_dir / "raw" / "ok.md",
                source_path=source_path,
                file_hash="ok" + "0" * 62,
                doc_name="ok",
            ),
        )

        class FakeSnapshot:
            journal_path = kb_dir / ".openkb" / "journal" / "ok.json"

            def mark_committed(self):
                pass

            def rollback_best_effort(self):
                return None

            def discard_best_effort(self):
                return RuntimeError("cleanup failed")

        with patch("openkb.cli.snapshot_paths", return_value=FakeSnapshot()), \
             patch("openkb.cli.publish_staged_tree"), \
             patch("openkb.cli.asyncio.run"):
            outcome = _commit_prepared_add(prepared, kb_dir, "gpt-4o-mini")

        assert outcome == "added"  # regression: was "failed" (rolled back success)
        hashes = json.loads((kb_dir / ".openkb" / "hashes.json").read_text(encoding="utf-8"))
        assert "ok" + "0" * 62 in hashes  # registry write survived

    def test_commit_rolls_back_real_snapshot_on_compile_failure(self, tmp_path):
        """End-to-end rollback: a REAL snapshot + REAL publish, then a compile
        failure, must restore the KB to its pre-add state — published raw and
        source files removed, registry unchanged, no orphaned artifacts or
        journal. The FakeSnapshot-based test cannot exercise this transactional
        guarantee (the whole reason the feature exists).
        """
        import json as json_mod

        from openkb.cli import _PreparedAdd, _commit_prepared_add
        from openkb.converter import ConvertResult

        kb_dir = self._setup_kb(tmp_path)
        pre_hashes = (kb_dir / ".openkb" / "hashes.json").read_text(encoding="utf-8")

        # A staging dir holding the converted artifacts that publish_staged_tree
        # copies into the live KB before compile runs.
        staging = tmp_path / "staging"
        (staging / "raw").mkdir(parents=True)
        (staging / "wiki" / "sources").mkdir(parents=True)
        (staging / "raw" / "boom.md").write_text("# raw", encoding="utf-8")
        source_md = staging / "wiki" / "sources" / "boom.md"
        source_md.write_text("# converted", encoding="utf-8")

        prepared = _PreparedAdd(
            file_path=tmp_path / "boom.md",
            result=ConvertResult(
                raw_path=staging / "raw" / "boom.md",
                source_path=source_md,
                file_hash="boom" + "0" * 60,
                doc_name="boom",
            ),
            staging_dir=staging,
        )

        with patch("openkb.cli.asyncio.run", side_effect=RuntimeError("compile failed")), \
             patch("openkb.cli.time.sleep"):
            outcome = _commit_prepared_add(prepared, kb_dir, "gpt-4o-mini")

        assert outcome == "failed"
        # Published artifacts were rolled back (removed).
        assert not (kb_dir / "raw" / "boom.md").exists()
        assert not (kb_dir / "wiki" / "sources" / "boom.md").exists()
        assert not (kb_dir / "wiki" / "summaries" / "boom.md").exists()
        # Registry restored to pre-add state; no leaked boom entry.
        hashes = json_mod.loads(
            (kb_dir / ".openkb" / "hashes.json").read_text(encoding="utf-8")
        )
        assert hashes == json.loads(pre_hashes)
        assert "boom" + "0" * 60 not in hashes
        # No orphan journal/backup left behind; staging cleaned up.
        assert not any((kb_dir / ".openkb" / "journal").glob("*.json"))
        assert not staging.exists()

    def test_add_snapshot_rolls_back_pageindex_sqlite_sidecars(self, tmp_path):
        """Long-doc failures must not leave SQLite sidecars newer than pageindex.db."""
        from openkb.cli import _snapshot_add_paths
        from openkb.mutation import snapshot_paths

        kb_dir = self._setup_kb(tmp_path)
        openkb_dir = kb_dir / ".openkb"
        (openkb_dir / "pageindex.db").write_bytes(b"before")

        snapshot = snapshot_paths(
            kb_dir,
            _snapshot_add_paths(kb_dir, "long", None, None),
            operation="add",
            details={},
        )

        for suffix in ("-wal", "-shm", "-journal"):
            (openkb_dir / f"pageindex.db{suffix}").write_bytes(b"after")

        snapshot.rollback()
        snapshot.discard()

        assert (openkb_dir / "pageindex.db").read_bytes() == b"before"
        for suffix in ("-wal", "-shm", "-journal"):
            assert not (openkb_dir / f"pageindex.db{suffix}").exists()

    def test_add_single_file_stages_unless_file_already_in_raw(self, tmp_path):
        """stage=True (default for single-file add / chat) routes convert
        through an isolated staging dir; stage=False (watch / URL, file
        already in raw/) keeps convert's in-place path. The staging default
        closes the crash-orphan window for files that don't already live in
        raw/."""
        from openkb.cli import _PreparedAdd, _add_single_file_locked

        kb_dir = self._setup_kb(tmp_path)
        doc = tmp_path / "test.md"
        doc.write_text("# hi", encoding="utf-8")

        captured: list = []

        def fake_prepare(file_path, kb_dir_arg, staging_dir, doc_name=None):
            captured.append(staging_dir)
            return _PreparedAdd(file_path=file_path, staging_dir=staging_dir, outcome="skipped")

        with patch("openkb.cli._prepare_add_file", side_effect=fake_prepare), \
             patch("openkb.cli._commit_prepared_add", return_value="skipped"):
            _add_single_file_locked(doc, kb_dir)            # default stage=True
            _add_single_file_locked(doc, kb_dir, stage=False)

        assert captured[0] is not None   # staged by default → no live-KB write pre-snapshot
        assert captured[1] is None       # in-place for watch/URL (file already in raw/)

    def test_commit_conflict_guard_normalizes_unicode_filenames(self, tmp_path):
        """A legacy (path-less) entry whose name is stored NFC must match a
        file whose name the filesystem reports as NFD (macOS HFS+/APFS), so a
        same-document re-add is allowed instead of mis-reported as a conflict.
        The guard NFKC-normalizes both sides; a raw ``==`` would diverge."""
        from openkb.cli import _PreparedAdd, _commit_prepared_add
        from openkb.converter import ConvertResult
        from openkb.state import HashRegistry

        kb_dir = self._setup_kb(tmp_path)
        import unicodedata as _ud
        nfc_name = "r\u00e9sum\u00e9.pdf"            # NFC: é = U+00E9 (composed)
        nfd_name = _ud.normalize("NFD", nfc_name)     # NFD: e + U+0301 (decomposed)
        assert nfc_name != nfd_name                   # raw bytes differ

        source_path = kb_dir / "wiki" / "sources" / "resume.md"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text("# cv", encoding="utf-8")

        HashRegistry(kb_dir / ".openkb" / "hashes.json").add(
            "legacy-hash", {"name": nfc_name, "doc_name": "resume", "type": "pdf"}
        )

        prepared = _PreparedAdd(
            file_path=tmp_path / nfd_name,
            result=ConvertResult(
                raw_path=kb_dir / "raw" / "resume.pdf",
                source_path=source_path,
                file_hash="new" + "0" * 61,
                doc_name="resume",
            ),
        )

        with patch("openkb.cli.publish_staged_tree"), \
             patch("openkb.cli.asyncio.run"):
            outcome = _commit_prepared_add(prepared, kb_dir, "gpt-4o-mini")

        # NFC-vs-NFD is the same document, not a conflict → ingest proceeds.
        assert outcome == "added"

    def test_add_directory_jobs_gt1_runs_real_pipeline(self, tmp_path):
        """jobs>1 ThreadPoolExecutor 路径的端到端测试。

        其余 jobs>1 测试都 mock 了 _prepare_add_file 和 _commit_prepared_add，
        所以真正的并发分支——futures 按扫描顺序提交、_staging_dir_for 分配、
        prepared_outcomes.get(f) or futures[f].result() 回退、publish_staged_tree
        发布、registry 写入、staging 清理——从不被执行。这里用真实 prepare + 真实
        commit，只 mock LLM compile，让最复杂的新路径真正跑一遍。
        """
        kb_dir = self._setup_kb(tmp_path)
        (kb_dir / ".openkb" / "config.yaml").write_text(
            "model: gpt-4o-mini\nfile_processing_jobs: 3\n",
            encoding="utf-8",
        )
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        for letter in ("a", "b", "c"):
            (docs_dir / f"{letter}.md").write_text(f"# {letter}", encoding="utf-8")

        runner = CliRunner()
        with patch("openkb.cli._find_kb_dir", return_value=kb_dir), \
             patch("openkb.cli.asyncio.run"):
            result = runner.invoke(cli, ["add", str(docs_dir)])

        assert result.exception is None, result.output
        assert result.output.count("[OK]") == 3
        hashes = json.loads(
            (kb_dir / ".openkb" / "hashes.json").read_text(encoding="utf-8")
        )
        assert len(hashes) == 3
        assert {meta["doc_name"] for meta in hashes.values()} == {"a", "b", "c"}
        # Staging dirs cleaned up after each commit.
        staging = kb_dir / ".openkb" / "staging"
        if staging.exists():
            assert not any(p.name.startswith("add-") for p in staging.iterdir())
        # Source artifacts published from staging into the live KB.
        for letter in ("a", "b", "c"):
            assert (kb_dir / "wiki" / "sources" / f"{letter}.md").exists()

    def test_add_directory_interrupted_batch_does_not_leak_staging(self, tmp_path):
        """A failure aborting the batch mid-loop must not leak the staging dirs
        already created for files that never reach commit. Per-commit cleanup
        only runs inside _commit_prepared_add, and recovery only scans
        journal/ — so the batch itself must reap its own staging set.

        The fake commit mimics the real one's per-call staging cleanup (try/
        finally), so the only dir that should leak without a batch-level guard
        is the never-committed third file's.
        """
        kb_dir = self._setup_kb(tmp_path)
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        for name in ("a.md", "b.md", "c.md"):
            (docs_dir / name).write_text(f"# {name}", encoding="utf-8")

        from openkb.cli import _PreparedAdd, _cleanup_staging

        def fake_prepare(file_path, kb_dir_arg, staging_dir, doc_name=None):
            return _PreparedAdd(file_path=file_path, staging_dir=staging_dir)

        commit_calls = {"n": 0}

        def failing_commit(prepared, kb_dir_arg, model):
            commit_calls["n"] += 1
            try:
                if commit_calls["n"] == 2:
                    raise RuntimeError("mid-batch failure aborts the loop")
                return "added"
            finally:
                # mimic real _commit_prepared_add's per-call staging cleanup
                _cleanup_staging(prepared.staging_dir)

        runner = CliRunner()
        with patch("openkb.cli._prepare_add_file", side_effect=fake_prepare), \
             patch("openkb.cli._commit_prepared_add", side_effect=failing_commit), \
             patch("openkb.cli._find_kb_dir", return_value=kb_dir):
            result = runner.invoke(cli, ["add", str(docs_dir)])

        assert isinstance(result.exception, RuntimeError)
        staging_root = kb_dir / ".openkb" / "staging"
        leaked = [p for p in staging_root.glob("add-*")] if staging_root.exists() else []
        assert leaked == [], f"interrupted batch leaked staging dirs: {leaked}"

    def test_commit_path_hardlinks_concepts_backup(self, tmp_path):
        """The real add-commit path must snapshot wiki/concepts (and peers)
        via hardlinks, not copies. Spy on snapshot_paths during a real
        _commit_prepared_add and assert the concepts backup shares the live
        file's inode — the O(1) snapshot that keeps per-file batch cost from
        scaling with the corpus.
        """
        import openkb.mutation as mut
        from openkb.cli import _PreparedAdd, _commit_prepared_add
        from openkb.converter import ConvertResult

        kb_dir = self._setup_kb(tmp_path)
        concepts_file = kb_dir / "wiki" / "concepts" / "keep.md"
        concepts_file.write_text("keep", encoding="utf-8")
        live_inode = concepts_file.stat().st_ino

        captured = {}
        real_snapshot = mut.snapshot_paths

        def spy(kb_dir_arg, paths, *, operation, details=None, hardlink_dirs=None):
            snap = real_snapshot(
                kb_dir_arg, paths,
                operation=operation, details=details, hardlink_dirs=hardlink_dirs,
            )
            backup_concepts = snap.backup_dir / "wiki" / "concepts" / "keep.md"
            captured["hardlinked"] = (
                backup_concepts.exists() and backup_concepts.stat().st_ino == live_inode
            )
            return snap

        staging = tmp_path / "staging"
        (staging / "raw").mkdir(parents=True)
        (staging / "wiki" / "sources").mkdir(parents=True)
        (staging / "raw" / "doc.md").write_text("# raw", encoding="utf-8")
        source_md = staging / "wiki" / "sources" / "doc.md"
        source_md.write_text("# converted", encoding="utf-8")
        prepared = _PreparedAdd(
            file_path=tmp_path / "doc.md",
            result=ConvertResult(
                raw_path=staging / "raw" / "doc.md",
                source_path=source_md,
                file_hash="d" + "0" * 63,
                doc_name="doc",
            ),
            staging_dir=staging,
        )

        with patch("openkb.cli.asyncio.run"), \
             patch("openkb.cli.snapshot_paths", side_effect=spy):
            _commit_prepared_add(prepared, kb_dir, "gpt-4o-mini")

        assert captured.get("hardlinked") is True, (
            "real add-commit path did not hardlink the concepts backup"
        )

    def test_add_from_pageindex_cloud_dispatches(self, tmp_path):
        kb_dir = self._setup_kb(tmp_path)
        runner = CliRunner()
        with patch("openkb.cli.import_from_pageindex_cloud", return_value="added") as mock_imp, \
             patch("openkb.cli._find_kb_dir", return_value=kb_dir):
            result = runner.invoke(cli, ["add", "--from-pageindex-cloud", "doc-123"])
            mock_imp.assert_called_once_with("doc-123", kb_dir)
            assert result.exit_code == 0  # success → exit 0

    def test_add_cloud_failure_exits_nonzero(self, tmp_path):
        kb_dir = self._setup_kb(tmp_path)
        runner = CliRunner()
        with patch("openkb.cli.import_from_pageindex_cloud", return_value="failed"), \
             patch("openkb.cli._find_kb_dir", return_value=kb_dir):
            result = runner.invoke(cli, ["add", "--from-pageindex-cloud", "doc-x"])
            assert result.exit_code == 1  # failed import must not exit 0

    def test_add_rejects_path_and_cloud_together(self, tmp_path):
        kb_dir = self._setup_kb(tmp_path)
        doc = tmp_path / "test.md"
        doc.write_text("# Hi")
        runner = CliRunner()
        with patch("openkb.cli.import_from_pageindex_cloud") as mock_imp, \
             patch("openkb.cli.add_single_file") as mock_add, \
             patch("openkb.cli._find_kb_dir", return_value=kb_dir):
            result = runner.invoke(cli, ["add", str(doc), "--from-pageindex-cloud", "doc-1"])
            assert "not both" in result.output
            mock_imp.assert_not_called()
            mock_add.assert_not_called()

    def test_add_requires_path_or_cloud(self, tmp_path):
        kb_dir = self._setup_kb(tmp_path)
        runner = CliRunner()
        with patch("openkb.cli._find_kb_dir", return_value=kb_dir):
            result = runner.invoke(cli, ["add"])
            assert "Provide a PATH" in result.output

    def test_add_cloud_import_drains_pending_journal_under_lock(self, tmp_path):
        """The cloud path acquires the mutation lock (the outer @_with_kb_lock
        was removed for directory batching), so a pending journal left by a
        crashed prior run is drained before the import proceeds — not left to
        race with it. Regression guard: removing the _kb_mutation_lock wrap
        would let the seeded journal (and its mutated live file) survive."""
        from openkb.mutation import snapshot_paths

        kb_dir = self._setup_kb(tmp_path)
        # Seed an unresolved "active" journal exactly as a crashed prior
        # mutation would leave one: snapshot a live file, mutate it, then
        # never mark_committed / discard.
        live = kb_dir / "wiki" / "index.md"
        live.write_text("before-crash", encoding="utf-8")
        snap = snapshot_paths(kb_dir, [live], operation="seed", details={})
        live.write_text("mutated-by-crashed-run", encoding="utf-8")
        assert snap.journal_path.exists()  # genuinely pending

        with patch("openkb.cli.import_from_pageindex_cloud", return_value="added"), \
             patch("openkb.cli._find_kb_dir", return_value=kb_dir):
            result = CliRunner().invoke(cli, ["add", "--from-pageindex-cloud", "doc-1"])

        assert result.exit_code == 0, result.output
        # The lock drained the pending journal on acquire: live content rolled
        # back to pre-crash, and no journal remains.
        assert live.read_text(encoding="utf-8") == "before-crash"
        assert not any((kb_dir / ".openkb" / "journal").glob("*.json"))


class TestImportFromPageindexCloud:
    def _setup_kb(self, tmp_path):
        (tmp_path / "raw").mkdir()
        (tmp_path / "wiki" / "sources" / "images").mkdir(parents=True)
        (tmp_path / "wiki" / "summaries").mkdir(parents=True)
        (tmp_path / "wiki" / "concepts").mkdir(parents=True)
        (tmp_path / "wiki" / "reports").mkdir(parents=True)
        openkb_dir = tmp_path / ".openkb"
        openkb_dir.mkdir()
        (openkb_dir / "config.yaml").write_text("model: gpt-4o-mini\n")
        (openkb_dir / "hashes.json").write_text(json.dumps({}))
        return tmp_path

    def test_registers_rawless_cloud_entry(self, tmp_path):
        import hashlib
        from openkb.cli import import_from_pageindex_cloud
        from openkb.indexer import CloudImportResult
        from openkb.state import HashRegistry

        kb_dir = self._setup_kb(tmp_path)
        result = CloudImportResult(
            doc_id="cloud-1", doc_name="Cloud-Paper", name="Cloud Paper.pdf",
            description="desc",
        )

        with patch("openkb.cli.import_cloud_document", return_value=result), \
             patch("openkb.cli.compile_long_doc", return_value=None) as mock_compile, \
             patch("openkb.cli._setup_llm_key"):
            outcome = import_from_pageindex_cloud("cloud-1", kb_dir)

        assert outcome == "added"
        mock_compile.assert_called_once()
        registry = HashRegistry(kb_dir / ".openkb" / "hashes.json")
        synthetic = hashlib.sha256(b"pageindex-cloud:cloud-1").hexdigest()
        meta = registry.get(synthetic)
        assert meta is not None
        assert meta["type"] == "pageindex_cloud"
        assert meta["origin"] == "cloud"
        assert meta["doc_id"] == "cloud-1"
        assert meta["path"] == "pageindex-cloud:cloud-1"
        assert "raw_path" not in meta

    def test_second_import_is_skipped(self, tmp_path):
        from openkb.cli import import_from_pageindex_cloud
        from openkb.indexer import CloudImportResult

        kb_dir = self._setup_kb(tmp_path)
        result = CloudImportResult(
            doc_id="cloud-1", doc_name="Cloud-Paper", name="Cloud Paper.pdf",
            description="desc",
        )

        with patch("openkb.cli.import_cloud_document", return_value=result) as mock_import, \
             patch("openkb.cli.compile_long_doc", return_value=None), \
             patch("openkb.cli._setup_llm_key"):
            import_from_pageindex_cloud("cloud-1", kb_dir)
            second = import_from_pageindex_cloud("cloud-1", kb_dir)

        assert second == "skipped"
        assert mock_import.call_count == 1  # not fetched again

    def test_import_failure_returns_failed_and_registers_nothing(self, tmp_path):
        from openkb.cli import import_from_pageindex_cloud
        from openkb.state import HashRegistry

        kb_dir = self._setup_kb(tmp_path)
        with patch("openkb.cli.import_cloud_document", side_effect=RuntimeError("boom")), \
             patch("openkb.cli._setup_llm_key"):
            outcome = import_from_pageindex_cloud("cloud-9", kb_dir)

        assert outcome == "failed"
        registry = HashRegistry(kb_dir / ".openkb" / "hashes.json")
        assert registry.all_entries() == {}

    def test_compile_failure_cleans_up_orphan_artifacts(self, tmp_path):
        """If import_cloud_document writes artifacts but compile fails twice, the
        mutation journal rolls the wiki trees back to their pre-import state — no
        summary/source orphans (`openkb remove` couldn't reach them otherwise),
        no registry entry (so a retry isn't skipped), and no journal left behind.
        The artifacts are written inside the import mock so they land after the
        snapshot (otherwise rollback would faithfully restore them)."""
        from openkb.cli import import_from_pageindex_cloud
        from openkb.indexer import CloudImportResult
        from openkb.state import HashRegistry

        kb_dir = self._setup_kb(tmp_path)
        (kb_dir / "wiki" / "entities").mkdir(parents=True, exist_ok=True)
        (kb_dir / "wiki" / "index.md").write_text("# Index\n", encoding="utf-8")
        doc_name = "Cloud-Paper"
        result = CloudImportResult(
            doc_id="cloud-1", doc_name=doc_name, name="Cloud Paper.pdf", description="d",
        )

        def write_artifacts(doc_id, kb, path_key):
            # import_cloud_document writes summary + source before returning;
            # written inside the call so the pre-import snapshot doesn't capture
            # them and rollback removes them.
            (kb / "wiki" / "summaries" / f"{doc_name}.md").write_text("---\n---\n# s\n")
            (kb / "wiki" / "sources" / f"{doc_name}.json").write_text("[]")
            return result

        with patch("openkb.cli.import_cloud_document", side_effect=write_artifacts), \
             patch("openkb.cli.compile_long_doc", side_effect=RuntimeError("boom")), \
             patch("openkb.cli.time.sleep"), \
             patch("openkb.cli._setup_llm_key"):
            outcome = import_from_pageindex_cloud("cloud-1", kb_dir)

        assert outcome == "failed"
        # Artifacts rolled back via journal — no orphans `remove` couldn't reach.
        assert not (kb_dir / "wiki" / "summaries" / f"{doc_name}.md").exists()
        assert not (kb_dir / "wiki" / "sources" / f"{doc_name}.json").exists()
        # Nothing registered → a retry is not skipped.
        assert HashRegistry(kb_dir / ".openkb" / "hashes.json").all_entries() == {}
        # Journal rolled back + discarded, nothing active left behind.
        assert not any((kb_dir / ".openkb" / "journal").glob("*.json"))

    def test_import_cloud_failure_rolls_back_partial_writes(self, tmp_path):
        """A crash mid-import_cloud_document — it writes live artifacts, then
        raises — must leave no orphan. The mutation journal snapshots the wiki
        trees before the import and rolls the partial writes back on failure,
        restoring the KB and leaving no active journal. (Pre-journal, this
        exception path returned "failed" with no cleanup, stranding artifacts.)"""
        from openkb.cli import import_from_pageindex_cloud

        kb_dir = self._setup_kb(tmp_path)
        pre_hashes = (kb_dir / ".openkb" / "hashes.json").read_text(encoding="utf-8")

        def write_then_fail(doc_id, kb, path_key):
            # import_cloud_document writes summary + source before it can fail.
            (kb / "wiki" / "summaries" / "Cloud.md").write_text("---\n---\n# s\n")
            (kb / "wiki" / "sources" / "Cloud.json").write_text("[]")
            raise RuntimeError("cloud fetch blew up")

        with patch("openkb.cli.import_cloud_document", side_effect=write_then_fail), \
             patch("openkb.cli._setup_llm_key"):
            outcome = import_from_pageindex_cloud("cloud-1", kb_dir)

        assert outcome == "failed"
        # Partial writes rolled back via journal — no orphaned artifacts.
        assert not (kb_dir / "wiki" / "summaries" / "Cloud.md").exists()
        assert not (kb_dir / "wiki" / "sources" / "Cloud.json").exists()
        # Registry untouched.
        assert (kb_dir / ".openkb" / "hashes.json").read_text(encoding="utf-8") == pre_hashes
        # No active journal left stranded for a later run to clean up.
        assert not any((kb_dir / ".openkb" / "journal").glob("*.json"))

    def test_cloud_import_survives_post_commit_log_failure(self, tmp_path):
        """A failure appending to wiki/log.md after the registry write must not
        turn a successful, already-registered import into an uncaught error —
        the log append is post-commit and best-effort, mirroring the add path."""
        import hashlib

        from openkb.cli import import_from_pageindex_cloud
        from openkb.indexer import CloudImportResult
        from openkb.state import HashRegistry

        kb_dir = self._setup_kb(tmp_path)
        result = CloudImportResult(
            doc_id="cloud-1", doc_name="Cloud", name="Cloud.pdf", description="d",
        )

        with patch("openkb.cli.import_cloud_document", return_value=result), \
             patch("openkb.cli.compile_long_doc", return_value=None), \
             patch("openkb.cli._setup_llm_key"), \
             patch("openkb.cli.append_log", side_effect=OSError("disk full")):
            outcome = import_from_pageindex_cloud("cloud-1", kb_dir)

        assert outcome == "added"  # registered successfully, log failure swallowed
        synthetic = hashlib.sha256(b"pageindex-cloud:cloud-1").hexdigest()
        assert HashRegistry(kb_dir / ".openkb" / "hashes.json").get(synthetic) is not None

    def test_cloud_import_success_leaves_no_journal(self, tmp_path):
        """A successful cloud import marks the journal committed and discards it,
        leaving no journal behind. A stale 'committed' journal would be harmless
        (the next drain discards it), but the success path must not leak one."""
        import hashlib

        from openkb.cli import import_from_pageindex_cloud
        from openkb.indexer import CloudImportResult
        from openkb.state import HashRegistry

        kb_dir = self._setup_kb(tmp_path)
        result = CloudImportResult(
            doc_id="cloud-1", doc_name="Cloud", name="Cloud.pdf", description="d",
        )

        with patch("openkb.cli.import_cloud_document", return_value=result), \
             patch("openkb.cli.compile_long_doc", return_value=None), \
             patch("openkb.cli._setup_llm_key"):
            outcome = import_from_pageindex_cloud("cloud-1", kb_dir)

        assert outcome == "added"
        assert not any((kb_dir / ".openkb" / "journal").glob("*.json"))
        synthetic = hashlib.sha256(b"pageindex-cloud:cloud-1").hexdigest()
        assert HashRegistry(kb_dir / ".openkb" / "hashes.json").get(synthetic) is not None
