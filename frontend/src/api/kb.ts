import { apiFetch } from "./client"

export interface KbSummary {
  name: string
  document_count: number
  last_compile: string | null
  has_raw: boolean
}

export interface KbListResponse {
  root: string
  knowledge_bases: KbSummary[]
}

export function listKbs(): Promise<KbListResponse> {
  return apiFetch<KbListResponse>("/api/v1/kbs")
}

export interface KbConfig {
  model: string
  language: string
  pageindex_threshold: number
  /** Plaintext LLM base URL (a config value, not a credential); null if unset. */
  openai_api_base: string | null
  /** Presence flag only — the raw key value is NEVER returned by the API. */
  has_api_key: boolean
}

/**
 * Shape of a config PATCH. Merge-patch (RFC 7386) semantics ride on the JSON
 * body: an OMITTED field (TS `undefined`, dropped by JSON.stringify) leaves the
 * value UNCHANGED; an explicit `null` CLEARS it; a value SETS it. Callers MUST
 * therefore never send `api_key: ""` — the empty string is a real "set an empty
 * key" request, not "unchanged" or "clear".
 */
export interface KbConfigPatch {
  config?: Partial<Pick<KbConfig, "model" | "language" | "pageindex_threshold">>
  api_key?: string | null
  openai_api_base?: string | null
}

export function getKbConfig(kb: string): Promise<KbConfig> {
  return apiFetch<KbConfig>(`/api/v1/kb/config?kb=${encodeURIComponent(kb)}`)
}

export function patchKbConfig(kb: string, patch: KbConfigPatch): Promise<KbConfig> {
  return apiFetch<KbConfig>("/api/v1/kb/config", { method: "PATCH", body: { kb, ...patch } })
}
