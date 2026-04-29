"""Tests for openkb.converter."""
from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from openkb.converter import ConvertResult, _make_doc_name, convert_document, get_pdf_page_count


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
        assert result.doc_name == _make_doc_name(src, kb_dir)
        assert result.source_path is not None
        assert result.source_path.name == f"{result.doc_name}.md"
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
        assert result.raw_path.name == f"{result.doc_name}.md"
        assert result.raw_path.exists()

    def test_same_filename_different_paths_get_distinct_outputs(self, kb_dir):
        """Same basename in different folders must not overwrite outputs."""
        first_dir = kb_dir / "inputs" / "first"
        second_dir = kb_dir / "inputs" / "second"
        first_dir.mkdir(parents=True)
        second_dir.mkdir(parents=True)
        first = first_dir / "report.md"
        second = second_dir / "report.md"
        first.write_text("# First\n\nAlpha content.", encoding="utf-8")
        second.write_text("# Second\n\nBeta content.", encoding="utf-8")

        first_result = convert_document(first, kb_dir)
        second_result = convert_document(second, kb_dir)

        assert first_result.doc_name != second_result.doc_name
        assert first_result.source_path != second_result.source_path
        assert first_result.raw_path != second_result.raw_path
        assert first_result.source_path.read_text(encoding="utf-8").startswith("# First")
        assert second_result.source_path.read_text(encoding="utf-8").startswith("# Second")

    def test_raw_copy_keeps_doc_name_when_content_changes(self, kb_dir):
        """A watched raw copy keeps the original document identity across edits."""
        from openkb.state import HashRegistry

        src = kb_dir / "inputs" / "notes.md"
        src.parent.mkdir(parents=True)
        src.write_text("# Notes\n\nOld content.", encoding="utf-8")

        first_result = convert_document(src, kb_dir)
        registry = HashRegistry(kb_dir / ".openkb" / "hashes.json")
        registry.add(
            first_result.file_hash,
            {
                "name": src.name,
                "doc_name": first_result.doc_name,
                "path": "inputs/notes.md",
                "raw_path": f"raw/{first_result.doc_name}.md",
                "source_path": f"wiki/sources/{first_result.doc_name}.md",
                "type": "md",
            },
        )

        first_result.raw_path.write_text("# Notes\n\nNew content.", encoding="utf-8")
        second_result = convert_document(first_result.raw_path, kb_dir)

        assert second_result.skipped is False
        assert second_result.doc_name == first_result.doc_name
        assert second_result.source_path == first_result.source_path
        assert second_result.source_path.read_text(encoding="utf-8").startswith("# Notes\n\nNew content.")


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

    def test_same_stem_different_extensions_get_distinct_outputs(self, kb_dir, tmp_path):
        """report.md and report.pdf must not share the same internal name."""
        md_src = tmp_path / "report.md"
        pdf_src = tmp_path / "report.pdf"
        md_src.write_text("# Markdown report", encoding="utf-8")
        pdf_src.write_bytes(b"%PDF-1.4 fake content")

        md_result = convert_document(md_src, kb_dir)
        with (
            patch("openkb.converter.pymupdf.open") as mock_mu,
            patch("openkb.converter.convert_pdf_with_images", return_value="# PDF report"),
        ):
            fake_doc = MagicMock()
            fake_doc.page_count = 5
            fake_doc.__enter__ = MagicMock(return_value=fake_doc)
            fake_doc.__exit__ = MagicMock(return_value=False)
            mock_mu.return_value = fake_doc
            pdf_result = convert_document(pdf_src, kb_dir)

        assert md_result.doc_name != pdf_result.doc_name
        assert md_result.source_path != pdf_result.source_path
        assert md_result.raw_path != pdf_result.raw_path


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
