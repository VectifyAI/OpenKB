import { apiFetch, apiStream, getToken, getApiBase } from "./client"

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
 * Upload documents via multipart `POST /api/v1/add`.
 *
 * This does NOT go through `apiFetch` because the body is `FormData`, not JSON
 * — but the bearer token must still be attached by hand so the request carries
 * `Authorization` when a token is set. `stream=false` forces the JSON
 * `AddResponse` branch; without it the endpoint defaults to an SSE stream and
 * `res.json()` would fail.
 */
export async function uploadDocuments(kb: string, files: File[]): Promise<AddResult> {
  const form = new FormData()
  form.append("kb", kb)
  form.append("stream", "false")
  files.forEach((f) => form.append("files", f))
  const token = getToken()
  const res = await fetch(getApiBase().replace(/\/$/, "") + "/api/v1/add", {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: form,
  })
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const j = await res.json()
      detail = typeof j.detail === "string" ? j.detail : detail
    } catch {
      // keep default message
    }
    throw new Error(`上传失败：${detail}`)
  }
  return res.json() as Promise<AddResult>
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
 */
export function runRecompile(kb: string, docName?: string) {
  return apiStream("/api/v1/recompile", {
    kb,
    doc_name: docName,
    all_docs: !docName,
    stream: true,
  })
}

export function runLint(kb: string, fix: boolean): Promise<unknown> {
  return apiFetch<unknown>("/api/v1/lint", { body: { kb, fix } })
}
