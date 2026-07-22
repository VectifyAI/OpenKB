import { apiFetch } from "./client"

/** Global default scalars (DEFAULT-filled when global.yaml is silent), plus the
 *  global-default credentials read from ~/.config/openkb/.env. */
export interface GlobalConfig {
  model: string
  language: string
  pageindex_threshold: number
  /** CLEANED effective global entity-extraction vocabulary (always includes
   *  "other"). Mirrors KbConfig.entity_types. */
  entity_types: string[]
  /** Plaintext global-default LLM base URL (a config value, not a secret);
   *  null if unset. Mirrors KbConfig.openai_api_base. */
  openai_api_base?: string | null
  /** Presence flag only — the raw key value is NEVER returned by the API. */
  has_api_key?: boolean
  /** Effective KB root directory — where `<root>/<name>` KBs are created. */
  kb_root: string
  /** True when OPENKB_KB_ROOT is set in the environment, which overrides any
   *  UI-set root; edits here won't take effect until the env var is unset. */
  kb_root_env_pinned?: boolean
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
    /** A list sets the global vocabulary; `null` reverts to the built-in
     *  default. Cleaned server-side (lowercase, `[a-z0-9 _-]`, + "other"). */
    entity_types?: string[] | null
  }
  api_key?: string | null
  openai_api_base?: string | null
  /** Effective KB root. A value sets it; `null` (or empty) reverts to the
   *  built-in default. Placed top-level, mirroring api_key/openai_api_base. */
  kb_root?: string | null
}

export function getGlobalConfig(): Promise<GlobalConfig> {
  return apiFetch<GlobalConfig>("/api/v1/config")
}

export function patchGlobalConfig(patch: GlobalConfigPatch): Promise<GlobalConfig> {
  return apiFetch<GlobalConfig>("/api/v1/config", { method: "PATCH", body: patch })
}
