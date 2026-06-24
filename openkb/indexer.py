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
from openkb.pdf_extractor import normalize_page_content, pages_to_json, resolve_pdf_extractor
from openkb.tree_renderer import render_summary_md

logger = logging.getLogger(__name__)


@dataclass
class IndexResult:
    """Result of indexing a long document via PageIndex."""

    doc_id: str
    description: str
    tree: dict


def _normalize_page_content(raw_pages: Any) -> list[dict[str, Any]]:
    """Normalize PageIndex/local PDF page content into OpenKB's JSON shape."""
    return pages_to_json(normalize_page_content(raw_pages))


def _get_pdf_page_count(pdf_path: Path) -> int:
    from openkb.converter import get_pdf_page_count

    return get_pdf_page_count(pdf_path)


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
        page_count = _get_pdf_page_count(pdf_path)
        try:
            all_pages = _normalize_page_content(col.get_page_content(doc_id, f"1-{page_count}"))
        except Exception as exc:
            logger.warning("Cloud get_page_content failed for %s: %s", pdf_path.name, exc)

    if not all_pages:
        if pageindex_api_key:
            logger.warning("Cloud returned no pages for %s; falling back to configured PDF extractor", pdf_path.name)
        pdf_extractor = resolve_pdf_extractor(config)
        all_pages = pages_to_json(pdf_extractor.parse_pages(pdf_path, source_name, images_dir))

    if not all_pages:
        raise RuntimeError(f"No page content extracted for {pdf_path.name}")

    (sources_dir / f"{source_name}.json").write_text(
        json_mod.dumps(all_pages, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    # Write wiki/summaries/ (no images, just summaries)
    summaries_dir = kb_dir / "wiki" / "summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)
    summary_md = render_summary_md(tree, source_name, doc_id, description=description)
    (summaries_dir / f"{source_name}.md").write_text(summary_md, encoding="utf-8")

    return IndexResult(doc_id=doc_id, description=description, tree=tree)
