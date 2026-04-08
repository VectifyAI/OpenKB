"""PageIndex indexer for long documents."""
from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import os

from pageindex import IndexConfig, PageIndexClient

from openkb.config import load_config
from openkb.tree_renderer import render_source_md, render_summary_md

logger = logging.getLogger(__name__)

_IMG_REF_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


@dataclass
class IndexResult:
    """Result of indexing a long document via PageIndex."""

    doc_id: str
    description: str
    tree: dict


def _relocate_images(markdown: str, doc_stem: str, dest_images_dir: Path) -> str:
    """Copy images from PageIndex internal paths to wiki/sources/images/ and rewrite refs.

    PageIndex stores images internally (e.g. .openkb/files/{collection}/{doc_id}/images/).
    We copy them to dest_images_dir and rewrite paths to be relative to the .md file
    (i.e. images/{doc_stem}/filename).
    """
    dest_images_dir.mkdir(parents=True, exist_ok=True)

    def _replace(match: re.Match) -> str:
        alt = match.group(1)
        src_path_str = match.group(2)
        src_path = Path(src_path_str)
        if not src_path.exists():
            logger.warning("Image not found: %s", src_path)
            return match.group(0)
        filename = src_path.name
        dest = dest_images_dir / filename
        if not dest.exists():
            shutil.copy2(src_path, dest)
        return f"![{alt}](images/{doc_stem}/{filename})"

    return _IMG_REF_RE.sub(_replace, markdown)


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

    # Write wiki/sources/ — copy images from PageIndex internal location
    # and rewrite paths to be relative to the .md file (images/{stem}/filename)
    sources_dir = kb_dir / "wiki" / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    dest_images_dir = sources_dir / "images" / pdf_path.stem
    source_md = render_source_md(tree, doc_name, doc_id)
    source_md = _relocate_images(source_md, pdf_path.stem, dest_images_dir)
    (sources_dir / f"{pdf_path.stem}.md").write_text(source_md, encoding="utf-8")

    # Write wiki/summaries/ (no images, just summaries)
    summaries_dir = kb_dir / "wiki" / "summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)
    summary_md = render_summary_md(tree, doc_name, doc_id)
    (summaries_dir / f"{pdf_path.stem}.md").write_text(summary_md, encoding="utf-8")

    return IndexResult(doc_id=doc_id, description=description, tree=tree)
