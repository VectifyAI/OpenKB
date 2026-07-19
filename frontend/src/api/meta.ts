import { apiFetch } from "./client"

export interface Meta {
  version: string
}

/** App metadata (the real installed openkb version). */
export function getMeta(): Promise<Meta> {
  return apiFetch<Meta>("/api/v1/meta")
}
