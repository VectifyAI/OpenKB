"""Built-in bundle normalizers."""

from __future__ import annotations

from openkb.ingest.normalizers.image import IMAGE_EXTENSIONS, ImageNormalizer
from openkb.ingest.normalizers.markdown import MarkdownNormalizer
from openkb.ingest.normalizers.markitdown import MarkItDownNormalizer
from openkb.ingest.normalizers.pdf import PdfNormalizer
from openkb.ingest.normalizers.text import PlainTextNormalizer

__all__ = [
    "IMAGE_EXTENSIONS",
    "ImageNormalizer",
    "MarkdownNormalizer",
    "MarkItDownNormalizer",
    "PdfNormalizer",
    "PlainTextNormalizer",
]
