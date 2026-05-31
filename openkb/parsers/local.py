from __future__ import annotations

from pathlib import Path

from markitdown import MarkItDown

from openkb.images import (
    convert_pdf_with_images,
    copy_relative_images,
    extract_base64_images,
)
from openkb.parsers.base import ParseResult, Parser

_LOCAL_EXTENSIONS = {
    ".pdf", ".md", ".markdown", ".docx", ".pptx", ".xlsx", ".xls",
    ".html", ".htm", ".txt", ".csv",
}


class LocalParser(Parser):
    """Default parser: pymupdf for PDF, markitdown for office/html, direct read for md."""

    name = "local"

    def __init__(self, doc_name: str = "", images_dir: Path | None = None,
                 source_dir: Path | None = None):
        self.doc_name = doc_name
        self.images_dir = images_dir
        self.source_dir = source_dir

    def supports(self, suffix: str) -> bool:
        return suffix.lower() in _LOCAL_EXTENSIONS

    def parse(self, src: Path) -> ParseResult:
        suffix = src.suffix.lower()
        if suffix in {".md", ".markdown"}:
            markdown = src.read_text(encoding="utf-8")
            markdown = copy_relative_images(
                markdown, src.parent, self.doc_name, self.images_dir
            )
        elif suffix == ".pdf":
            markdown = convert_pdf_with_images(src, self.doc_name, self.images_dir)
        else:
            mid = MarkItDown()
            markdown = mid.convert(str(src)).text_content
            markdown = extract_base64_images(markdown, self.doc_name, self.images_dir)
        return ParseResult(markdown=markdown)
