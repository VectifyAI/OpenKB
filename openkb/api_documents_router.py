"""Document REST endpoints: read a document's ingested source text.

An APIRouter (sibling of api_pages_router.py) so api.py stays under the
per-file line gate. Read-only: source documents are ``Do not modify directly``
artifacts, so there is no edit/delete counterpart here (document removal lives
on ``/api/v1/remove``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from starlette.concurrency import run_in_threadpool

from openkb.api_helpers import _resolve_kb, require_bearer_token
from openkb.api_models import DocumentSourceRequest, DocumentSourceResponse
from openkb.documents import read_document_source

documents_router = APIRouter()


@documents_router.post("/api/v1/document/source", response_model=DocumentSourceResponse)
async def document_source_endpoint(
    request: DocumentSourceRequest,
    _: None = Depends(require_bearer_token),
) -> DocumentSourceResponse:
    kb_dir = _resolve_kb(request.kb)
    try:
        result = await run_in_threadpool(read_document_source, kb_dir, request.hash, request.page)
    except (OSError, ValueError) as exc:
        # Corrupt/unreadable source file (bad JSON, unexpected shape, I/O error):
        # a controlled 500 with a clean message beats an unhandled stack trace.
        raise HTTPException(status_code=500, detail="Could not read document source.") from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Document source not found.")
    return DocumentSourceResponse(**result)
