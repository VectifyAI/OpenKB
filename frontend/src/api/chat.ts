import { apiFetch, apiStream, type SseEvent } from "./client"

/**
 * A provenance "source" derived from a `tool_call` the agent made during a
 * turn.
 *
 * This is NOT a model-authored citation — the real backend emits no citations.
 * It is the set of wiki artifacts the agent actually *read*, reconstructed from
 * the live SSE `tool_call` stream via an explicit WHITELIST of tool names
 * (never a blacklist): only tools whose provenance we can name are surfaced;
 * every other tool name is ignored (see {@link toolCallSource}).
 *
 *  - kind `"page"`: a `read_file(path)` call. Clickable — the path resolves to
 *    a real wiki page via `/api/v1/page {kb, path}`.
 *  - kind `"doc"`: a `get_page_content(doc_name, pages)` call against a long
 *    (PageIndex) document's internal JSON metadata. No endpoint serves that, so
 *    it renders as a non-clickable label — still shown, so the answer's
 *    provenance is honest about having drawn on a long document, not only wiki
 *    pages.
 */
export interface Source {
  kind: "page" | "doc"
  /** Display label: the page path (kind `"page"`) or doc name (kind `"doc"`). */
  label: string
  /** Wiki-relative page path — present only for kind `"page"`. */
  path?: string
  /** Long-document name — present only for kind `"doc"`. */
  docName?: string
}

/**
 * Accumulated state for ONE assistant turn, folded from the SSE event stream.
 *
 * Sources are per-turn: dedupe happens within this object only, never across a
 * whole session. The same page read again in a later turn is a distinct, valid
 * source and must not be suppressed by an earlier turn.
 */
export interface ChatTurnState {
  /** Answer text: accumulated from `delta` events (progressive) and confirmed
   *  by the `final` event. */
  answer: string
  /** Whitelisted, per-turn-deduped provenance, in first-seen order. */
  sources: Source[]
  /** Most recent in-progress read, for a live "reading X…" indicator; null once
   *  the turn is idle or finished. */
  reading: Source | null
  /** Chat session id — only chat's `final` carries it; query turns stay null. */
  sessionId: string | null
  /** Where a saved query answer landed (query's `final.saved_path`). */
  savedPath: string | null
  /** Turn count from chat's `final`. */
  turnCount: number | null
  /** True once the terminal `done` event arrives. */
  done: boolean
  /** Error message from an `error` event, else null. */
  error: string | null
  /** Viewable HTML files this turn produced via the chat agent's write_file
   *  tool (from `artifact` SSE events). `kb` is captured at fold time (the
   *  active KB when the artifact event arrived), NOT re-attached at render
   *  time — the top-level `kb` state can change mid-session (KB dropdown
   *  switch), and a historical turn's file must keep pointing at the KB it
   *  was actually produced under. */
  artifacts: { path: string; name: string; kb: string }[]
}

export function initialTurnState(): ChatTurnState {
  return {
    answer: "",
    sources: [],
    reading: null,
    sessionId: null,
    savedPath: null,
    turnCount: null,
    done: false,
    error: null,
    artifacts: [],
  }
}

/** Normalize a wiki path for dedupe: strip a leading `./`, surrounding slashes,
 *  and a trailing `.md` so `summaries/x` and `summaries/x.md` collapse to one. */
