"""KB discovery for ``GET /api/v1/kbs`` (the web UI's KB switcher).

Split out of :mod:`openkb.api_helpers` so that module stays under the per-file
line gate (``tests/test_file_size.py``). Discovery unions the children of
``kb_root_dir()`` with the KBs registered in global.yaml (``kb_aliases`` /
``known_kbs``), so a KB created outside the root — the CLI initializes a KB
wherever it is run — is still surfaced.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from openkb.api_helpers import _is_kb_dir
from openkb.config import kb_root_dir, registered_kbs


def _kb_list_item(kb_dir: Path, name: str) -> dict[str, Any]:
    """Build one KB-list item: name + resolved path + rollup counters.

    ``document_count`` comes from ``.openkb/hashes.json`` (0 if absent/corrupt)
    and ``last_compile`` from the newest ``wiki/summaries/*.md`` mtime.
    """
    hashes_file = kb_dir / ".openkb" / "hashes.json"
    doc_count = 0
    if hashes_file.exists():
        try:
            doc_count = len(json.loads(hashes_file.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            doc_count = 0
    last_compile = None
    summaries_dir = kb_dir / "wiki" / "summaries"
    if summaries_dir.is_dir():
        mtimes = [p.stat().st_mtime for p in summaries_dir.glob("*.md")]
        if mtimes:
            last_compile = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(max(mtimes)))
    return {
        "name": name,
        "path": str(kb_dir),
        "document_count": doc_count,
        "last_compile": last_compile,
        "has_raw": (kb_dir / "raw").is_dir(),
    }


def _list_knowledge_bases() -> dict[str, Any]:
    """List knowledge bases for the web UI's KB switcher.

    Discovery unions two sources and de-dupes by RESOLVED path so a KB that is
    both a root child and registered appears exactly once:

    1. children of ``kb_root_dir()`` that look like a KB (``_is_kb_dir``); and
    2. KBs registered in global.yaml (``kb_aliases`` name→path, then
       ``known_kbs`` paths) that live OUTSIDE the root.

    Root children are visited first, so a dual-source KB keeps its directory
    name (which equals the alias for KBs created via ``/init``); a registry-only
    KB keeps its alias name (or, for a bare ``known_kbs`` path, its directory
    name). Stale/missing registry entries (fail ``_is_kb_dir``) are skipped. The
    response still carries the top-level ``root``.
    """
    root = kb_root_dir()
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if not child.is_dir() or not _is_kb_dir(child):
                continue
            resolved = child.resolve()
            seen.add(str(resolved))
            items.append(_kb_list_item(resolved, child.name))
    for name, kb_dir in registered_kbs():
        if str(kb_dir) in seen or not _is_kb_dir(kb_dir):
            continue
        seen.add(str(kb_dir))
        items.append(_kb_list_item(kb_dir, name))
    return {"root": str(root), "knowledge_bases": items}
