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
             patch("openkb.cli.convert_document", return_value=mock_result), \
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
             patch("openkb.cli.convert_document", return_value=mock_result), \
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
