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

function baseUrl(): string {
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

/** SSE stream over fetch (EventSource can't set Authorization headers). */
export async function* apiStream(path: string, body: unknown): AsyncGenerator<SseEvent> {
  const token = getToken()
  const headers: Record<string, string> = { "Content-Type": "application/json" }
  if (token) headers["Authorization"] = `Bearer ${token}`

  const res = await fetch(baseUrl() + path, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
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
