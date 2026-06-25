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

        runner = CliRunner()
        with patch("openkb.cli.add_single_file") as mock_add, \
             patch("openkb.cli._find_kb_dir", return_value=kb_dir):
            runner.invoke(cli, ["add", str(docs_dir)])
            # Should be called for .md and .txt but not .xyz
            assert mock_add.call_count == 2
            called_names = {call.args[0].name for call in mock_add.call_args_list}
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
        """If import succeeds (artifacts written) but compile fails twice, the
        summary/source artifacts are cleaned up — no registry entry exists, so
        `openkb remove` couldn't reach them otherwise — and nothing is registered
        (so a retry isn't skipped)."""
        from openkb.cli import import_from_pageindex_cloud
        from openkb.indexer import CloudImportResult
        from openkb.state import HashRegistry

        kb_dir = self._setup_kb(tmp_path)
        (kb_dir / "wiki" / "entities").mkdir(parents=True, exist_ok=True)
        (kb_dir / "wiki" / "index.md").write_text("# Index\n", encoding="utf-8")
        doc_name = "Cloud-Paper"
        # Simulate the artifacts import_cloud_document writes before compile.
        (kb_dir / "wiki" / "summaries" / f"{doc_name}.md").write_text("---\n---\n# s\n")
        (kb_dir / "wiki" / "sources" / f"{doc_name}.json").write_text("[]")
        result = CloudImportResult(
            doc_id="cloud-1", doc_name=doc_name, name="Cloud Paper.pdf", description="d",
        )

        with patch("openkb.cli.import_cloud_document", return_value=result), \
             patch("openkb.cli.compile_long_doc", side_effect=RuntimeError("boom")), \
             patch("openkb.cli.time.sleep"), \
             patch("openkb.cli._setup_llm_key"):
            outcome = import_from_pageindex_cloud("cloud-1", kb_dir)

        assert outcome == "failed"
        # Orphan artifacts cleaned up (would be unreachable by `remove` otherwise).
        assert not (kb_dir / "wiki" / "summaries" / f"{doc_name}.md").exists()
        assert not (kb_dir / "wiki" / "sources" / f"{doc_name}.json").exists()
        # Nothing registered → a retry is not skipped.
        assert HashRegistry(kb_dir / ".openkb" / "hashes.json").all_entries() == {}
