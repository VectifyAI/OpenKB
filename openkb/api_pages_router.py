"""Wiki-page REST endpoints: read / delete a page.

An APIRouter (sibling of api_graph.py / api_config_router.py / api_kbs_router.py)
so api.py stays under the per-file line gate (tests/test_file_size.py). All
endpoints depend only on module-level helpers (_resolve_kb, require_bearer_token)
and page_ops — no create_app closure — so they extract cleanly. Page mutations
serialize via page_ops' own KB ingest lock, not the app's per-KB asyncio lock.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from starlette.concurrency import run_in_threadpool

from openkb.api_helpers import _resolve_kb, require_bearer_token
from openkb.api_models import (
    PageDeleteRequest,
    PageDeleteResponse,
    PageEditRequest,
    PageEditResponse,
    PageLinksRequest,
    PageLinksResponse,
    PageRequest,
    PageResponse,
)
from openkb.page_ops import delete_wiki_page, edit_wiki_page, page_link_context

pages_router = APIRouter()


@pages_router.post("/api/v1/page", response_model=PageResponse)
async def page_endpoint(
    request: PageRequest,
    _: None = Depends(require_bearer_token),
) -> PageResponse:
    kb_dir = _resolve_kb(request.kb)
    wiki_dir = (kb_dir / "wiki").resolve()
    rel = request.path if request.path.endswith(".md") else f"{request.path}.md"
    target = (wiki_dir / rel).resolve()
    if not target.is_relative_to(wiki_dir):
        raise HTTPException(status_code=400, detail="Invalid page path.")
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"Page not found: {request.path}")
    content = await run_in_threadpool(target.read_text, encoding="utf-8")
    return PageResponse(path=request.path, content=content)


@pages_router.post("/api/v1/page/delete", response_model=PageDeleteResponse)
async def delete_page_endpoint(
    request: PageDeleteRequest,
    _: None = Depends(require_bearer_token),
) -> PageDeleteResponse:
    kb_dir = _resolve_kb(request.kb)
    try:
        result = await run_in_threadpool(
            delete_wiki_page, kb_dir, request.path, dry_run=request.dry_run
        )
    except ValueError as exc:  # invalid/traversal-unsafe page ref
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result["status"] == "not_found":
        raise HTTPException(status_code=404, detail=f"Page not found: {request.path}")
    return PageDeleteResponse(**result)


@pages_router.post("/api/v1/page/links", response_model=PageLinksResponse)
async def page_links_endpoint(
    request: PageLinksRequest,
    _: None = Depends(require_bearer_token),
) -> PageLinksResponse:
    kb_dir = _resolve_kb(request.kb)
    try:
        result = await run_in_threadpool(page_link_context, kb_dir, request.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result["status"] == "not_found":
        raise HTTPException(status_code=404, detail=f"Page not found: {request.path}")
    return PageLinksResponse(**result)


@pages_router.put("/api/v1/page", response_model=PageEditResponse)
async def edit_page_endpoint(
    request: PageEditRequest,
    _: None = Depends(require_bearer_token),
) -> PageEditResponse:
    kb_dir = _resolve_kb(request.kb)
    try:
        result = await run_in_threadpool(edit_wiki_page, kb_dir, request.path, request.content)
    except ValueError as exc:  # invalid/traversal-unsafe page ref
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result["status"] == "not_found":
        raise HTTPException(status_code=404, detail=f"Page not found: {request.path}")
    return PageEditResponse(**result)
