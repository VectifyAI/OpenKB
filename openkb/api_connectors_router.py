"""REST mirror of ``openkb source``.

Lock-holding work runs in a worker thread. ``kb_lock`` reentrancy is
per-thread, so these handlers must not take the lock on the event loop.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from openkb.api_helpers import _resolve_kb, require_bearer_token
from openkb.connectors.backend import ProviderError
from openkb.connectors.credentials import s3_credentials
from openkb.connectors.gdrive import prepare_gdrive_binding
from openkb.connectors.model import parse_s3_binding
from openkb.connectors.state import KbBusyError
from openkb.connectors.store import remove_binding, upsert_binding
from openkb.connectors.sync import binding_views, sync_bindings

connectors_router = APIRouter()


class ConnectorView(BaseModel):
    name: str
    provider: str
    location: dict[str, str]
    description: str
    stale: bool
    last_sync_at: str | None = None
    added: int = 0
    skipped: int = 0
    updated: int = 0
    failed: int = 0
    error: str | None = None


class ConnectorListResponse(BaseModel):
    connectors: list[ConnectorView]


class ConnectorAddRequest(BaseModel):
    kb: str = Field(..., min_length=1)
    name: str
    provider: str = "s3"
    region: str = ""
    bucket: str = ""
    prefix: str = ""
    folder: str = ""


class ConnectorRemoveRequest(BaseModel):
    kb: str = Field(..., min_length=1)
    name: str


class ConnectorSyncRequest(BaseModel):
    kb: str = Field(..., min_length=1)
    name: str | None = None
    non_interactive: bool = True


class BindingSyncView(BaseModel):
    name: str
    provider: str
    ok: bool
    added: int
    skipped: int
    updated: int
    failed: int
    error: str | None = None


class ConnectorSyncResponse(BaseModel):
    exit_code: int
    results: list[BindingSyncView]


class ConnectorMessage(BaseModel):
    message: str


def _add(kb_dir, request: ConnectorAddRequest) -> str:
    if request.provider == "gdrive":
        return prepare_gdrive_binding(
            kb_dir,
            name=request.name,
            folder=request.folder,
            non_interactive=True,
        )
    if request.provider != "s3":
        raise ValueError("Only the s3 and gdrive providers are available.")
    if s3_credentials(kb_dir) is None:
        raise ValueError(
            "S3 credentials are missing. Set OPENKB_S3_ACCESS_KEY_ID and "
            "OPENKB_S3_SECRET_ACCESS_KEY in the environment or <kb>/.env."
        )
    binding = parse_s3_binding(
        name=request.name,
        region=request.region,
        bucket=request.bucket,
        prefix=request.prefix,
    )
    return upsert_binding(kb_dir, binding)


@connectors_router.get("/api/v1/connectors", response_model=ConnectorListResponse)
async def list_connectors_endpoint(
    kb: str = Query(...),
    _: None = Depends(require_bearer_token),
) -> ConnectorListResponse:
    kb_dir = _resolve_kb(kb)
    views = await run_in_threadpool(binding_views, kb_dir)
    return ConnectorListResponse(connectors=[ConnectorView(**view) for view in views])


@connectors_router.post("/api/v1/connectors", response_model=ConnectorMessage)
async def add_connector_endpoint(
    request: ConnectorAddRequest,
    _: None = Depends(require_bearer_token),
) -> ConnectorMessage:
    kb_dir = _resolve_kb(request.kb)
    try:
        message = await run_in_threadpool(_add, kb_dir, request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KbBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ConnectorMessage(message=message)


@connectors_router.post("/api/v1/connectors/remove", response_model=ConnectorMessage)
async def remove_connector_endpoint(
    request: ConnectorRemoveRequest,
    _: None = Depends(require_bearer_token),
) -> ConnectorMessage:
    kb_dir = _resolve_kb(request.kb)

    def _remove():
        return remove_binding(kb_dir, request.name)

    try:
        message = await run_in_threadpool(_remove)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"No binding named {request.name!r}.") from exc
    except KbBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ConnectorMessage(message=message)


@connectors_router.post("/api/v1/connectors/sync", response_model=ConnectorSyncResponse)
async def sync_connectors_endpoint(
    request: ConnectorSyncRequest,
    _: None = Depends(require_bearer_token),
) -> ConnectorSyncResponse:
    kb_dir = _resolve_kb(request.kb)

    def _sync():
        return sync_bindings(
            kb_dir,
            name=request.name,
            non_interactive=request.non_interactive,
        )

    try:
        outcome = await run_in_threadpool(_sync)
    except KbBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ConnectorSyncResponse(
        exit_code=outcome.exit_code,
        results=[
            BindingSyncView(
                name=item.name,
                provider=item.provider,
                ok=item.ok,
                added=item.added,
                skipped=item.skipped,
                updated=item.updated,
                failed=item.failed,
                error=item.error,
            )
            for item in outcome.reports
        ],
    )
