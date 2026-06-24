"""Tests for shared PDF page extraction."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from openkb.pdf_extractor import (
    LocalPdfExtractor,
    PageContent,
    normalize_page_content,
    pages_to_json,
    pages_to_markdown,
    resolve_pdf_extractor,
)


def test_normalize_page_content_accepts_common_shapes():
    pages = normalize_page_content([
        {"page_number": "2", "markdown": "  Page two  ", "images": [{"path": "sources/images/doc/a.png"}]},
        {"page_num": 3, "text": "Page three", "images": "bad"},
        " page four ",
    ])

    assert pages == [
        PageContent(page=2, content="Page two", images=[{"path": "sources/images/doc/a.png"}]),
        PageContent(page=3, content="Page three", images=[]),
        PageContent(page=3, content="page four", images=[]),
    ]


def test_pages_to_markdown_joins_page_content():
    pages = [
        PageContent(page=1, content="Page one", images=[]),
        PageContent(page=2, content="Page two", images=[]),
    ]

    assert pages_to_markdown(pages) == "Page one\n\nPage two"


def test_pages_to_markdown_renders_image_metadata_when_not_in_content():
    pages = [
        PageContent(
            page=1,
            content="Page one",
            images=[{"path": "sources/images/doc/p1.png"}],
        ),
        PageContent(
            page=2,
            content="![image](sources/images/doc/p2.png)",
            images=[{"path": "sources/images/doc/p2.png"}],
        ),
    ]

    assert pages_to_markdown(pages) == (
        "Page one\n\n![image](sources/images/doc/p1.png)\n\n"
        "![image](sources/images/doc/p2.png)"
    )


def test_pages_to_json_preserves_openkb_shape():
    pages = [
        PageContent(page=1, content="Page one", images=[{"path": "sources/images/doc/p1.png"}]),
    ]

    assert pages_to_json(pages) == [
        {"page": 1, "content": "Page one", "images": [{"path": "sources/images/doc/p1.png"}]},
    ]


def test_local_extractor_uses_existing_pymupdf_page_converter(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    images_dir = tmp_path / "images"

    with patch("openkb.images.convert_pdf_to_pages", return_value=[
        {"page": 1, "content": "Page one", "images": []},
    ]) as convert:
        pages = LocalPdfExtractor().parse_pages(pdf, "paper", images_dir)

    convert.assert_called_once_with(pdf, "paper", images_dir)
    assert pages == [PageContent(page=1, content="Page one", images=[])]


def test_resolve_pdf_extractor_defaults_to_local():
    assert isinstance(resolve_pdf_extractor({}), LocalPdfExtractor)


def test_resolve_pdf_extractor_accepts_string_and_object_local_config():
    assert isinstance(resolve_pdf_extractor({"pdf_parser": "local"}), LocalPdfExtractor)
    assert isinstance(resolve_pdf_extractor({"pdf_parser": {"provider": "local"}}), LocalPdfExtractor)


def test_resolve_pdf_extractor_rejects_unknown_provider():
    with pytest.raises(ValueError, match="Unsupported pdf_parser provider: mineru"):
        resolve_pdf_extractor({"pdf_parser": "mineru"})
