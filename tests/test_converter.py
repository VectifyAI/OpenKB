"""Tests for openkb.converter."""
from __future__ import annotations

from unittest.mock import MagicMock, patch


from openkb.converter import convert_document, get_pdf_page_count


# ---------------------------------------------------------------------------
# get_pdf_page_count
# ---------------------------------------------------------------------------


class TestGetPdfPageCount:
    def test_returns_page_count(self, tmp_path):
        """Mock pymupdf to return a doc with 5 pages."""
        fake_doc = MagicMock()
        fake_doc.page_count = 5
        fake_doc.__enter__ = MagicMock(return_value=fake_doc)
        fake_doc.__exit__ = MagicMock(return_value=False)
        with patch("openkb.converter.pymupdf.open", return_value=fake_doc):
            count = get_pdf_page_count(tmp_path / "fake.pdf")
        assert count == 5


# ---------------------------------------------------------------------------
# convert_document — .md input
# ---------------------------------------------------------------------------


class TestConvertDocumentMarkdown:
    def test_md_file_copied_to_wiki_sources(self, kb_dir):
        """A .md file is read and saved under wiki/sources/."""
        src = kb_dir / "raw" / "notes.md"
        src.write_text("# Notes\n\nSome content here.", encoding="utf-8")

        result = convert_document(src, kb_dir)

        assert result.skipped is False
        assert result.is_long_doc is False
        assert result.source_path is not None
        assert result.source_path.exists()
        assert result.source_path.read_text(encoding="utf-8").startswith("# Notes")

    def test_md_duplicate_skipped(self, kb_dir):
        """Second call with same file returns skipped=True when hash is registered."""
        from openkb.state import HashRegistry

        src = kb_dir / "raw" / "notes.md"
        src.write_text("# Notes\n\nSome content here.", encoding="utf-8")

        result1 = convert_document(src, kb_dir)  # first call
        # Simulate CLI registering the hash after successful compilation
        registry = HashRegistry(kb_dir / ".openkb" / "hashes.json")
        registry.add(result1.file_hash, {"name": src.name, "type": "md"})

        result2 = convert_document(src, kb_dir)  # second call
        assert result2.skipped is True
        assert result2.source_path is None
        assert result2.raw_path is None

    def test_md_raw_file_copied(self, kb_dir):
        """The original file should also be copied to raw/."""
        src = kb_dir / "input" / "notes.md"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text("# Notes\n", encoding="utf-8")

        result = convert_document(src, kb_dir)

        assert result.raw_path is not None
        assert result.raw_path.exists()


# ---------------------------------------------------------------------------
# convert_document — PDF short doc
# ---------------------------------------------------------------------------


class TestConvertDocumentPdfShort:
    def test_short_pdf_converted_via_pymupdf(self, kb_dir, tmp_path):
        """PDF under threshold is converted with pymupdf (convert_pdf_with_images)."""
        src = tmp_path / "short.pdf"
        src.write_bytes(b"%PDF-1.4 fake content")

        with (
            patch("openkb.converter.pymupdf.open") as mock_mu,
            patch("openkb.converter.convert_pdf_with_images", return_value="# Short PDF\n\nConverted.") as mock_cpwi,
        ):
            fake_doc = MagicMock()
            fake_doc.page_count = 5  # below default threshold of 20
            fake_doc.__enter__ = MagicMock(return_value=fake_doc)
            fake_doc.__exit__ = MagicMock(return_value=False)
            mock_mu.return_value = fake_doc

            result = convert_document(src, kb_dir)

        mock_cpwi.assert_called_once()
        assert result.skipped is False
        assert result.is_long_doc is False
        assert result.source_path is not None
        assert result.source_path.exists()


# ---------------------------------------------------------------------------
# convert_document — PDF long doc
# ---------------------------------------------------------------------------


class TestConvertDocumentPdfLong:
    def test_long_pdf_returns_is_long_doc(self, kb_dir, tmp_path):
        """PDF >= threshold pages returns is_long_doc=True, source_path=None."""
        src = tmp_path / "long.pdf"
        src.write_bytes(b"%PDF-1.4 fake long content")

        with (
            patch("openkb.converter.pymupdf.open") as mock_mu,
        ):
            fake_doc = MagicMock()
            fake_doc.page_count = 200  # above threshold
            fake_doc.__enter__ = MagicMock(return_value=fake_doc)
            fake_doc.__exit__ = MagicMock(return_value=False)
            mock_mu.return_value = fake_doc

            result = convert_document(src, kb_dir)

        assert result.is_long_doc is True
        assert result.source_path is None
        assert result.skipped is False
        assert result.raw_path is not None


