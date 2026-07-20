"""Serve path-validated viewable artifacts (output/**.html) for the Workbench.

A read-only sibling of ``api_graph.py``: an APIRouter so ``api.py`` stays under
the per-file line gate (``tests/test_file_size.py``). The path guard shares
the resolve + containment check used by the ``read_file`` tool
(``read_kb_file`` in ``openkb/agent/tools.py``), but narrows the servable
prefix to ``output/`` only — the sole consumer is conversationally-created
artifacts (written via ``write_file``, itself gated to ``output/``) — and
further narrows to viewable HTML so those loose ``output/**.html`` artifacts
open in the docked sandboxed iframe. ``wiki/`` and ``skills/`` are never
servable here: ``skills/`` can hold third-party skill bodies and must not
get a browser-executable HTML sink.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from openkb.api_helpers import _resolve_kb, require_bearer_token

output_router = APIRouter()

_VIEWABLE_SUFFIXES = (".html", ".htm")


@output_router.get("/api/v1/output")
async def output_file_endpoint(
    kb: str = Query(...),
    path: str = Query(..., min_length=1),
    _: None = Depends(require_bearer_token),
) -> FileResponse:
    kb_dir = _resolve_kb(kb)
    root = kb_dir.resolve()
    full = (root / path).resolve()
    if not full.is_relative_to(root):
        raise HTTPException(status_code=400, detail="Invalid output path.")
    rel = full.relative_to(root)
    if not rel.parts or rel.parts[0] != "output":
        raise HTTPException(status_code=400, detail="Path must be under output/.")
    if rel.suffix.lower() not in _VIEWABLE_SUFFIXES:
        raise HTTPException(status_code=400, detail="Only viewable HTML artifacts are served.")
    if not full.is_file():
        raise HTTPException(status_code=404, detail=f"Artifact not found: {path}")
    return FileResponse(full, media_type="text/html")
