"""PageIndex indexer for long documents."""
from __future__ import annotations

import json as json_mod
import logging

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import os

from pageindex import IndexConfig, PageIndexClient

from openkb.config import load_config
from openkb.tree_renderer import render_summary_md

logger = logging.getLogger(__name__)


@dataclass
class IndexResult:
    """Result of indexing a long document via PageIndex."""

    doc_id: str
    description: str
    tree: dict


@dataclass
class CloudImportResult:
    """Result of importing an existing PageIndex Cloud document."""

    doc_id: str
    doc_name: str   # collision-resistant wiki slug
    name: str       # cloud display name (original filename in the cloud)
    description: str


def _normalize_page_content(raw_pages: Any) -> list[dict[str, Any]]:
    """Normalize PageIndex/local PDF page content into OpenKB's JSON shape."""
    if not isinstance(raw_pages, list):
        return []

    pages: list[dict[str, Any]] = []
    for index, item in enumerate(raw_pages, start=1):
        if isinstance(item, str):
            content = item.strip()
            if content:
                pages.append({"page": index, "content": content, "images": []})
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
            pages.append({
                "page": page_number,
                "content": content,
                "images": normalized_images,
            })

    return pages


def _get_pdf_page_count(pdf_path: Path) -> int:
    from openkb.converter import get_pdf_page_count

    return get_pdf_page_count(pdf_path)


def _convert_pdf_to_pages(pdf_path: Path, doc_name: str, images_dir: Path) -> list[dict[str, Any]]:
    from openkb.images import convert_pdf_to_pages

    return convert_pdf_to_pages(pdf_path, doc_name, images_dir)