# ---------------------------------------------------------------------------
# _registry_path
# ---------------------------------------------------------------------------


class TestRegistryPath:
    def test_inside_kb_is_relative_posix(self, kb_dir):
        from openkb.converter import _registry_path
        p = kb_dir / "raw" / "sub" / "doc.md"
        assert _registry_path(p, kb_dir) == "raw/sub/doc.md"

    def test_outside_kb_is_absolute_posix(self, kb_dir, tmp_path_factory):
        from openkb.converter import _registry_path
        outside = tmp_path_factory.mktemp("elsewhere") / "doc.md"
        result = _registry_path(outside, kb_dir)
        assert result == outside.resolve().as_posix()
        assert result.startswith("/")


# ---------------------------------------------------------------------------
# resolve_doc_name
# ---------------------------------------------------------------------------


class TestResolveDocName:
    def _registry(self, kb_dir):
        from openkb.state import HashRegistry
        return HashRegistry(kb_dir / ".openkb" / "hashes.json")

    def test_unique_name_stays_clean(self, kb_dir):
        from openkb.converter import resolve_doc_name
        src = kb_dir / "raw" / "report.md"
        src.write_text("x", encoding="utf-8")
        assert resolve_doc_name(src, kb_dir, self._registry(kb_dir)) == "report"

    def test_known_path_reuses_stored_doc_name(self, kb_dir):
        from openkb.converter import resolve_doc_name
        reg = self._registry(kb_dir)
        reg.add("h1", {"name": "report.md", "doc_name": "report-x1",
                       "path": "inputs/report.md"})
        src = kb_dir / "inputs" / "report.md"
        src.parent.mkdir(parents=True)
        src.write_text("edited", encoding="utf-8")
        assert resolve_doc_name(src, kb_dir, reg) == "report-x1"

    def test_collision_gets_deterministic_suffix(self, kb_dir):
        from openkb.converter import _registry_path, resolve_doc_name
        import hashlib
        reg = self._registry(kb_dir)
        # "report" already taken by a different, path-indexed source
        reg.add("h1", {"name": "report.md", "doc_name": "report",
                       "path": "inputs/first/report.md"})
        src = kb_dir / "inputs" / "second" / "report.md"
        src.parent.mkdir(parents=True)
        src.write_text("y", encoding="utf-8")
        expected_suffix = hashlib.sha256(
            _registry_path(src, kb_dir).encode("utf-8")
        ).hexdigest()[:8]
        assert resolve_doc_name(src, kb_dir, reg) == f"report-{expected_suffix}"

    def test_collision_with_on_disk_source_file(self, kb_dir):
        # Pre-upgrade docs may exist on disk without any registry entry.
        from openkb.converter import resolve_doc_name
        (kb_dir / "wiki" / "sources" / "report.md").write_text("old", encoding="utf-8")
        src = kb_dir / "raw" / "report.md"
        src.write_text("new other doc", encoding="utf-8")
        # With an empty registry there is no legacy entry to claim the name,
        # so the on-disk file makes this a genuine collision: suffix expected.
        name = resolve_doc_name(src, kb_dir, self._registry(kb_dir))
        assert name.startswith("report-") and len(name) == len("report-") + 8

    def test_legacy_entry_is_reused_and_backfilled(self, kb_dir):
        from openkb.converter import _registry_path, resolve_doc_name
        reg = self._registry(kb_dir)
        reg.add("h_old", {"name": "notes.md", "doc_name": "notes", "type": "md"})
        src = kb_dir / "raw" / "notes.md"
        src.write_text("edited content", encoding="utf-8")
        assert resolve_doc_name(src, kb_dir, reg) == "notes"
        # path backfilled onto the legacy entry
        assert reg.get("h_old")["path"] == _registry_path(src, kb_dir)

    def test_stem_is_sanitized(self, kb_dir):
        from openkb.converter import resolve_doc_name
        src = kb_dir / "raw" / "my report (final).md"
        src.write_text("x", encoding="utf-8")
        assert resolve_doc_name(src, kb_dir, self._registry(kb_dir)) == "my-report-final"

    def test_same_stem_different_extension_collides(self, kb_dir):
        # report.pdf vs an existing "report" (from report.md) — extension
        # does not disambiguate; the second source gets a suffix.
        from openkb.converter import resolve_doc_name
        reg = self._registry(kb_dir)
        reg.add("h1", {"name": "report.md", "doc_name": "report",
                       "path": "inputs/report.md"})
        src = kb_dir / "raw" / "report.pdf"
        src.write_bytes(b"%PDF-1.4 fake")
        name = resolve_doc_name(src, kb_dir, reg)
        assert name.startswith("report-") and name != "report"
