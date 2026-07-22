"""Document REST endpoints: read a document's ingested source text.

An APIRouter (sibling of api_pages_router.py) so api.py stays under the
per-file line gate. Read-only: source documents are ``Do not modify directly``
artifacts, so there is no edit/delete counterpart here (document removal lives
on ``/api/v1/remove``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from openkb.api_helpers import _resolve_kb, require_bearer_token
from openkb.api_models import DocumentSourceRequest, DocumentSourceResponse
from openkb.documents import read_document_source

documents_router = APIRouter()

# Raster types the image extractor produces, mapped to explicit media types.
# SVG is excluded (inline-script risk). We set the media type ourselves rather
# than let FileResponse guess it: mimetypes has no ``.webp`` entry on some
# Python versions and would fall back to ``text/plain``, which a blob-loaded
# ``<img>`` then refuses to render.
_IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


@documents_router.post("/api/v1/document/source", response_model=DocumentSourceResponse)
async def document_source_endpoint(
    request: DocumentSourceRequest,
    _: None = Depends(require_bearer_token),
) -> DocumentSourceResponse:
    kb_dir = _resolve_kb(request.kb)
    try:
        result = await run_in_threadpool(read_document_source, kb_dir, request.hash)
    except (OSError, ValueError) as exc:
        # Corrupt/unreadable source file (bad JSON, unexpected shape, I/O error):
        # a controlled 500 with a clean message beats an unhandled stack trace.
        raise HTTPException(status_code=500, detail="Could not read document source.") from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Document source not found.")
    return DocumentSourceResponse(**result)


@documents_router.get("/api/v1/document/image")
async def document_image_endpoint(
    kb: str = Query(...),
    path: str = Query(..., min_length=1),
    _: None = Depends(require_bearer_token),
) -> FileResponse:
    """Serve an extracted document image (``wiki/sources/images/**``).

    ``path`` is the image reference from the source text, resolved relative to
    the KB's ``wiki/`` dir (source text stores ``sources/images/<doc>/...``).
    Narrowed to the images dir + raster suffixes with a traversal guard, so this
    read-only sink can never serve wiki pages, skills, or arbitrary files.
    """
    kb_dir = _resolve_kb(kb)
    images_root = (kb_dir / "wiki" / "sources" / "images").resolve()
    full = (kb_dir / "wiki" / path).resolve()
    if not full.is_relative_to(images_root):
        raise HTTPException(status_code=400, detail="Invalid image path.")
    media_type = _IMAGE_MEDIA_TYPES.get(full.suffix.lower())
    if media_type is None:
        raise HTTPException(status_code=400, detail="Only extracted images are served.")
    if not full.is_file():
        raise HTTPException(status_code=404, detail="Image not found.")
    return FileResponse(full, media_type=media_type)
