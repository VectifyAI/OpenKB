"""Shared PDF page extraction for OpenKB."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class PageContent:
    """Extracted content for one PDF page."""

    page: int
    content: str
    images: list[dict[str, Any]]


class PdfExtractor(Protocol):
    """Protocol implemented by PDF page extraction backends."""

    def parse_pages(
        self,
        pdf_path: Path,
        doc_name: str,
        images_dir: Path,
    ) -> list[PageContent]:
        """Extract ordered page content from *pdf_path*."""


class LocalPdfExtractor:
    """Local PyMuPDF-backed PDF extractor preserving existing behavior."""

    provider = "local"

    def parse_pages(
        self,
        pdf_path: Path,
        doc_name: str,
        images_dir: Path,
    ) -> list[PageContent]:
        from openkb.images import convert_pdf_to_pages

        return normalize_page_content(convert_pdf_to_pages(pdf_path, doc_name, images_dir))


def normalize_page_content(raw_pages: Any) -> list[PageContent]:
    """Normalize raw page dictionaries into :class:`PageContent` objects."""
    if not isinstance(raw_pages, list):
        return []

    pages: list[PageContent] = []
    for index, item in enumerate(raw_pages, start=1):
        if isinstance(item, str):
            content = item.strip()
            if content:
                pages.append(PageContent(page=index, content=content, images=[]))
            continue

        if not isinstance(item, dict):
            continue

        raw_page = item.get("page", item.get("page_number", item.get("page_num", index)))
        try:
            page_number = int(raw_page)
        except (TypeError, ValueError):
            page_number = index
        if page_number < 1:
            page_number = index

        content = item.get("content", item.get("markdown", item.get("text", "")))
        if content is None:
            content = ""
        content = str(content).strip()

        images = item.get("images", [])
        if not isinstance(images, list):
            images = []
        normalized_images = [
            image for image in images
            if isinstance(image, dict) and isinstance(image.get("path"), str)
        ]

        if content or normalized_images:
            pages.append(PageContent(
                page=page_number,
                content=content,
                images=normalized_images,
            ))

    return pages


def pages_to_markdown(pages: list[PageContent]) -> str:
    """Render extracted pages as the Markdown source used for short PDFs."""
    rendered_pages: list[str] = []
    for page in pages:
        parts: list[str] = []
        if page.content:
            parts.append(page.content)

        for image in page.images:
            path = image.get("path")
            if not isinstance(path, str) or not path:
                continue
            image_markdown = f"![image]({path})"
            if image_markdown not in page.content and path not in page.content:
                parts.append(image_markdown)

        page_markdown = "\n\n".join(parts).strip()
        if page_markdown:
            rendered_pages.append(page_markdown)

    return "\n\n".join(rendered_pages).strip()


def pages_to_json(pages: list[PageContent]) -> list[dict[str, Any]]:
    """Render extracted pages as OpenKB's long-PDF source JSON shape."""
    return [
        {
            "page": page.page,
            "content": page.content,
            "images": page.images,
        }
        for page in pages
    ]


def _provider_from_config(config: dict[str, Any]) -> str:
    raw = config.get("pdf_parser", "local")
    if isinstance(raw, str):
        provider = raw
    elif isinstance(raw, dict):
        provider = raw.get("provider", "local")
    else:
        provider = "local"
    return str(provider).strip().lower() or "local"


def resolve_pdf_extractor(config: dict[str, Any]) -> PdfExtractor:
    """Resolve the configured PDF extraction backend."""
    provider = _provider_from_config(config)
    if provider == "local":
        return LocalPdfExtractor()
    raise ValueError(f"Unsupported pdf_parser provider: {provider}")
