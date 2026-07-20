import { apiFetch } from "./client"

/** A concept-graph node. Shape mirrors `openkb.visualize.build_graph`. */
export interface GraphData {
  nodes: Array<{
    id: string
    label: string
    type: string
    description: string
    sources: string[]
    in: number
    out: number
  }>
  edges: Array<{ source: string; target: string }>
  types: string[]
}

/** One ingested source document, as reported by `/api/v1/list`. */
export interface WikiDocument {
  hash: string
  name: string
  type: string
  display_type: string
  pages: number | null
}

/**
 * The `/api/v1/list` response (`openkb.cli.get_kb_list` → `ListResponse`).
 *
 * `summaries`, `concepts`, and `entities` are page *stems* (no `.md`);
 * `reports` are full file *names* (with `.md`). The endpoint now surfaces
 * `entities/` pages alongside the other wiki types.
 */
export interface KbInventory {
  documents: WikiDocument[]
  document_count: number
  summaries: string[]
  concepts: string[]
  entities: string[]
  reports: string[]
}

export function getGraph(kb: string): Promise<GraphData> {
  return apiFetch<GraphData>("/api/v1/graph", { body: { kb } })
}

/** Fetch one wiki page's Markdown. `path` is relative to `wiki/` (`.md` optional). */
export function getPage(kb: string, path: string): Promise<{ path: string; content: string }> {
  return apiFetch<{ path: string; content: string }>("/api/v1/page", { body: { kb, path } })
}

export function getKbInventory(kb: string): Promise<KbInventory> {
  return apiFetch<KbInventory>("/api/v1/list", { body: { kb } })
}
