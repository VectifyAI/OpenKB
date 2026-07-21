"""Knowledge-base lifecycle endpoints beyond config (POST /api/v1/kb/delete).

An APIRouter (sibling of api_config_router.py / api_graph.py / api_output.py) so
api.py stays under the per-file line gate (tests/test_file_size.py). delete_kb
does its own filesystem removal + global-registry unregister (config.py's lock);
like /api/v1/remove it needs no create_app closure and extracts cleanly.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from starlette.concurrency import run_in_threadpool

from openkb.api_helpers import _resolve_kb, require_bearer_token
from openkb.api_models import KbDeleteRequest, KbDeleteResponse
from openkb.config import delete_kb

kbs_router = APIRouter()


@kbs_router.post("/api/v1/kb/delete", response_model=KbDeleteResponse)
async def delete_kb_endpoint(
    request: KbDeleteRequest,
    _: None = Depends(require_bearer_token),
) -> Any:
    # Type-the-name confirmation, re-checked server-side: this physically
    # removes the whole KB directory (raw docs + wiki) and is irreversible, so
    # it must never fire from a client that skipped the guard.
    if request.confirm_name != request.kb:
        raise HTTPException(status_code=400, detail="confirm_name does not match the KB name.")
    kb_dir = _resolve_kb(request.kb)  # 400 if the name is not a KB
    # delete_kb rmtrees the directory and unregisters it from global.yaml;
    # offload the blocking filesystem work off the event loop.
    await run_in_threadpool(delete_kb, kb_dir)
    return KbDeleteResponse(deleted=True, kb=request.kb, path=str(kb_dir))
