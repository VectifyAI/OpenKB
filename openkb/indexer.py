"""PageIndex indexer for long documents."""
from __future__ import annotations

import json as json_mod
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

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


def index_long_document(pdf_path: Path, kb_dir: Path) -> IndexResult:
    """Index a long PDF document using PageIndex and write wiki pages."""
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
    doc_name: str = doc.get("doc_name", pdf_path.stem)
    description: str = doc.get("doc_description", "")
    structure: list = doc.get("structure", [])

    tree = {
        "doc_name": doc_name,
        "doc_description": description,
        "structure": structure,
    }

    # Write wiki/sources/ — get per-page content from PageIndex and store as JSON
    sources_dir = kb_dir / "wiki" / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    dest_images_dir = sources_dir / "images" / pdf_path.stem

    # Get per-page content from PageIndex
    all_pages = col.get_page_content(doc_id, f"1-{doc.get('page_count', 9999)}")

    # Relocate image paths in each page
    dest_images_dir.mkdir(parents=True, exist_ok=True)
    for page in all_pages:
        if "images" in page:
            for img in page["images"]:
                src_path = Path(img["path"])
                if src_path.exists():
                    filename = src_path.name
                    dest = dest_images_dir / filename
                    if not dest.exists():
                        shutil.copy2(src_path, dest)
                    img["path"] = f"images/{pdf_path.stem}/{filename}"

    (sources_dir / f"{pdf_path.stem}.json").write_text(
        json_mod.dumps(all_pages, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    # Write wiki/summaries/ (no images, just summaries)
    summaries_dir = kb_dir / "wiki" / "summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)
    summary_md = render_summary_md(tree, doc_name, doc_id)
    (summaries_dir / f"{pdf_path.stem}.md").write_text(summary_md, encoding="utf-8")

    return IndexResult(doc_id=doc_id, description=description, tree=tree)
