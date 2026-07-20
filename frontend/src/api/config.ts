import { apiFetch } from "./client"

/** Global default scalars (DEFAULT-filled when global.yaml is silent), plus the
 *  global-default credentials read from ~/.config/openkb/.env. */
export interface GlobalConfig {
  model: string
  language: string
  pageindex_threshold: number
  /** Plaintext global-default LLM base URL (a config value, not a secret);
   *  null if unset. Mirrors KbConfig.openai_api_base. */
  openai_api_base?: string | null
  /** Presence flag only — the raw key value is NEVER returned by the API. */
  has_api_key?: boolean
}

/**
 * Merge-patch body for the global defaults. RFC 7386 semantics: an OMITTED
 * field leaves it unchanged; an explicit `null` reverts it to the built-in
 * default (scalars) / removes it from the global .env (credentials); a value
 * sets it. As with KbConfigPatch, never send `api_key: ""` — that is a real
 * "set an empty key" request, not "unchanged" or "clear".
 */
export interface GlobalConfigPatch {
  config?: {
    model?: string | null
    language?: string | null
    pageindex_threshold?: number | null
  }
  api_key?: string | null
  openai_api_base?: string | null
}

export function getGlobalConfig(): Promise<GlobalConfig> {
  return apiFetch<GlobalConfig>("/api/v1/config")
}

export function patchGlobalConfig(patch: GlobalConfigPatch): Promise<GlobalConfig> {
  return apiFetch<GlobalConfig>("/api/v1/config", { method: "PATCH", body: patch })
}
