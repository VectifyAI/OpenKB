import { apiFetch, apiStream, getToken, getApiBase } from "./client"
import i18n from "@/lib/i18n"

/** One file's outcome in an `/api/v1/add` response (`AddFileItem`). */
export interface AddFileItem {
  original_name: string
  saved_path: string | null
  status: string
  message: string
}

/** `/api/v1/add` JSON response (`AddResponse`). */
export interface AddResult {
  kb: string
  files: AddFileItem[]
  added_count: number
  skipped_count: number
  failed_count: number
}

/** One entry in a watcher's `recent_events` ring buffer (`WatchEventItem`). */
export interface WatchEventItem {
  ts: number
  event: string
  data: Record<string, unknown>
}

/**
 * `/api/v1/watch/{start,stop,status}` response (`WatchStatusResponse`).
 * When no watcher exists the backend returns just `{ kb, active: false }`;
 * the optional fields are only present while a watcher is (or was) live.
 */
export interface WatchStatus {
  kb: string
  active: boolean
  started_at?: number | null
  raw_dir?: string | null
  debounce?: number | null
  counters: Record<string, number>
  recent_events: WatchEventItem[]
}

/**
 * One event surfaced by a streaming `POST /api/v1/add` (`_stream_add_uploads`).
 * The backend also emits `start` / `uploaded` / `done` frames that carry no
 * per-file UI signal, so they are collapsed away here.
 */
export type UploadEvent =
  | { type: "file_start"; index: number; original_name: string }
  | { type: "file_done"; index: number; file: AddFileItem }
  | { type: "final"; result: AddResult }
  | { type: "error"; message: string }

/**
 * Upload documents via multipart `POST /api/v1/add` with `stream=true`, calling
 * `onEvent` as the backend reports per-file progress over SSE.
 *
 * This does NOT go through `apiStream` because the body is `FormData`, not JSON
 * — but the bearer token must still be attached by hand so the request carries
 * `Authorization` when a token is set. The SSE frame parsing mirrors
 * `apiStream` in `client.ts`. Events map to `_stream_add_uploads`:
 *   `file_start` → compile starting for one file,
 *   `file_done`  → that file's `AddFileItem` (status added/skipped/failed),
 *   `final`      → the summary `AddResponse`,
 *   `error`      → a stream-level failure message.
 *
 * The backend emits per-file events strictly in upload order (one `file_start`
 * then one `file_done` per file, `saved_uploads` order == `files` order), so
 * `file_start`/`file_done` carry a running `index` the caller uses to correlate
 * an event to its seeded row by position — robust to duplicate basenames, which
 * name-matching would collide.
 *
 * Pass an optional `signal` to make the upload cancellable — aborting it rejects
 * the in-flight `fetch`/`reader.read()` with an `AbortError` that propagates out
 * (mirrors `apiStream`/`runRecompile`), so navigating away mid-upload cancels it.
 */
export async function streamUpload(
  kb: string,
  files: File[],
  onEvent: (ev: UploadEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const form = new FormData()
  form.append("kb", kb)
  form.append("stream", "true")
  files.forEach((f) => form.append("files", f))
  const token = getToken()
  const res = await fetch(getApiBase().replace(/\/$/, "") + "/api/v1/add", {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: form,
    signal,
  })
  if (!res.ok || !res.body) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const j = await res.json()
      detail = typeof j.detail === "string" ? j.detail : detail
    } catch {
      // keep default message
    }
    throw new Error(i18n.t("common:errors.uploadFailed", { detail }))
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ""
  // Running cursor over the seeded rows: `file_start` advances it, the matching
  // `file_done` reuses it. See the ordering note above.
  let fileCursor = -1
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    const blocks = buf.split("\n\n")
    buf = blocks.pop() ?? ""
    for (const block of blocks) {
      const lines = block.split("\n")
      const eventLine = lines.find((l) => l.startsWith("event: "))
      const dataLine = lines.find((l) => l.startsWith("data: "))
      if (!eventLine || !dataLine) continue
      const event = eventLine.slice("event: ".length)
      let data: any
      try {
        data = JSON.parse(dataLine.slice("data: ".length))
      } catch {
        // Malformed / non-JSON data frame — skip it like a robust SSE reader
        // instead of throwing out of the whole read loop.
        continue
      }
      switch (event) {
        case "file_start":
          fileCursor += 1
          onEvent({ type: "file_start", index: fileCursor, original_name: data.original_name })
          break
        case "file_done":
          onEvent({ type: "file_done", index: fileCursor, file: data as AddFileItem })
          break
        case "final":
          onEvent({ type: "final", result: data as AddResult })
          break
        case "error":
          onEvent({ type: "error", message: String(data.message ?? "") })
          break
        // `start` / `uploaded` / `done` carry no per-file UI signal.
      }
    }
  }
}

/** `/api/v1/remove` response (`RemoveResponse`) — the subset the UI reads.
 *  `status` is `removed` on full success or `partial` when the local wiki files
 *  were removed but PageIndex cleanup failed (both are HTTP 200); on `partial`,
 *  `pageindex_error` carries why the remote cleanup failed. */
export interface RemoveResult {
  status: string
  name?: string | null
  doc_name?: string | null
  pageindex_error?: string | null
  message?: string | null
}

/**
 * Remove one document from a KB via `POST /api/v1/remove`.
 *
 * `identifier` is resolved by the backend (`_resolve_doc_identifier`) against
 * the original filename first (`metadata['name']`), then the slug
 * (`doc_name`), then a case-insensitive substring — so passing a document's
 * exact original `name` gives a deterministic single hit. A 404 means no
 * match; a 409 means the identifier matched multiple documents. Both surface
 * as an `ApiError` from `apiFetch`.
 */
export function removeDocument(kb: string, identifier: string): Promise<RemoveResult> {
  return apiFetch<RemoveResult>("/api/v1/remove", { body: { kb, identifier } })
}

export function watchStart(kb: string, debounce?: number): Promise<WatchStatus> {
  return apiFetch<WatchStatus>("/api/v1/watch/start", { body: { kb, debounce } })
}
export function watchStop(kb: string): Promise<WatchStatus> {
  return apiFetch<WatchStatus>("/api/v1/watch/stop", { body: { kb } })
}
export function watchStatus(kb: string): Promise<WatchStatus> {
  return apiFetch<WatchStatus>("/api/v1/watch/status", { body: { kb } })
}

/**
 * Stream a recompile (`POST /api/v1/recompile`). Emits SSE events
 * `start` / `doc` / `final` / `error` / `done` (see `_stream_recompile`).
 * With no `docName` it recompiles every indexed doc (`all_docs: true`).
 *
 * Pass an optional `signal` to make the stream cancellable — the caller aborts
 * it to stop the SSE (e.g. when the settings sheet closes) without leaving the
 * recompile running headless. Backward-compatible: omit it and nothing changes.
 */
export function runRecompile(kb: string, docName?: string, signal?: AbortSignal) {
  return apiStream(
    "/api/v1/recompile",
    {
      kb,
      doc_name: docName,
      all_docs: !docName,
      stream: true,
    },
    signal,
  )
}

export function runLint(kb: string, fix: boolean): Promise<unknown> {
  return apiFetch<unknown>("/api/v1/lint", { body: { kb, fix } })
}
