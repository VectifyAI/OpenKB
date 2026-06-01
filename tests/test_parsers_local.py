"""Tests for LocalParser — preserves legacy md/pdf/markitdown behavior."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from openkb.parsers.local import LocalParser
from openkb.parsers.base import ParseResult


def test_supports_all_known_extensions():
    p = LocalParser()
    for ext in [".pdf", ".md", ".markdown", ".docx", ".pptx", ".xlsx", ".html", ".txt", ".csv"]:
        assert p.supports(ext) is True


def test_parse_md_reads_text(tmp_path):
    src = tmp_path / "n.md"
    src.write_text("# Title\n\nbody", encoding="utf-8")
    images_dir = tmp_path / "img" / "n"
    p = LocalParser(doc_name="n", images_dir=images_dir, source_dir=tmp_path)
    result = p.parse(src)
    assert isinstance(result, ParseResult)
    assert result.markdown.startswith("# Title")


def test_parse_pdf_delegates_to_convert_pdf_with_images(tmp_path):
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"%PDF-1.4 fake")
    images_dir = tmp_path / "img" / "doc"
    with patch("openkb.parsers.local.convert_pdf_with_images", return_value="PDF MD") as m:
        p = LocalParser(doc_name="doc", images_dir=images_dir, source_dir=tmp_path)
        result = p.parse(src)
    m.assert_called_once_with(src, "doc", images_dir)
    assert result.markdown == "PDF MD"


def test_parse_other_uses_markitdown_and_extracts_base64(tmp_path):
    src = tmp_path / "deck.pptx"
    src.write_bytes(b"PK fake")
    images_dir = tmp_path / "img" / "deck"
    with patch("openkb.parsers.local.MarkItDown") as fake_mid, \
         patch("openkb.parsers.local.extract_base64_images", return_value="CLEANED") as ex:
        fake_mid.return_value.convert.return_value.text_content = "MARKITDOWN MD"
        p = LocalParser(doc_name="deck", images_dir=images_dir, source_dir=tmp_path)
        result = p.parse(src)
    ex.assert_called_once_with("MARKITDOWN MD", "deck", images_dir)
    assert result.markdown == "CLEANED"
