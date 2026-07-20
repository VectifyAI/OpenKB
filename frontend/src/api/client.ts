const LS_BASE = "openkb_api_base"
const LS_TOKEN = "openkb_token"

export function getApiBase(): string {
  return localStorage.getItem(LS_BASE) || ""
}
export function getToken(): string {
  return localStorage.getItem(LS_TOKEN) || ""
}
export function setConnection(apiBase: string, token: string): void {
  localStorage.setItem(LS_BASE, apiBase)
  localStorage.setItem(LS_TOKEN, token)
}

/**
 * Warn ONCE if the configured API base is cross-origin AND served over plain
 * `http:` — the bearer token is attached to every request, so such a base would
 * send it in the clear. Advisory only (console; no UI) and never blocking:
 * same-origin or a relative base (the production mount at `/`) is always safe
 * and never warns; a LAN/dev `http://` endpoint still works, just with a
 * heads-up. The flag flips only once we actually warn, so a base later switched
 * from safe to unsafe can still surface the warning.
 */
let baseSafetyWarned = false
function checkBaseSafety(): void {
  if (baseSafetyWarned) return
  const base = getApiBase()
  if (!base) return // same-origin
  try {
    const url = new URL(base, window.location.origin)
    if (url.origin !== window.location.origin && url.protocol === "http:") {
      baseSafetyWarned = true
      console.warn(
        `[OpenKB] API base "${base}" is cross-origin and served over http:. ` +
          "Your API token is sent to this host in the clear on every request. " +
          "Use https for a remote API, or verify this URL is trusted.",
      )
    }
  } catch {
    // Not a parseable URL — it fails at fetch time; nothing to warn about here.
  }
}

function baseUrl(): string {
  // Called by every request helper below (JSON, blob, SSE), so this is the one
  // place the base/token is first used — the right spot for the advisory.
  checkBaseSafety()
  return getApiBase().replace(/\/$/, "")
}

let unauthorizedHandler: (() => void) | null = null
export function onUnauthorized(cb: () => void): void {
  unauthorizedHandler = cb
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

interface FetchOpts {
  method?: string
  body?: unknown
}

/** JSON request/response helper. Attaches the bearer token when present. */
export async function apiFetch<T>(path: string, opts: FetchOpts = {}): Promise<T> {
  const token = getToken()
  const headers: Record<string, string> = {}
  if (opts.body !== undefined) headers["Content-Type"] = "application/json"
  if (token) headers["Authorization"] = `Bearer ${token}`

  const res = await fetch(baseUrl() + path, {
    method: opts.method ?? (opts.body !== undefined ? "POST" : "GET"),
    headers,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  })

  if (res.status === 401) unauthorizedHandler?.()
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const j = await res.json()
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j)
    } catch {
      // keep default message
    }
    throw new ApiError(res.status, detail)
  }

  const ct = res.headers.get("content-type") || ""
  if (ct.includes("application/json")) return res.json() as Promise<T>
  return res.text() as unknown as Promise<T>
}

/**
 * Fetch raw bytes with the bearer header attached, returning a blob: URL.
 * Use this for anything an <iframe>/<a download> needs to point at — those
 * elements cannot carry an Authorization header, and the token must never
 * appear in a query string.
 */
export async function fetchAsBlobUrl(path: string): Promise<string> {
  const token = getToken()
  const headers: Record<string, string> = {}
  if (token) headers["Authorization"] = `Bearer ${token}`
  const res = await fetch(baseUrl() + path, { headers })
  if (res.status === 401) unauthorizedHandler?.()
  if (!res.ok) throw new ApiError(res.status, `${res.status} ${res.statusText}`)
  const blob = await res.blob()
  return URL.createObjectURL(blob)
}

export interface SseEvent {
  event: string
  data: any
}

/**
 * SSE stream over fetch (EventSource can't set Authorization headers).
 *
 * Pass an optional `signal` to make the stream cancellable: aborting it rejects
 * the in-flight `fetch`/`reader.read()` with an `AbortError`, which propagates
 * out of this generator so the consumer's `for await` throws. The consumer can
 * then distinguish a user-abort (`e.name === "AbortError"` / `signal.aborted`)
 * from a real error and settle silently — see `ChatSession.runTurn`.
 */
export async function* apiStream(
  path: string,
  body: unknown,
  signal?: AbortSignal,
): AsyncGenerator<SseEvent> {
  const token = getToken()
  const headers: Record<string, string> = { "Content-Type": "application/json" }
  if (token) headers["Authorization"] = `Bearer ${token}`

  const res = await fetch(baseUrl() + path, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    signal,
  })
  if (res.status === 401) unauthorizedHandler?.()
  if (!res.ok || !res.body) {
    throw new ApiError(res.status, `${res.status} ${res.statusText}`)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ""
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
      yield {
        event: eventLine.slice("event: ".length),
        data: JSON.parse(dataLine.slice("data: ".length)),
      }
    }
  }
}
