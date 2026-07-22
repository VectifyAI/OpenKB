"""Pydantic request and response models for the OpenKB REST API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr


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


class ChatTraceStep(BaseModel):
    """One persisted step of a chat turn's interleaved trace: a run of
    narration/answer text (``kind="text"``) or one tool call (``kind="tool"``,
    with the raw ``name``/``arguments`` so the frontend applies on restore the
    same read-only whitelist it uses live). Turns recorded before traces
    existed or via the CLI have an empty trace, and the client then falls back
    to the flat ``assistant_texts`` entry."""

    kind: str
    text: str | None = None
    name: str | None = None
    arguments: str | None = None


class ChatSessionLoadResponse(BaseModel):
    session_id: str
    title: str
    turn_count: int
    user_turns: list[str]
    assistant_texts: list[str]
    # Parallel to assistant_texts (1:1 by index); an empty inner list means
    # "no trace for this turn — render the flat assistant_texts entry instead".
    assistant_traces: list[list[ChatTraceStep]] = Field(default_factory=list)


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
    # Optional custom location for the KB: an ABSOLUTE directory (a relative
    # path is a 400). Absent → created under kb_root_dir() / kb. Either way the
    # KB is registered under `kb` so it resolves by name afterwards.
    path: str | None = None


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
    entities: list[str]
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


class DeckRequest(BaseModel):
    kb: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    intent: str = Field(..., min_length=1)
    stream: bool = True


class DeckResponse(BaseModel):
    name: str
    status: str
    path: str


class DeckListResponse(BaseModel):
    decks: list[dict]


class SkillRequest(BaseModel):
    kb: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    intent: str = Field(..., min_length=1)
    stream: bool = True


class SkillResponse(BaseModel):
    name: str
    status: str
    path: str


class SkillListResponse(BaseModel):
    skills: list[dict]


class GraphRequest(BaseModel):
    kb: str = Field(..., min_length=1)


class GraphResponse(BaseModel):
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    types: list[str]


class PageRequest(BaseModel):
    kb: str = Field(..., min_length=1)
    path: str = Field(..., min_length=1)


class PageResponse(BaseModel):
    path: str
    content: str


class DocumentSourceRequest(BaseModel):
    kb: str = Field(..., min_length=1)
    # SHA-256 hash key from /list — the unique document identifier (avoids
    # ambiguity when two documents share a doc_name/filename stem).
    hash: str = Field(..., min_length=1)


class DocumentSourceResponse(BaseModel):
    hash: str
    name: str
    doc_name: str
    type: str
    format: str
    content: str
    # Page count for long docs (per-page JSON); None for short (single .md).
    pages: int | None = None


class PageDeleteRequest(BaseModel):
    kb: str = Field(..., min_length=1)
    # A '<section>/<name>' wiki-page ref, e.g. "concepts/attention". Only
    # concepts/ and entities/ pages are user-deletable (validated server-side).
    path: str = Field(..., min_length=1)
    # dry_run reports the impacted backlink pages without deleting anything, so
    # the UI can show "these N pages will have their links demoted" + confirm.
    dry_run: bool = False


class PageDeleteResponse(BaseModel):
    status: str
    target: str
    # Content pages whose inbound [[links]] to the target will be / were demoted
    # to plain text (the deletion's blast radius).
    backlinks: list[str] = []
    files_changed: int | None = None
    ghosts_stripped: int | None = None


class PageLinksRequest(BaseModel):
    kb: str = Field(..., min_length=1)
    path: str = Field(..., min_length=1)


class PageLinksResponse(BaseModel):
    status: str
    target: str
    outlinks: list[str] = []  # pages this page links to
    backlinks: list[str] = []  # pages that link to this page


class PageEditRequest(BaseModel):
    kb: str = Field(..., min_length=1)
    path: str = Field(..., min_length=1)
    # New page BODY. The OKF frontmatter (type/description/sources) is
    # code-managed and preserved server-side; any frontmatter here is dropped.
    content: str


class PageEditResponse(BaseModel):
    status: str
    target: str
    # Dead [[links]] the edit introduced, demoted to plain text on save (so the
    # UI can tell the user which links didn't resolve).
    ghosts_stripped: list[str] = []
    content: str | None = None  # the saved full page (frontmatter + body)


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
    # Resolved directory of the KB. Present for every item so a KB registered
    # OUTSIDE kb_root_dir() is addressable/locatable by the UI, not just root
    # children.
    path: str
    document_count: int = 0
    last_compile: str | None = None
    has_raw: bool = False


class KbListResponse(BaseModel):
    root: str
    knowledge_bases: list[KbSummaryItem]


class KbDeleteRequest(BaseModel):
    kb: str = Field(..., min_length=1)
    # Must equal `kb`: a type-the-name confirmation, re-checked server-side so
    # this irreversible delete never fires from a client that skipped the guard.
    confirm_name: str = Field(..., min_length=1)


class KbDeleteResponse(BaseModel):
    deleted: bool
    kb: str
    path: str


class MetaResponse(BaseModel):
    version: str


class _KbConfigWritable(BaseModel):
    """Typed schema for the writable ``config.yaml`` fields.

    All fields are optional so a partial merge-patch validates. Used to reject
    a wrong-typed config VALUE with a 400 BEFORE it is persisted: a bad value
    that reached disk would make every future ``read_kb_config`` re-read it and
    500 (persisted corruption + endpoint DoS). Types MUST match what
    ``read_kb_config``/``KbConfigResponse`` read back.
    """

    model: str | None = None
    language: str | None = None
    pageindex_threshold: int | None = None
    # Entity-type vocabulary for extraction. A list REPLACES the layer below; an
    # explicit null reverts to inherited. Values are cleaned/deduped and "other"
    # is always ensured at read time (config.resolve_entity_types).
    entity_types: list[str] | None = None


# Single source of truth for the writable config keys (derived from the model
# above so the two never drift).
_KB_CONFIG_WRITABLE_KEYS = set(_KbConfigWritable.model_fields)


class GlobalConfigValues(BaseModel):
    """Raw global-layer values (null where global.yaml is silent)."""

    model: str | None = None
    language: str | None = None
    pageindex_threshold: int | None = None
    entity_types: list[str] | None = None


class GlobalConfigResponse(BaseModel):
    model: str
    language: str
    pageindex_threshold: int
    # Effective global entity-type vocabulary (cleaned; always includes "other").
    entity_types: list[str]
    # Effective KB root that kb_root_dir() would return (env OPENKB_KB_ROOT >
    # global.yaml kb_root > default <config>/kbs). kb_root_env_pinned is True
    # when OPENKB_KB_ROOT is set — a global.yaml kb_root is then ineffective, so
    # the UI can note that editing it won't take effect on this server.
    kb_root: str
    kb_root_env_pinned: bool
    # Global-default credentials read from ~/.config/openkb/.env (the
    # lowest-precedence credential source). openai_api_base is plaintext (a
    # config value, not a secret); has_api_key is a presence flag only — the raw
    # key value is NEVER returned by the API. Mirrors KbConfigResponse.
    openai_api_base: str | None
    has_api_key: bool


class GlobalConfigPatchRequest(BaseModel):
    config: dict[str, Any] | None = None
    # Global-default credentials (mirrors KbConfigPatchRequest). Merge-patch
    # semantics via model_fields_set: an ABSENT field is left unchanged, an
    # explicit null CLEARS it. api_key is a SecretStr so its value never lands
    # in logs/reprs.
    api_key: SecretStr | None = None
    openai_api_base: str | None = None
    # RFC 7386 merge-patch (via model_fields_set): a string SETS global.yaml
    # `kb_root`, an explicit null REMOVES it (revert to the default root), and an
    # absent field leaves it unchanged. NOT a credential — persisted to
    # global.yaml, never the .env. Env OPENKB_KB_ROOT still overrides at runtime.
    kb_root: str | None = None


class KbConfigResponse(BaseModel):
    model: str
    language: str
    pageindex_threshold: int
    # Effective entity-type vocabulary (cleaned; always includes "other").
    entity_types: list[str]
    openai_api_base: str | None
    has_api_key: bool
    # Additive (non-breaking): which layer supplied each scalar's effective
    # value, and the raw global-layer values for the "继承 · 全局(値)" badge.
    sources: dict[str, Literal["kb", "global", "default"]]
    global_values: GlobalConfigValues


class KbConfigPatchRequest(BaseModel):
    kb: str
    config: dict[str, Any] | None = None
    api_key: SecretStr | None = None
    openai_api_base: str | None = None
