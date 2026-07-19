import { apiStream, fetchAsBlobUrl } from "./client"

/**
 * Deck/skill generator streams. Both post `{ kb, name, intent, stream: true }`
 * and emit SSE events `start` / `error` / `final` / `done`, where the `final`
 * event's data is `{ name, status, path }` (see `_iter_deck`/`_iter_skill` in
 * `openkb/api_helpers.py`). NOTE: this `final` shape differs from chat/query's
 * `final` — do not fold these through `mapSseEvent`/`foldSseEvent`.
 */
export function runDeckCommand(kb: string, name: string, intent: string) {
  return apiStream("/api/v1/deck", { kb, name, intent, stream: true })
}

export function runSkillCommand(kb: string, name: string, intent: string) {
  return apiStream("/api/v1/skill", { kb, name, intent, stream: true })
}

/**
 * Fetch the rendered deck HTML with the bearer token attached CLIENT-SIDE and
 * return a `blob:` URL. The returned URL is what a sandboxed `<iframe src>` must
 * point at — NEVER point an iframe/`<a download>` directly at this authenticated
 * `/api/...` endpoint, and never put the token in a query string. Caller owns
 * the returned URL and MUST `URL.revokeObjectURL` it when done.
 */
export function getDeckBlobUrl(kb: string, name: string): Promise<string> {
  return fetchAsBlobUrl(
    `/api/v1/deck/${encodeURIComponent(name)}?kb=${encodeURIComponent(kb)}`,
  )
}

/**
 * Fetch the rendered knowledge-graph HTML (self-contained) with the bearer token
 * attached CLIENT-SIDE and return a `blob:` URL for a sandboxed `<iframe src>`.
 * Same rules as {@link getDeckBlobUrl}: never a raw href to the API path, never a
 * token in a query string; caller MUST `URL.revokeObjectURL` when done.
 */
export function getGraphBlobUrl(kb: string): Promise<string> {
  return fetchAsBlobUrl(`/api/v1/graph/html?kb=${encodeURIComponent(kb)}`)
}

/**
 * Fetch the skill's `.zip` archive with the bearer token attached CLIENT-SIDE
 * and return a `blob:` URL suitable for a temporary `<a download>`. Same rules
 * as {@link getDeckBlobUrl}: never a raw href to the API path; caller must
 * revoke the URL.
 */
export function getSkillArchiveBlobUrl(kb: string, name: string): Promise<string> {
  return fetchAsBlobUrl(
    `/api/v1/skill/${encodeURIComponent(name)}/archive?kb=${encodeURIComponent(kb)}`,
  )
}

/**
 * Fetch a conversationally-written viewable HTML artifact (output/**.html) with
 * the bearer token attached CLIENT-SIDE and return a `blob:` URL for a sandboxed
 * `<iframe src>`. Same rules as {@link getDeckBlobUrl}: never a raw href to the
 * API path, never a token in a query string; caller MUST `URL.revokeObjectURL`.
 */
export function getOutputBlobUrl(kb: string, path: string): Promise<string> {
  return fetchAsBlobUrl(
    `/api/v1/output?kb=${encodeURIComponent(kb)}&path=${encodeURIComponent(path)}`,
  )
}
