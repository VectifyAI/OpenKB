"""Read the ingested source text for a document (REST ``/document/source``).

The Documents pane lists ingested docs by hash; this resolves a hash to the
converted full text under ``wiki/sources/``. Short docs are stored as
``<doc_name>.md``; long docs as ``<doc_name>.json`` — a per-page
``list[{page, content, images}]`` (see ``indexer._write_long_doc_artifacts``).
Read-only: sources are ``Do not modify directly`` artifacts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openkb.state import HashRegistry


def _render_pages(pages: list[dict[str, Any]]) -> str:
    """Join a long doc's per-page ``content`` into one Markdown string.

    Pages are separated by a thematic break (``---``) so the reader keeps
    page boundaries; blank/missing page content is skipped.
    """
    parts = [str(page.get("content", "")).strip() for page in pages]
    return "\n\n---\n\n".join(part for part in parts if part)


def _resolve_source_file(kb_dir: Path, meta: dict, doc_name: str) -> Path | None:
    """Resolve a document's source file, guarding against path traversal.

    Prefers the registry's stored ``source_path`` (a KB-relative posix path),
    then falls back to the ``wiki/sources/<doc_name>.{md,json}`` convention
    (older entries carry no ``source_path``). Returns ``None`` when nothing
    resolves to an existing file inside ``wiki/sources/``.
    """
    sources_dir = (kb_dir / "wiki" / "sources").resolve()
    candidates: list[Path] = []
    stored = meta.get("source_path")
    if stored:
        candidates.append(kb_dir / stored)
    candidates.append(sources_dir / f"{doc_name}.md")
    candidates.append(sources_dir / f"{doc_name}.json")
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.is_file() and resolved.is_relative_to(sources_dir):
            return resolved
    return None


def read_document_source(kb_dir: Path, file_hash: str) -> dict[str, Any] | None:
    """Return the ingested source text for the document identified by hash.

    Returns ``None`` when the hash is unknown OR its source file is missing,
    so the caller maps both to a 404. ``format`` is always ``"markdown"``;
    ``pages`` is the page count for long docs (per-page JSON) and ``None`` for
    short docs.
    """
    registry = HashRegistry(kb_dir / ".openkb" / "hashes.json")
    meta = registry.get(file_hash)
    if meta is None:
        return None

    doc_name = meta.get("doc_name") or Path(meta.get("name", "")).stem
    source = _resolve_source_file(kb_dir, meta, doc_name)
    if source is None:
        return None

    if source.suffix == ".json":
        pages = json.loads(source.read_text(encoding="utf-8"))
        content = _render_pages(pages)
        page_count: int | None = len(pages)
    else:
        content = source.read_text(encoding="utf-8")
        page_count = None

    return {
        "hash": file_hash,
        "name": meta.get("name", doc_name),
        "doc_name": doc_name,
        "type": meta.get("type", "unknown"),
        "format": "markdown",
        "content": content,
        "pages": page_count,
    }
