"""FastAPI REST service for OpenKB query and chat."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import litellm
from agents import set_tracing_disabled
from dotenv import load_dotenv
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from openkb.agent.chat import build_chat_session_agent, iter_chat_turn_events
from openkb.agent.chat_session import delete_session, list_sessions, load_session
from openkb.agent.query import (
    build_run_config_from_bundle,
    run_query,
)
from openkb.api_helpers import (
    _configure_cors,
    _init_kb_for_api,
    _list_knowledge_bases,
    _load_or_create_session,
    _mount_web_ui,
    _parse_stream_form,
    _reserve_add_uploads,
    _resolve_kb,
    _run_add_uploads,
    _save_query_answer,
    _stream_add_uploads,
    _stream_chat,
    _stream_query,
    _stream_recompile,
    _stream_remove,
    _stream_watch_events,
    _write_add_uploads,
    require_bearer_token,
)
from openkb.api_models import (
    AddResponse,
    ChatRequest,
    ChatResponse,
    ChatSessionDeleteRequest,
    ChatSessionDeleteResponse,
    ChatSessionListResponse,
    ChatSessionLoadRequest,
    ChatSessionLoadResponse,
    EnvWritten,
    GraphRequest,
    GraphResponse,
    InitRequest,
    InitResponse,
    KbListResponse,
    KbRequest,
    LintRequest,
    LintResponse,
    ListResponse,
    PageRequest,
    PageResponse,
    QueryRequest,
    QueryResponse,
    RecompileRequest,
    RecompileResponse,
    RemoveRequest,
    RemoveResponse,
    StatusResponse,
    WatchStartRequest,
    WatchStatusResponse,
)
from openkb.cli import (
    get_kb_list,
    get_kb_status,
    iter_recompile,
    run_lint_report,
    run_remove_for_api,
)
from openkb.config import (
    DEFAULT_CONFIG,
    kb_root_dir,
    load_config,
    resolve_credential_bundle,
    validate_kb_name,
)
from openkb.log import append_log
from openkb.watch_service import WatchRegistry

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    # One registry per app instance so each TestClient is isolated.
    # Keep process-wide setup inside create_app() so importing the module
    # does not mutate global state for tests or library consumers.
    set_tracing_disabled(True)
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "true")
    litellm.suppress_debug_info = True
    load_dotenv()

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
        # Auth is opt-in (see require_bearer_token). Warn once at server startup
        # when no token is configured, so an exposed deployment is never
        # silently world-open. Fires for every launch path (uvicorn/gunicorn/
        # the openkb-api CLI); harmless on loopback. (create_app() can't see the
        # bind host under a factory launch, so this is host-agnostic.)
        if not os.environ.get("OPENKB_API_TOKEN"):
            logger.warning(
                "OPENKB_API_TOKEN is not set — the REST API is unauthenticated. "
                "This is fine for local use; set OPENKB_API_TOKEN to require a "
                "bearer token before exposing the server on a reachable interface."
            )
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
        # Reserve unique raw paths under the lock so concurrent same-name
        # uploads cannot race on _unique_raw_path and overwrite each other,
        # then stream the bodies outside it so a large or slow upload does not
        # block other same-KB mutations (lint/recompile/other adds).
        async with _kb_mutation_lock(kb):
            reserved = _reserve_add_uploads(resolved_kb_dir, files)
        saved_uploads = await _write_add_uploads(reserved, files)
        if _parse_stream_form(stream):
            return StreamingResponse(
                _stream_add_uploads(kb, resolved_kb_dir, saved_uploads, bundle=bundle),
                media_type="text/event-stream",
            )
        return await _run_add_uploads(kb, resolved_kb_dir, saved_uploads, bundle=bundle)

    @app.post("/api/v1/query", response_model=QueryResponse)
    async def query_endpoint(
        request: QueryRequest,
        fastapi_request: Request,
        _: None = Depends(require_bearer_token),
    ) -> Any:
        kb_dir = _resolve_kb(request.kb)
        bundle = resolve_credential_bundle(kb_dir)
        config = load_config(kb_dir / ".openkb" / "config.yaml")
        model = config.get("model", DEFAULT_CONFIG["model"])
        run_config = build_run_config_from_bundle(model, bundle)

        if request.stream:
            return StreamingResponse(
                _stream_query(request, kb_dir, model, fastapi_request, bundle=bundle),
                media_type="text/event-stream",
            )

        try:
            answer = await run_query(
                request.question,
                kb_dir,
                model,
                stream=False,
                run_config=run_config,
                bundle=bundle,
            )
            append_log(kb_dir / "wiki", "query", request.question)
            saved_path = (
                _save_query_answer(kb_dir, request.question, answer) if request.save else None
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
        fastapi_request: Request,
        _: None = Depends(require_bearer_token),
    ) -> Any:
        kb_dir = _resolve_kb(request.kb)
        bundle = resolve_credential_bundle(kb_dir)
        session = _load_or_create_session(kb_dir, request.session_id)
        run_config = build_run_config_from_bundle(session.model, bundle)

        if request.stream:
            return StreamingResponse(
                _stream_chat(request, kb_dir, session, fastapi_request, bundle=bundle),
                media_type="text/event-stream",
            )

        try:
            answer = ""
            append_log(kb_dir / "wiki", "query", request.message)
            agent = build_chat_session_agent(kb_dir, session, bundle=bundle)
            async for event in iter_chat_turn_events(
                agent, session, request.message, run_config=run_config
            ):
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

    @app.post("/api/v1/graph", response_model=GraphResponse)
    async def graph_endpoint(
        request: GraphRequest,
        _: None = Depends(require_bearer_token),
    ) -> GraphResponse:
        kb_dir = _resolve_kb(request.kb)
        from openkb.visualize import build_graph

        graph = await run_in_threadpool(build_graph, kb_dir / "wiki")
        return GraphResponse(**graph)

    @app.post("/api/v1/page", response_model=PageResponse)
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
            raise HTTPException(
                status_code=404, detail=result.get("message", "Document not found.")
            )
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
        # Return the real post-stop status. If the worker was mid-compile and
        # entered draining mode, status() reports active=True; otherwise it
        # reports active=False after the watcher has been removed.
        return WatchStatusResponse(**registry.status(request.kb))

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

    # Catch-all for unknown API paths so they return JSON 404 instead of being
    # swallowed by the StaticFiles mount below (which serves index.html for SPA
    # routing and would turn API 404s into HTML 200s).
    @app.api_route(
        "/api/{path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
    )
    async def api_not_found(path: str) -> Any:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )

    _mount_web_ui(app)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the OpenKB REST API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    import uvicorn

    # Use the factory so uvicorn can reload a fresh app instance and so the
    # module can be imported without triggering global side effects.
    uvicorn.run(
        "openkb.api:create_app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        factory=True,
    )


if __name__ == "__main__":
    main()
