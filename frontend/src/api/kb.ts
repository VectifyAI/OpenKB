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