function normalizePath(path: string): string {
  return path
    .trim()
    .replace(/^\.\//, "")
    .replace(/^\/+|\/+$/g, "")
    .replace(/\.md$/i, "")
}

function sourceKey(s: Source): string {
  return s.kind === "page"
    ? `page:${normalizePath(s.path ?? "")}`
    : `doc:${(s.docName ?? "").trim()}`
}

/**
 * The WHITELIST. Turn a `tool_call` event's `data` into a {@link Source}, or
 * `null` to drop it.
 *
 * Only `read_file` and `get_page_content` are recognised; any other tool name
 * (`get_image`, or a tool added to the agent later) yields `null` and is
 * ignored. This is deliberately a whitelist, not a blacklist: an unknown future
 * tool never becomes a broken or misleading chip.
 *
 * `arguments` is a JSON string — parsed defensively; a parse failure or a
 * missing required field yields `null` rather than a half-formed source.
 */
export function toolCallSource(data: unknown): Source | null {
  const d = (data ?? {}) as { name?: unknown; arguments?: unknown }
  const name = d.name
  if (name !== "read_file" && name !== "get_page_content") return null

  let args: Record<string, unknown> = {}
  const raw = d.arguments
  if (typeof raw === "string" && raw.trim()) {
    try {
      args = JSON.parse(raw) as Record<string, unknown>
    } catch {
      return null
    }
  } else if (raw && typeof raw === "object") {
    args = raw as Record<string, unknown>
  }

  if (name === "read_file") {
    const path = typeof args.path === "string" ? args.path.trim() : ""
    if (!path) return null
    return { kind: "page", label: path, path }
  }
  // get_page_content
  const docName = typeof args.doc_name === "string" ? args.doc_name.trim() : ""
  if (!docName) return null
  return { kind: "doc", label: docName, docName }
}

/** Append a source to a per-turn list, deduped by normalized key. First-seen
 *  order is preserved. */
function mergeSource(list: Source[], s: Source): Source[] {
  const key = sourceKey(s)
  return list.some((x) => sourceKey(x) === key) ? list : [...list, s]
}

/**
 * Defensively derive sources from a `final.history` array (the Agents SDK
 * `to_input_list()` record), if one is ever present.
 *
 * The current backend does NOT forward `history` over SSE — `_stream_query` and
 * `iter_chat_turn_events` deliberately strip it from the `final` frame to avoid
 * shipping large file/page contents to the browser — so this returns `null`
 * today and the live-accumulated `sources` are kept. It is retained as a
 * forward-compatible *preference*: if a future backend includes `history`, it
 * becomes the authoritative list and a dropped mid-stream `tool_call` can no
 * longer lose a source.
 */
export function sourcesFromHistory(history: unknown): Source[] | null {
  if (!Array.isArray(history)) return null
  let out: Source[] = []
  for (const item of history) {
    if (!item || typeof item !== "object") continue
    const record = item as { type?: unknown; name?: unknown; arguments?: unknown }
    const isCall =
      record.type === "function_call" ||
      (typeof record.name === "string" && "arguments" in record)
    if (!isCall) continue
    const src = toolCallSource({ name: record.name, arguments: record.arguments })
    if (src) out = mergeSource(out, src)
  }
  return out
}

/**
 * Fold one SSE event into the running turn state. Callers apply this per event
 * as the stream arrives (calling `setState`), so the UI reveals the answer and
 * the "reading" indicator live — no artificial delay.
 *
 * `kb` is the KB active at fold time (captured once by the caller at turn
 * start, e.g. `runTurn`'s `activeKb`), baked into any `artifact` produced this
 * turn — never re-derived from render-time state, so a later KB switch can't
 * retroactively repoint a historical turn's file artifact at the wrong KB.
 *
 * Real backend event shapes (verified against `openkb/api_helpers.py`):
 *   - `delta`     → `{ text }`            (incremental answer text)
 *   - `tool_call` → `{ name, arguments }` (arguments is a JSON string)
 *   - `final`     → query: `{ answer, saved_path }`;
 *                   chat:  `{ answer, session_id, turn_count }`
 *   - `error`     → `{ message }`
 *   - `artifact`  → `{ kind: "file", path, name }` (chat only, from a confirmed
 *                   `write_file` of a viewable `output/**.html`)
 *   - `done`      → `{}`
 *   - `start`     → ignored
 */
export function foldSseEvent(state: ChatTurnState, event: SseEvent, kb: string): ChatTurnState {
  const data = (event?.data ?? {}) as Record<string, unknown>
  switch (event?.event) {
    case "delta": {
      const text = typeof data.text === "string" ? data.text : ""
      return text ? { ...state, answer: state.answer + text } : state
    }
    case "tool_call": {
      const src = toolCallSource(data)
      if (!src) return state
      return { ...state, reading: src, sources: mergeSource(state.sources, src) }
    }
    case "final": {
      const histSources = sourcesFromHistory(data.history)
      const answer =
        typeof data.answer === "string" && data.answer ? data.answer : state.answer
      return {
        ...state,
        answer,
        sources: histSources ?? state.sources,
        reading: null,
        sessionId:
          typeof data.session_id === "string" ? data.session_id : state.sessionId,
        savedPath: typeof data.saved_path === "string" ? data.saved_path : state.savedPath,
        turnCount: typeof data.turn_count === "number" ? data.turn_count : state.turnCount,
      }
    }
    case "error": {
      const message = typeof data.message === "string" ? data.message : "请求失败"
      return { ...state, reading: null, error: message }
    }
    case "artifact": {
      // Forward-compat guard: only `kind: "file"` artifacts are folded here.
      // A future non-file `kind` must not be silently mis-tagged as a file.
      if (data.kind !== "file") return state
      const path = typeof data.path === "string" ? data.path.trim() : ""
      const name = typeof data.name === "string" ? data.name.trim() : ""
      if (!path || !name) return state
      if (state.artifacts.some((x) => x.path === path)) return state
      return { ...state, artifacts: [...state.artifacts, { path, name, kb }] }
    }
    case "done":
      return { ...state, reading: null, done: true }
    default:
      // "start" and any unknown event: ignore.
      return state
  }
}

// --- Stream wrappers over apiStream --------------------------------------

/**
 * One-off wiki Q&A (`/ask`): streams `delta`/`tool_call`/`final`. Stateless —
 * no chat session is created (`final` carries `saved_path`, never a session id).
 */
export async function* streamQuery(
  kb: string,
  question: string,
  opts: { save?: boolean } = {},
): AsyncGenerator<SseEvent> {
  yield* apiStream("/api/v1/query", {
    kb,
    question,
    stream: true,
    save: opts.save ?? false,
  })
}

/**
 * Multi-turn chat. A null `sessionId` starts a new session; the `final` event
 * carries the created `session_id`, which the caller should adopt for
 * subsequent turns.
 */
export async function* streamChat(
  kb: string,
  sessionId: string | null,
  message: string,
): AsyncGenerator<SseEvent> {
  yield* apiStream("/api/v1/chat", {
    kb,
    session_id: sessionId,
    message,
    stream: true,
  })
}

// --- Session listing / loading -------------------------------------------

export interface ChatSessionItem {
  id: string
  title: string
  turn_count: number
  updated_at: string
  model: string
}

export interface ChatSessionListResponse {
  kb: string
  sessions: ChatSessionItem[]
}

/** List a single KB's chat sessions (`/api/v1/chat/sessions`). There is no
 *  cross-KB aggregate endpoint; callers merge per-KB results client-side. */
export function listSessions(kb: string): Promise<ChatSessionListResponse> {
  return apiFetch<ChatSessionListResponse>("/api/v1/chat/sessions", { body: { kb } })
}

export interface ChatSessionLoad {
  session_id: string
  title: string
  turn_count: number
  user_turns: string[]
  assistant_texts: string[]
}

/** Load one session's turns for restore-on-reload (`/api/v1/chat/sessions/load`).
 *  Returns answer text only — per-turn sources are live-derived and not
 *  persisted, so restored turns render without source chips. */
export function loadSession(kb: string, sessionId: string): Promise<ChatSessionLoad> {
  return apiFetch<ChatSessionLoad>("/api/v1/chat/sessions/load", {
    body: { kb, session_id: sessionId },
  })
}
