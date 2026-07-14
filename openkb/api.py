"""FastAPI REST service for OpenKB query and chat."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import re
import hmac
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import litellm
from agents import set_tracing_disabled
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.staticfiles import StaticFiles

from openkb.agent.chat import build_chat_session_agent, iter_chat_turn_events
from openkb.agent.chat_session import ChatSession, delete_session, list_sessions, load_session
from openkb.agent.query import build_query_agent, iter_agent_response_events, run_query, build_run_config_from_bundle
from openkb.config import resolve_credential_bundle
from openkb.cli import (
    SUPPORTED_EXTENSIONS,
    _add_for_api,
    _setup_llm_key,
    get_kb_list,
    get_kb_status,
    initialize_kb,
    run_remove_for_api,
    run_lint_report,
    iter_recompile,
)
from openkb.config import (
    DEFAULT_CONFIG,
    kb_root_dir,
    load_config,
    register_kb_alias,
    resolve_kb_alias,
    validate_kb_name,
)
from openkb.log import append_log
from openkb.watch_service import WatchRegistry

set_tracing_disabled(True)
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "true")
litellm.suppress_debug_info = True
load_dotenv()

security = HTTPBearer(auto_error=False)
UPLOAD_CHUNK_BYTES = 1024 * 1024
MAX_UPLOAD_FILE_BYTES = int(
    os.environ.get("OPENKB_MAX_UPLOAD_FILE_BYTES", str(100 * 1024 * 1024))
)
MAX_UPLOAD_REQUEST_BYTES = int(
    os.environ.get("OPENKB_MAX_UPLOAD_REQUEST_BYTES", str(500 * 1024 * 1024))
)


class QueryRequest(BaseModel):
    kb: str = Field(..., min_length=1)
    question: str = Field(..., min_length=1)
    stream: bool = True
    save: bool = False


class QueryResponse(BaseModel):
    answer: str
    saved_path: str | None = None


class ChatRequest(BaseModel):
    kb: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)
    session_id: str | None = None
    stream: bool = True


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    turn_count: int


class ChatSessionItem(BaseModel):
    id: str
    title: str
    turn_count: int
    updated_at: str
    model: str


class ChatSessionListResponse(BaseModel):
    kb: str
    sessions: list[ChatSessionItem]


class ChatSessionLoadResponse(BaseModel):
    session_id: str
    title: str
    turn_count: int
    user_turns: list[str]
    assistant_texts: list[str]


class ChatSessionLoadRequest(BaseModel):
    kb: str = Field(..., min_length=1)
    session_id: str = Field(..., min_length=1)


class ChatSessionDeleteRequest(BaseModel):
    kb: str = Field(..., min_length=1)
    session_id: str = Field(..., min_length=1)


class ChatSessionDeleteResponse(BaseModel):
    deleted: bool


class InitRequest(BaseModel):
    kb: str = Field(..., min_length=1)
    model: str | None = None
    api_key: str | None = None
    openai_api_base: str | None = None


class EnvWritten(BaseModel):
    api_key: bool
    openai_api_base: bool


class InitResponse(BaseModel):
    kb: str
    created: bool
    env_written: EnvWritten
    message: str


class AddFileItem(BaseModel):
    original_name: str
    saved_path: str | None = None
    status: str
    message: str


class AddResponse(BaseModel):
    kb: str
    files: list[AddFileItem]
    added_count: int
    skipped_count: int
    failed_count: int


class KbRequest(BaseModel):
    kb: str = Field(..., min_length=1)


class LintRequest(BaseModel):
    kb: str = Field(..., min_length=1)
    fix: bool = False


class DocumentItem(BaseModel):
    hash: str
    name: str
    type: str
    display_type: str
    pages: int | None = None


class ListResponse(BaseModel):
    documents: list[DocumentItem]
    document_count: int
    summaries: list[str]
    concepts: list[str]
    reports: list[str]


class StatusResponse(BaseModel):
    directories: dict[str, int]
    raw_count: int
    total_indexed: int
    last_compile: str | None = None
    last_lint: str | None = None


class LintResponse(BaseModel):
    skipped: bool
    reason: str | None = None
    message: str
    structural_report: str | None = None
    knowledge_report: str | None = None
    report_path: str | None = None
    lint_files_changed: int | None = None
    lint_ghosts_removed: int | None = None


class RemoveRequest(BaseModel):
    kb: str = Field(..., min_length=1)
    identifier: str = Field(..., min_length=1)
    keep_raw: bool = False
    keep_empty: bool = False
    dry_run: bool = False
    stream: bool = False


class RemoveActionItem(BaseModel):
    tag: str
    target: str


class RemoveResponse(BaseModel):
    status: str
    name: str | None = None
    doc_name: str | None = None
    actions: list[RemoveActionItem] = []
    concepts_deleted: list[str] = []
    entities_deleted: list[str] = []
    lint_files_changed: int | None = None
    lint_ghosts_removed: int | None = None
    pageindex_message: str | None = None
    pageindex_error: str | None = None
    message: str | None = None
    candidates: list[dict[str, str]] = []


class RecompileDocItem(BaseModel):
    name: str | None = None
    doc_name: str | None = None
    type: str
    status: str
    elapsed: float | None = None
    message: str | None = None


class RecompileRequest(BaseModel):
    kb: str = Field(..., min_length=1)
    doc_name: str | None = None
    all_docs: bool = False
    dry_run: bool = False
    refresh_schema: bool = False
    stream: bool = False


class RecompileTargetItem(BaseModel):
    name: str
    doc_name: str
    type: str


class RecompileResponse(BaseModel):
    status: str
    total: int
    recompiled: int
    skipped: int
    docs: list[RecompileDocItem] = []
    targets: list[RecompileTargetItem] | None = None
    candidates: list[dict[str, str]] | None = None
    message: str | None = None


class WatchStartRequest(BaseModel):
    kb: str = Field(..., min_length=1)
    debounce: float = Field(default=2.0, gt=0)


class WatchEventItem(BaseModel):
    ts: float
    event: str
    data: dict[str, Any] = {}


class WatchStatusResponse(BaseModel):
    kb: str
    active: bool
    started_at: float | None = None
    raw_dir: str | None = None
    debounce: float | None = None
    counters: dict[str, int] = {}
    recent_events: list[WatchEventItem] = []


class KbSummaryItem(BaseModel):
    name: str
    document_count: int = 0
    last_compile: str | None = None
    has_raw: bool = False


class KbListResponse(BaseModel):
    root: str
    knowledge_bases: list[KbSummaryItem]


def create_app() -> FastAPI:
    # One registry per app instance so each TestClient is isolated.
    registry = WatchRegistry()

    # Per-KB asyncio locks for async mutation endpoints (lint/recompile).
    # kb_ingest_lock tracks reentrancy in threading.local, but the event loop
    # runs all requests on one thread, so concurrent same-KB mutations are
    # mis-counted as re-entrant and bypass mutual exclusion. An asyncio.Lock
    # serializes same-KB mutations *before* they enter the threading lock,
    # eliminating the hazard without touching locks.py.
    kb_mutation_locks: dict[str, asyncio.Lock] = {}

    def _kb_mutation_lock(kb: str) -> asyncio.Lock:
        lock = kb_mutation_locks.get(kb)
        if lock is None:
            lock = asyncio.Lock()
            kb_mutation_locks[kb] = lock
        return lock

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            registry.stop_all()

    app = FastAPI(title="OpenKB API", lifespan=lifespan)

    _configure_cors(app)

    @app.get("/api/v1/kbs", response_model=KbListResponse)
    async def list_kbs_endpoint(
        _: None = Depends(require_bearer_token),
    ) -> KbListResponse:
        return KbListResponse(**_list_knowledge_bases())

    @app.post("/api/v1/init", response_model=InitResponse)
    async def init_endpoint(
        request: InitRequest,
        _: None = Depends(require_bearer_token),
    ) -> InitResponse:
        try:
            kb_name = validate_kb_name(request.kb)
            kb_dir = (kb_root_dir() / kb_name).resolve()
            # Run lock-holding work in a threadpool so each request gets its
            # own threading.local (kb_ingest_lock reentrancy is per-thread)
            # and the event loop is not blocked by file I/O / flock.
            result = await run_in_threadpool(
                _init_kb_for_api,
                kb_dir,
                kb_name,
                model=request.model,
                api_key=request.api_key,
                openai_api_base=request.openai_api_base,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except FileExistsError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Init failed: {exc}",
            ) from exc

        return InitResponse(
            kb=kb_name,
            created=bool(result["created"]),
            env_written=EnvWritten(**result["env_written"]),
            message=str(result["message"]),
        )

    @app.post("/api/v1/add", response_model=AddResponse)
    async def add_endpoint(
        kb: str = Form(...),
        stream: str = Form("true"),
        files: list[UploadFile] = File(default=[]),
        _: None = Depends(require_bearer_token),
    ) -> Any:
        resolved_kb_dir = _resolve_kb(kb)
        bundle = resolve_credential_bundle(resolved_kb_dir)
        if not files:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No files uploaded.",
            )
        saved_uploads = await _save_add_uploads(resolved_kb_dir, files)
        if _parse_stream_form(stream):
            return StreamingResponse(
                _stream_add_uploads(kb, resolved_kb_dir, saved_uploads, bundle=bundle),
                media_type="text/event-stream",
            )
        return await _run_add_uploads(kb, resolved_kb_dir, saved_uploads, bundle=bundle)

    @app.post("/api/v1/query", response_model=QueryResponse)
    async def query_endpoint(
        request: QueryRequest,
        _: None = Depends(require_bearer_token),
    ) -> Any:
        kb_dir = _resolve_kb(request.kb)
        _setup_llm_key(kb_dir)
        bundle = resolve_credential_bundle(kb_dir)
        config = load_config(kb_dir / ".openkb" / "config.yaml")
        model = config.get("model", DEFAULT_CONFIG["model"])
        run_config = build_run_config_from_bundle(model, bundle)

        if request.stream:
            return StreamingResponse(
                _stream_query(request, kb_dir, model, bundle=bundle),
                media_type="text/event-stream",
            )

        try:
            answer = await run_query(request.question, kb_dir, model, stream=False, run_config=run_config, bundle=bundle)
            append_log(kb_dir / "wiki", "query", request.question)
            saved_path = (
                _save_query_answer(kb_dir, request.question, answer)
                if request.save
                else None
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Query failed: {exc}",
            ) from exc
        return QueryResponse(
            answer=answer,
            saved_path=str(saved_path) if saved_path else None,
        )

    @app.post("/api/v1/chat", response_model=ChatResponse)
    async def chat_endpoint(
        request: ChatRequest,
        _: None = Depends(require_bearer_token),
    ) -> Any:
        kb_dir = _resolve_kb(request.kb)
        _setup_llm_key(kb_dir)
        bundle = resolve_credential_bundle(kb_dir)
        session = _load_or_create_session(kb_dir, request.session_id)
        run_config = build_run_config_from_bundle(session.model, bundle)

        if request.stream:
            return StreamingResponse(
                _stream_chat(request, kb_dir, session, bundle=bundle),
                media_type="text/event-stream",
            )

        try:
            answer = ""
            append_log(kb_dir / "wiki", "query", request.message)
            agent = build_chat_session_agent(kb_dir, session, bundle=bundle)
            async for event in iter_chat_turn_events(agent, session, request.message, run_config=run_config):
                if event["event"] == "final":
                    answer = event["data"]["answer"]
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Chat failed: {exc}",
            ) from exc
        return ChatResponse(
            session_id=session.id,
            answer=answer,
            turn_count=session.turn_count,
        )

    @app.post("/api/v1/chat/sessions", response_model=ChatSessionListResponse)
    async def chat_sessions_endpoint(
        request: KbRequest,
        _: None = Depends(require_bearer_token),
    ) -> ChatSessionListResponse:
        kb_dir = _resolve_kb(request.kb)
        try:
            sessions = list_sessions(kb_dir)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"List sessions failed: {exc}",
            ) from exc
        return ChatSessionListResponse(kb=request.kb, sessions=sessions)

    @app.post("/api/v1/chat/sessions/load", response_model=ChatSessionLoadResponse)
    async def chat_session_load_endpoint(
        request: ChatSessionLoadRequest,
        _: None = Depends(require_bearer_token),
    ) -> ChatSessionLoadResponse:
        kb_dir = _resolve_kb(request.kb)
        try:
            session = load_session(kb_dir, request.session_id)
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Chat session not found: {request.session_id}",
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Load session failed: {exc}",
            ) from exc
        return ChatSessionLoadResponse(
            session_id=session.id,
            title=session.title,
            turn_count=session.turn_count,
            user_turns=session.user_turns,
            assistant_texts=session.assistant_texts,
        )

    @app.post("/api/v1/chat/sessions/delete", response_model=ChatSessionDeleteResponse)
    async def chat_session_delete_endpoint(
        request: ChatSessionDeleteRequest,
        _: None = Depends(require_bearer_token),
    ) -> ChatSessionDeleteResponse:
        kb_dir = _resolve_kb(request.kb)
        try:
            deleted = delete_session(kb_dir, request.session_id)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Delete session failed: {exc}",
            ) from exc
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Chat session not found: {request.session_id}",
            )
        return ChatSessionDeleteResponse(deleted=True)

    @app.post("/api/v1/list", response_model=ListResponse)
    async def list_endpoint(
        request: KbRequest,
        _: None = Depends(require_bearer_token),
    ) -> ListResponse:
        kb_dir = _resolve_kb(request.kb)
        try:
            return ListResponse(**get_kb_list(kb_dir))
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"List failed: {exc}",
            ) from exc

    @app.post("/api/v1/status", response_model=StatusResponse)
    async def status_endpoint(
        request: KbRequest,
        _: None = Depends(require_bearer_token),
    ) -> StatusResponse:
        kb_dir = _resolve_kb(request.kb)
        try:
            return StatusResponse(**get_kb_status(kb_dir))
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Status failed: {exc}",
            ) from exc

    @app.post("/api/v1/lint", response_model=LintResponse)
    async def lint_endpoint(
        request: LintRequest,
        _: None = Depends(require_bearer_token),
    ) -> LintResponse:
        kb_dir = _resolve_kb(request.kb)
        bundle = resolve_credential_bundle(kb_dir)
        try:
            # Only fix=True mutations need serialization; read-only lint
            # (fix=False) is a report and may run concurrently.
            if request.fix:
                async with _kb_mutation_lock(request.kb):
                    return LintResponse(**await run_lint_report(kb_dir, fix=True, bundle=bundle))
            return LintResponse(**await run_lint_report(kb_dir, fix=False, bundle=bundle))
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Lint failed: {exc}",
            ) from exc
    @app.post("/api/v1/remove", response_model=RemoveResponse)
    async def remove_endpoint(
        request: RemoveRequest,
        _: None = Depends(require_bearer_token),
    ) -> Any:
        kb_dir = _resolve_kb(request.kb)
        if request.stream:
            return StreamingResponse(
                _stream_remove(request, kb_dir),
                media_type="text/event-stream",
            )
        result = await run_in_threadpool(
            run_remove_for_api,
            kb_dir,
            request.identifier,
            keep_raw=request.keep_raw,
            keep_empty=request.keep_empty,
            dry_run=request.dry_run,
        )
        status_value = result.get("status")
        if status_value == "not_found":
            raise HTTPException(status_code=404, detail=result.get("message", "Document not found."))
        if status_value == "multiple":
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Identifier matches multiple documents.",
                    "candidates": result.get("candidates", []),
                },
            )
        return RemoveResponse(**result)

    @app.post("/api/v1/recompile", response_model=RecompileResponse)
    async def recompile_endpoint(
        request: RecompileRequest,
        _: None = Depends(require_bearer_token),
    ) -> Any:
        kb_dir = _resolve_kb(request.kb)
        bundle = resolve_credential_bundle(kb_dir)
        if request.stream:
            return StreamingResponse(
                _stream_recompile(request, kb_dir, _kb_mutation_lock(request.kb), bundle=bundle),
                media_type="text/event-stream",
            )
        # Aggregate the async generator into a single JSON response. Terminal
        # errors map to HTTP codes; the final event carries the aggregate.
        targets: list[dict] | None = None
        candidates: list[dict[str, str]] | None = None
        error_code: int | None = None
        error_message: str | None = None
        result: dict = {}
        async with _kb_mutation_lock(request.kb):
            async for event in iter_recompile(
                kb_dir,
                request.doc_name,
                all_docs=request.all_docs,
                dry_run=request.dry_run,
                refresh_schema=request.refresh_schema,
                bundle=bundle,
            ):
                name = event.get("event")
                if name == "plan":
                    targets = event.get("targets", [])
                elif name == "error":
                    error_code = event.get("code", 500)
                    error_message = event.get("message", "Recompile failed.")
                    candidates = event.get("candidates")
                elif name == "final":
                    result = event
        if error_code is not None:
            if error_code == 409 and candidates is not None:
                raise HTTPException(
                    status_code=409,
                    detail={"message": error_message, "candidates": candidates},
                )
            raise HTTPException(status_code=error_code, detail=error_message)
        return RecompileResponse(
            status=result.get("status", "done"),
            total=result.get("total", 0),
            recompiled=result.get("recompiled", 0),
            skipped=result.get("skipped", 0),
            docs=result.get("docs", []),
            targets=targets,
            candidates=candidates,
        )
    @app.post("/api/v1/watch/start", response_model=WatchStatusResponse)
    async def watch_start_endpoint(
        request: WatchStartRequest,
        _: None = Depends(require_bearer_token),
    ) -> WatchStatusResponse:
        kb_dir = _resolve_kb(request.kb)
        try:
            registry.start(request.kb, kb_dir, debounce=request.debounce)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Watch start failed: {exc}",
            ) from exc
        return WatchStatusResponse(**registry.status(request.kb))

    @app.post("/api/v1/watch/stop", response_model=WatchStatusResponse)
    async def watch_stop_endpoint(
        request: KbRequest,
        _: None = Depends(require_bearer_token),
    ) -> WatchStatusResponse:
        _resolve_kb(request.kb)
        if not registry.stop(request.kb):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No active watcher for KB: {request.kb}",
            )
        return WatchStatusResponse(kb=request.kb, active=False)

    @app.post("/api/v1/watch/status", response_model=WatchStatusResponse)
    async def watch_status_endpoint(
        request: KbRequest,
        _: None = Depends(require_bearer_token),
    ) -> WatchStatusResponse:
        _resolve_kb(request.kb)
        return WatchStatusResponse(**registry.status(request.kb))

    @app.get("/api/v1/watch/events")
    async def watch_events_endpoint(
        request: Request,
        kb: str = Query(..., min_length=1),
        max_events: int | None = Query(default=None, ge=1),
        timeout_seconds: float | None = Query(default=None, ge=0),
        _: None = Depends(require_bearer_token),
    ) -> Any:
        _resolve_kb(kb)
        return StreamingResponse(
            _stream_watch_events(registry, kb, max_events, timeout_seconds, request),
            media_type="text/event-stream",
    )

    _mount_web_ui(app)

    return app


def _configure_cors(app: FastAPI) -> None:
    """Allow browser frontends to call the API (configurable via env)."""
    raw = os.environ.get("OPENKB_CORS_ORIGINS", "")
    wildcard = raw.strip() == "*"
    if wildcard:
        origins = ["*"]
    else:
        origins = [o.strip() for o in raw.split(",") if o.strip()] or [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ]
    # A wildcard origin with credentials is insecure: any site can issue
    # credentialed cross-origin requests. Reject this combination by forcing
    # credentials off for wildcards, which still allows unauthenticated
    # cross-origin GETs but blocks cookie/token-bearing requests.
    allow_credentials = not wildcard
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def _mount_web_ui(app: FastAPI) -> None:
    """Serve the bundled web UI at ``/`` when the ``web/`` directory exists.

    Mounting under the API origin avoids cross-origin fetch from ``file://``
    so the browser SPA can call the REST endpoints directly.
    """
    web_dir = Path(__file__).resolve().parent.parent / "web"
    if web_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web-ui")


def _list_knowledge_bases() -> dict[str, Any]:
    """List knowledge bases under the server's ``OPENKB_KB_ROOT``.

    Used by the web UI's KB switcher. There is no persisted KB registry, so
    discovery is directory-based: a child of ``OPENKB_KB_ROOT`` counts as a KB
    when it has both ``.openkb`` and ``wiki`` subdirectories.
    """
    root = kb_root_dir()
    items: list[dict[str, Any]] = []
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            if not _is_kb_dir(child):
                continue
            hashes_file = child / ".openkb" / "hashes.json"
            doc_count = 0
            if hashes_file.exists():
                try:
                    doc_count = len(json.loads(hashes_file.read_text(encoding="utf-8")))
                except (ValueError, OSError):
                    doc_count = 0
            last_compile = None
            summaries_dir = child / "wiki" / "summaries"
            if summaries_dir.is_dir():
                mtimes = [p.stat().st_mtime for p in summaries_dir.glob("*.md")]
                if mtimes:
                    last_compile = time.strftime(
                        "%Y-%m-%dT%H:%M:%S", time.localtime(max(mtimes))
                    )
            items.append(
                {
                    "name": child.name,
                    "document_count": doc_count,
                    "last_compile": last_compile,
                    "has_raw": (child / "raw").is_dir(),
                }
            )
    return {"root": str(root), "knowledge_bases": items}

def require_bearer_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> None:
    expected = os.environ.get("OPENKB_API_TOKEN")
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OPENKB_API_TOKEN is not configured.",
        )
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token required.",
        )
    if not hmac.compare_digest(credentials.credentials, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token.",
        )


def _resolve_kb(value: str) -> Path:
    try:
        kb_dir = resolve_kb_alias(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    if not _is_kb_dir(kb_dir):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Not a knowledge base: {value}",
        )
    return kb_dir


def _is_kb_dir(kb_dir: Path) -> bool:
    """A directory counts as a KB when it has both ``.openkb`` and ``wiki``."""
    return (kb_dir / ".openkb").is_dir() and (kb_dir / "wiki").is_dir()


def _init_kb_for_api(
    kb_dir: Path,
    kb_name: str,
    *,
    model: str | None,
    api_key: str | None,
    openai_api_base: str | None,
) -> dict:
    """Run ``initialize_kb`` + ``register_kb_alias`` off the event loop.

    Both touch file locks (``register_kb_alias`` holds the global-config
    lock); running them in a threadpool gives each request its own
    ``threading.local`` so ``kb_ingest_lock``'s reentrancy bookkeeping is
    correct, and avoids blocking the event loop.
    """
    result = initialize_kb(
        kb_dir, model=model, api_key=api_key, openai_api_base=openai_api_base,
    )
    register_kb_alias(kb_name, kb_dir)
    return result


def _save_query_answer(kb_dir: Path, question: str, answer: str) -> Path | None:
    from openkb.cli import save_exploration

    return save_exploration(kb_dir, question, answer)


def _parse_stream_form(value: str | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return True
    return value.strip().lower() not in {"false", "0", "no", "off"}


def _safe_upload_name(filename: str | None) -> str:
    name = Path(filename or "").name
    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is missing a filename.",
        )
    return name


def _unique_raw_path(raw_dir: Path, filename: str) -> Path:
    candidate = raw_dir / filename
    if not candidate.exists():
        return candidate

    stem = candidate.stem
    suffix = candidate.suffix
    counter = 1
    while True:
        candidate = raw_dir / f"{stem}-{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


async def _save_upload(
    kb_dir: Path,
    upload: UploadFile,
    request_bytes_so_far: int,
) -> tuple[Path, int]:
    filename = _safe_upload_name(upload.filename)
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported file type: {suffix}. "
                f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
            ),
        )

    raw_dir = kb_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    saved_path = _unique_raw_path(raw_dir, filename)
    try:
        file_bytes = 0
        with saved_path.open("wb") as handle:
            while chunk := await upload.read(UPLOAD_CHUNK_BYTES):
                file_bytes += len(chunk)
                request_bytes = request_bytes_so_far + file_bytes
                if file_bytes > MAX_UPLOAD_FILE_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"Uploaded file exceeds limit of "
                            f"{MAX_UPLOAD_FILE_BYTES} bytes."
                        ),
                    )
                if request_bytes > MAX_UPLOAD_REQUEST_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"Upload request exceeds limit of "
                            f"{MAX_UPLOAD_REQUEST_BYTES} bytes."
                        ),
                    )
                handle.write(chunk)
    except Exception as exc:
        saved_path.unlink(missing_ok=True)
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Upload save failed: {exc}",
        ) from exc
    finally:
        await upload.close()
    return saved_path, file_bytes


def _summarize_add_results(kb: str, results: list[AddFileItem]) -> AddResponse:
    return AddResponse(
        kb=kb,
        files=results,
        added_count=sum(1 for item in results if item.status == "added"),
        skipped_count=sum(1 for item in results if item.status == "skipped"),
        failed_count=sum(1 for item in results if item.status == "failed"),
    )


def _model_payload(model: BaseModel) -> dict[str, Any]:
    return model.model_dump()


async def _save_add_uploads(
    kb_dir: Path,
    files: list[UploadFile],
) -> list[tuple[Path, str]]:
    saved_uploads: list[tuple[Path, str]] = []
    request_bytes = 0
    try:
        for upload in files:
            original_name = _safe_upload_name(upload.filename)
            saved_path, file_bytes = await _save_upload(kb_dir, upload, request_bytes)
            request_bytes += file_bytes
            saved_uploads.append((saved_path, original_name))
    except Exception:
        for saved_path, _ in saved_uploads:
            saved_path.unlink(missing_ok=True)
        raise
    return saved_uploads


async def _run_add_uploads(
    kb: str,
    kb_dir: Path,
    saved_uploads: list[tuple[Path, str]],
    *,
    bundle=None,
) -> AddResponse:
    results = []
    for saved_path, original_name in saved_uploads:
        results.append(await _add_saved_file(kb_dir, saved_path, original_name, bundle=bundle))
    return _summarize_add_results(kb, results)


async def _stream_add_uploads(
    kb: str,
    kb_dir: Path,
    saved_uploads: list[tuple[Path, str]],
    *,
    bundle=None,
) -> AsyncIterator[str]:
    yield _sse(
        "start",
        {"endpoint": "add", "kb": kb, "file_count": len(saved_uploads)},
    )
    results: list[AddFileItem] = []
    try:
        for saved_path, original_name in saved_uploads:
            yield _sse(
                "uploaded",
                {"original_name": original_name, "saved_path": str(saved_path)},
            )
            yield _sse(
                "file_start",
                {"original_name": original_name, "saved_path": str(saved_path)},
            )
            item = await _add_saved_file(kb_dir, saved_path, original_name, bundle=bundle)
            results.append(item)
            yield _sse("file_done", _model_payload(item))
        final = _summarize_add_results(kb, results)
        yield _sse("final", _model_payload(final))
    except HTTPException as exc:
        yield _sse("error", {"message": exc.detail})
    except Exception as exc:
        yield _sse("error", {"message": f"Add failed: {exc}"})
    yield _sse("done", {})


async def _add_saved_file(kb_dir: Path, saved_path: Path, original_name: str, *, bundle=None) -> AddFileItem:
    result = await run_in_threadpool(_add_for_api, saved_path, kb_dir, bundle=bundle)
    item = AddFileItem(**result.__dict__)
    item.original_name = original_name
    if item.status == "skipped":
        saved_path.unlink(missing_ok=True)
        item.saved_path = None
    return item


def _load_or_create_session(kb_dir: Path, session_id: str | None) -> ChatSession:
    if session_id:
        try:
            return load_session(kb_dir, session_id)
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Chat session not found: {session_id}",
            ) from exc

    config = load_config(kb_dir / ".openkb" / "config.yaml")
    model = config.get("model", DEFAULT_CONFIG["model"])
    language = config.get("language", "en")
    return ChatSession.new(kb_dir, model, language)


def _sse(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


async def _stream_query(
    request: QueryRequest,
    kb_dir: Path,
    model: str,
    *,
    bundle=None,
) -> AsyncIterator[str]:
    yield _sse("start", {"endpoint": "query"})
    run_config = build_run_config_from_bundle(model, bundle)
    try:
        config = load_config(kb_dir / ".openkb" / "config.yaml")
        language = config.get("language", "en")
        agent = build_query_agent(str(kb_dir / "wiki"), model, language=language, bundle=bundle)
        final_answer = ""
        async for event in iter_agent_response_events(agent, request.question, run_config=run_config):
            data = event["data"]
            if event["event"] == "final":
                final_answer = data["answer"]
                saved_path = (
                    _save_query_answer(kb_dir, request.question, final_answer)
                    if request.save
                    else None
                )
                append_log(kb_dir / "wiki", "query", request.question)
                yield _sse(
                    "final",
                    {
                        "answer": final_answer,
                        "saved_path": str(saved_path) if saved_path else None,
                    },
                )
            else:
                yield _sse(event["event"], data)
    except Exception as exc:
        yield _sse("error", {"message": f"Query failed: {exc}"})
    yield _sse("done", {})


async def _stream_chat(
    request: ChatRequest,
    kb_dir: Path,
    session: ChatSession,
    *,
    bundle=None,
) -> AsyncIterator[str]:
    yield _sse("start", {"endpoint": "chat", "session_id": session.id})
    run_config = build_run_config_from_bundle(session.model, bundle)
    try:
        append_log(kb_dir / "wiki", "query", request.message)
        agent = build_chat_session_agent(kb_dir, session, bundle=bundle)
        async for event in iter_chat_turn_events(agent, session, request.message, run_config=run_config):
            yield _sse(event["event"], event["data"])
    except Exception as exc:
        yield _sse("error", {"message": f"Chat failed: {exc}"})
    yield _sse("done", {})


async def _stream_remove(
    request: RemoveRequest,
    kb_dir: Path,
) -> AsyncIterator[str]:
    """SSE view of remove: start, plan, per-stage progress, final, done.

    Maps ``run_remove_for_api``'s status codes to events so a streaming
    client can react to ``not_found`` / ``multiple`` / ``partial`` without
    waiting on an HTTP error.
    """
    yield _sse("start", {"endpoint": "remove", "identifier": request.identifier})
    try:
        result = await run_in_threadpool(
            run_remove_for_api,
            kb_dir,
            request.identifier,
            keep_raw=request.keep_raw,
            keep_empty=request.keep_empty,
            dry_run=request.dry_run,
        )
        status_value = result.get("status")
        if status_value == "not_found":
            yield _sse("error", {"code": 404, "message": "Document not found."})
        elif status_value == "multiple":
            yield _sse(
                "error",
                {"code": 409, "message": "Identifier matches multiple documents.",
                 "candidates": result.get("candidates", [])},
            )
        else:
            yield _sse("plan", {
                "name": result.get("name"),
                "doc_name": result.get("doc_name"),
                "actions": result.get("actions", []),
            })
            if status_value == "dry_run":
                yield _sse("final", {"status": "dry_run", **result})
            else:
                yield _sse("progress", {"stage": "wiki_cleanup"})
                yield _sse("final", {"status": status_value, **result})
    except Exception as exc:
        yield _sse("error", {"message": f"Remove failed: {exc}"})
    yield _sse("done", {})


async def _stream_recompile(
    request: RecompileRequest,
    kb_dir: Path,
    mutation_lock: asyncio.Lock,
    *,
    bundle=None,
) -> AsyncIterator[str]:
    """SSE view of recompile: start, per-doc progress, final, done.

    Maps ``iter_recompile``'s events to SSE so a streaming client can react
    to ``doc`` (ok/skipped/error) and terminal ``error`` (404/409/etc.) as
    they happen, without waiting on an HTTP error.
    """
    yield _sse("start", {"endpoint": "recompile"})
    # Hold the per-KB asyncio.Lock for the entire stream so concurrent
    # same-KB recompiles are serialized before kb_ingest_lock (which uses
    # threading.local and miscounts reentrancy on the event-loop thread).
    async with mutation_lock:
        try:
            async for event in iter_recompile(
                kb_dir,
                request.doc_name,
                all_docs=request.all_docs,
                dry_run=request.dry_run,
                refresh_schema=request.refresh_schema,
                bundle=bundle,
            ):
                name = event.get("event")
                if name == "error":
                    yield _sse("error", {
                        "code": event.get("code", 500),
                        "message": event.get("message", "Recompile failed."),
                        **({"candidates": event["candidates"]} if "candidates" in event else {}),
                    })
                elif name == "plan":
                    yield _sse("plan", {"targets": event.get("targets", [])})
                elif name == "doc":
                    yield _sse("doc", {k: v for k, v in event.items() if k != "event"})
                elif name == "final":
                    yield _sse("final", {k: v for k, v in event.items() if k != "event"})
        except Exception as exc:
            yield _sse("error", {"message": f"Recompile failed: {exc}"})
    yield _sse("done", {})


# Default cap for /watch/events SSE so abandoned clients do not poll forever.
_WATCH_SSE_TIMEOUT = float(os.environ.get("OPENKB_WATCH_SSE_TIMEOUT", "300"))

async def _stream_watch_events(
    registry: WatchRegistry,
    kb: str,
    max_events: int | None,
    timeout_seconds: float | None,
    request: Request,
) -> AsyncIterator[str]:
    """Tail a KB's watch event ring buffer as an SSE stream.

    Replays existing events then polls for new ones. Terminates when the
    watcher stops, or when ``max_events``/``timeout_seconds`` is reached (so
    bounded clients and tests can drain without hanging). With both unset the
    stream is capped by a default timeout when none is given.
    """
    state = registry.get(kb)
    yield _sse("start", {"endpoint": "watch", "kb": kb, "active": state is not None})
    if state is None:
        yield _sse("error", {"message": f"No active watcher for KB: {kb}"})
        yield _sse("done", {})
        return
    if timeout_seconds is None:
        timeout_seconds = _WATCH_SSE_TIMEOUT
    next_seq = 0
    emitted = 0
    started = time.monotonic()
    try:
        while True:
            if await request.is_disconnected():
                return
            for ev in list(state.events):
                if ev["seq"] < next_seq:
                    continue
                next_seq = ev["seq"] + 1
                yield _sse(ev["event"], ev["data"])
                emitted += 1
                if ev["event"] == "watcher_stopped":
                    yield _sse("done", {})
                    return
                if max_events is not None and emitted >= max_events:
                    yield _sse("done", {})
                    return
            if timeout_seconds is not None and (time.monotonic() - started) >= timeout_seconds:
                yield _sse("done", {})
                return
            await asyncio.sleep(0.5)
    except Exception as exc:
        yield _sse("error", {"message": f"Watch events stream failed: {exc}"})
    yield _sse("done", {})


app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the OpenKB REST API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    import uvicorn

    uvicorn.run("openkb.api:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
