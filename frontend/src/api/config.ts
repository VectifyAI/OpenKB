import { apiFetch } from "./client"

/** Global default scalars (DEFAULT-filled when global.yaml is silent). */
export interface GlobalConfig {
  model: string
  language: string
  pageindex_threshold: number
}

/**
 * Merge-patch body for the global defaults. RFC 7386 semantics: an OMITTED
 * field leaves it unchanged; an explicit `null` reverts it to the built-in
 * default; a value sets it.
 */
export interface GlobalConfigPatch {
  config?: {
    model?: string | null
    language?: string | null
    pageindex_threshold?: number | null
  }
}

export function getGlobalConfig(): Promise<GlobalConfig> {
  return apiFetch<GlobalConfig>("/api/v1/config")
}

export function patchGlobalConfig(patch: GlobalConfigPatch): Promise<GlobalConfig> {
  return apiFetch<GlobalConfig>("/api/v1/config", { method: "PATCH", body: patch })
}
