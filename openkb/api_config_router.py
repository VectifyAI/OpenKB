"""Global-config REST endpoints (GET/PATCH /api/v1/config).

An APIRouter (sibling of api_graph.py / api_output.py) so api.py stays under the
per-file line gate (tests/test_file_size.py). Global writes are serialized by
save_global_config's own lock (openkb/config.py), NOT the per-KB mutation lock,
so these endpoints need no create_app closure and extract cleanly.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool

from openkb.api_config import apply_global_config_patch, read_global_config
from openkb.api_helpers import require_bearer_token
from openkb.api_models import GlobalConfigPatchRequest, GlobalConfigResponse

config_router = APIRouter()


@config_router.get("/api/v1/config", response_model=GlobalConfigResponse)
async def global_config_get(
    _: None = Depends(require_bearer_token),
) -> GlobalConfigResponse:
    return read_global_config()


@config_router.patch("/api/v1/config", response_model=GlobalConfigResponse)
async def global_config_patch(
    request: GlobalConfigPatchRequest,
    _: None = Depends(require_bearer_token),
) -> GlobalConfigResponse:
    # apply_global_config_patch acquires a blocking portalocker flock; run it in
    # a threadpool so the async event loop is not frozen under lock contention
    # (matches the /init endpoint's run_in_threadpool offload in api.py). The
    # read path below holds no lock, so it stays on the event loop.
    await run_in_threadpool(apply_global_config_patch, request)
    return read_global_config()
