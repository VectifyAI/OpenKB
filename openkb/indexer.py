"""PageIndex indexer for long documents."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import os

from pageindex import IndexConfig, PageIndexClient

from openkb.config import load_config
from openkb.tree_renderer import render_source_md, render_summary_md

logger = logging.getLogger(__name__)


@dataclass
class IndexResult:
    """Result of indexing a long document via PageIndex."""

    doc_id: str
    description: str
    tree: dict


def index_long_document(pdf_path: Path, kb_dir: Path) -> IndexResult:
    """Index a long PDF document using PageIndex and write wiki pages.

    Steps:
    1. Create a :class:`~pageindex.LocalClient` with full node text, summary,
       and doc description generation enabled.
    2. Add the PDF to the default collection → receive ``doc_id``.
    3. Fetch document metadata and structure from PageIndex storage.
    4. Render and write ``wiki/sources/{stem}.md`` (source/pageindex view).
    5. Render and write ``wiki/summaries/{stem}.md`` (summary view).
    6. Return an :class:`IndexResult` with doc_id, description, and tree.
    """
    okb_dir = kb_dir / ".okb"
    config = load_config(okb_dir / "config.yaml")

    model: str = config.get("model", "gpt-5.4")
    pi_api_key = os.environ.get(config.get("pageindex_api_key_env", ""), "")

    # Images go directly to wiki/sources/images/{stem}/ via PageIndex
    images_dir = str(kb_dir / "wiki" / "sources" / "images" / pdf_path.stem)

    index_config = IndexConfig(
        if_add_node_text=True,
        if_add_node_summary=True,
        if_add_doc_description=True,
        images_dir=images_dir,
    )

    client = PageIndexClient(
        api_key=pi_api_key or None,
        model=model,
        storage_path=str(okb_dir),
        index_config=index_config,
    )
    col = client.collection()

    # 2. Add PDF → doc_id (retry up to 3 times — PageIndex TOC accuracy is stochastic)
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

    # 3. Fetch complete document (metadata + structure + text)
    doc = col.get_document(doc_id, include_text=True)
    doc_name: str = doc.get("doc_name", pdf_path.stem)
    description: str = doc.get("doc_description", "")
    structure: list = doc.get("structure", [])

    tree = {
        "doc_name": doc_name,
        "doc_description": description,
        "structure": structure,
    }

    # 4. Write wiki/sources/
    sources_dir = kb_dir / "wiki" / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    source_md = render_source_md(tree, doc_name, doc_id)
    (sources_dir / f"{pdf_path.stem}.md").write_text(source_md, encoding="utf-8")

    # 5. Write wiki/summaries/
    summaries_dir = kb_dir / "wiki" / "summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)
    summary_md = render_summary_md(tree, doc_name, doc_id)
    (summaries_dir / f"{pdf_path.stem}.md").write_text(summary_md, encoding="utf-8")

    return IndexResult(doc_id=doc_id, description=description, tree=tree)