def _write_long_doc_artifacts(
    tree: dict, pages: list[dict[str, Any]], doc_name: str, doc_id: str, kb_dir: Path,
    description: str = "",
) -> Path:
    """Write ``wiki/sources/<doc_name>.json`` + ``wiki/summaries/<doc_name>.md``.

    Returns the summary path. Shared by :func:`index_long_document` (local)
    and :func:`import_cloud_document` (cloud) so both produce identical
    artifacts. Page images, when present, are written separately by the
    caller's page extractor — this helper only persists page text + summary.
    """
    sources_dir = kb_dir / "wiki" / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    (sources_dir / f"{doc_name}.json").write_text(
        json_mod.dumps(pages, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    summaries_dir = kb_dir / "wiki" / "summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)
    summary_path = summaries_dir / f"{doc_name}.md"
    summary_path.write_text(
        render_summary_md(tree, doc_name, doc_id, description=description), encoding="utf-8"
    )
    return summary_path


def index_long_document(
    pdf_path: Path, kb_dir: Path, doc_name: str | None = None
) -> IndexResult:
    """Index a long PDF document using PageIndex and write wiki pages.

    ``doc_name`` is the collision-resistant wiki name used for all written
    artifacts; defaults to the PDF's stem for backward compatibility.
    """
    source_name = doc_name or pdf_path.stem
    openkb_dir = kb_dir / ".openkb"
    config = load_config(openkb_dir / "config.yaml")

    model: str = config.get("model", "gpt-5.4")
    pageindex_api_key = os.environ.get("PAGEINDEX_API_KEY", "")

    index_config = IndexConfig(
        if_add_node_text=True,
        if_add_node_summary=True,
        if_add_doc_description=True,
    )

    client = PageIndexClient(
        api_key=pageindex_api_key or None,
        model=model,
        storage_path=str(openkb_dir),
        index_config=index_config,
    )
    col = client.collection()

    # Add PDF (retry up to 3 times — PageIndex TOC accuracy is stochastic)
    max_retries = 3
    doc_id = None
    for attempt in range(1, max_retries + 1):
        try:
            doc_id = col.add(str(pdf_path))
            logger.info("PageIndex added %s → doc_id=%s (attempt %d)", pdf_path.name, doc_id, attempt)
            break
        except Exception as exc:
            logger.warning("PageIndex attempt %d/%d failed for %s: %s", attempt, max_retries, pdf_path.name, exc)
            if attempt == max_retries:
                raise RuntimeError(f"Failed to index {pdf_path.name} after {max_retries} attempts: {exc}") from exc

    # Fetch complete document (metadata + structure + text)
    doc = col.get_document(doc_id, include_text=True)
    indexed_doc_name: str = doc.get("doc_name", pdf_path.stem)
    description: str = doc.get("doc_description", "")
    structure: list = doc.get("structure", [])

    # Debug: print doc keys and page_count to diagnose get_page_content range
    logger.info("Doc keys: %s", list(doc.keys()))
    logger.info("page_count from doc: %s", doc.get("page_count", "NOT PRESENT"))

    tree = {
        "doc_name": indexed_doc_name,
        "doc_description": description,
        "structure": structure,
    }

    # Write wiki/sources/ — per-page content
    sources_dir = kb_dir / "wiki" / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    images_dir = sources_dir / "images" / source_name

    all_pages: list[dict[str, Any]] = []
    if pageindex_api_key:
        # Cloud mode: fetch OCR'd markdown from PageIndex. get_page_content
        # requires a page range, so pass "1-N".
        page_count = _get_pdf_page_count(pdf_path)
        try:
            all_pages = _normalize_page_content(col.get_page_content(doc_id, f"1-{page_count}"))
        except Exception as exc:
            logger.warning("Cloud get_page_content failed for %s: %s", pdf_path.name, exc)

    if not all_pages:
        if pageindex_api_key:
            logger.warning("Cloud returned no pages for %s; falling back to local pymupdf", pdf_path.name)
        all_pages = _normalize_page_content(_convert_pdf_to_pages(pdf_path, source_name, images_dir))

    if not all_pages:
        raise RuntimeError(f"No page content extracted for {pdf_path.name}")

    _write_long_doc_artifacts(
        tree, all_pages, source_name, doc_id, kb_dir, description=description
    )
    return IndexResult(doc_id=doc_id, description=description, tree=tree)


# PageIndex's get_page_content rejects a single page range covering more than
# this many pages (``parse_pages`` raises "Page range too large (max 1000)"),
# so cloud page fetches are windowed in chunks of this size.
_CLOUD_PAGE_WINDOW = 1000
# Safety bound on the windowed fetch (in pages) in case a backend never returns
# a short window — caps the loop at _CLOUD_PAGE_MAX / _CLOUD_PAGE_WINDOW calls.
_CLOUD_PAGE_MAX = 1_000_000


def _fetch_cloud_pages(col, doc_id: str) -> list[dict[str, Any]]:
    """Fetch all OCR pages of a cloud doc, windowing around the 1000-page cap.

    ``get_page_content`` returns the whole document and uses its ``pages`` arg
    only as a client-side filter that ``parse_pages`` caps at 1000 pages — so a
    single ``"1-<N>"`` request fails for any doc over 1000 pages. Request fixed
    ``1000``-page windows and stop as soon as a window comes back SHORT (fewer
    than a full window): PageIndex page numbers are sequential, so a short window
    means we've passed the last page. This is what makes the common (≤1000-page)
    doc a single request, while still fetching every page of a larger one — and,
    unlike bounding the loop by the tree's max page index, it never truncates a
    doc whose tree under-reports its page count (a real case: a paper whose tree
    stops a couple pages short of the references). A wide safety bound guards
    against a backend that never narrows the window.
    """
    pages: list[dict[str, Any]] = []
    start = 1
    while start <= _CLOUD_PAGE_MAX:
        window = _normalize_page_content(
            col.get_page_content(doc_id, f"{start}-{start + _CLOUD_PAGE_WINDOW - 1}")
        )
        pages.extend(window)
        if len(window) < _CLOUD_PAGE_WINDOW:
            break
        start += _CLOUD_PAGE_WINDOW
    return pages


def import_cloud_document(doc_id: str, kb_dir: Path, path_key: str) -> CloudImportResult:
    """Import an already-indexed PageIndex Cloud document by ``doc_id``.

    Fetches structure + OCR'd page content from the cloud (no local PDF) and
    writes the same wiki artifacts as :func:`index_long_document`. Requires
    ``PAGEINDEX_API_KEY``. ``path_key`` is the synthetic identity key
    (``pageindex-cloud:<doc_id>``) used to resolve a collision-resistant
    wiki name.
    """
    from openkb.converter import resolve_doc_name_from_key
    from openkb.state import HashRegistry

    pageindex_api_key = os.environ.get("PAGEINDEX_API_KEY", "")
    if not pageindex_api_key:
        raise RuntimeError(
            "Importing from PageIndex Cloud requires the PAGEINDEX_API_KEY "
            "environment variable."
        )

    client = PageIndexClient(api_key=pageindex_api_key)
    col = client.collection()

    doc = col.get_document(doc_id, include_text=True)
    cloud_name: str = doc.get("doc_name") or doc_id
    description: str = doc.get("doc_description", "")
    structure: list = doc.get("structure", [])

    registry = HashRegistry(kb_dir / ".openkb" / "hashes.json")
    stem = Path(cloud_name).stem or doc_id
    doc_name = resolve_doc_name_from_key(stem, path_key, registry)

    tree = {
        "doc_name": cloud_name,
        "doc_description": description,
        "structure": structure,
    }

    all_pages = _fetch_cloud_pages(col, doc_id)
    if not all_pages:
        raise RuntimeError(
            f"No page content returned from PageIndex Cloud for doc_id={doc_id}"
        )

    _write_long_doc_artifacts(tree, all_pages, doc_name, doc_id, kb_dir, description=description)
    return CloudImportResult(
        doc_id=doc_id, doc_name=doc_name, name=cloud_name, description=description,
    )
