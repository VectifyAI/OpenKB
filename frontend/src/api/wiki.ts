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

/** A document's ingested source text (`/api/v1/document/source`). This is the
 *  READ-ONLY conversion output stored under `wiki/sources/` — short docs are a
 *  single Markdown string (`total_pages=1`); long docs are paginated per
 *  source page. `page` is the 1-indexed page returned. */
export interface DocumentSource {
  hash: string
  name: string
  doc_name: string
  type: string
  format: string
  content: string
  page: number
  total_pages: number
}

/** Fetch one page of a document's converted text by its `hash` (the /list
 *  identifier). `page` is 1-indexed; long docs return just that page, short
 *  docs ignore it (always page 1 of 1). */
export function getDocumentSource(kb: string, hash: string, page = 1): Promise<DocumentSource> {
  return apiFetch<DocumentSource>("/api/v1/document/source", { body: { kb, hash, page } })
}

/** Result of `/api/v1/page/delete`. `backlinks` are 'section/stem' refs whose
 *  inbound [[links]] will be / were demoted to plain text. */
export interface PageDeleteResult {
  status: string
  target: string
  backlinks: string[]
  files_changed?: number | null
  ghosts_stripped?: number | null
}

/** Delete a concept/entity page. With `dryRun`, only reports the impact
 *  (backlinks) without changing anything — used for the confirm preview. */
export function deletePage(kb: string, path: string, dryRun = false): Promise<PageDeleteResult> {
  return apiFetch<PageDeleteResult>("/api/v1/page/delete", { body: { kb, path, dry_run: dryRun } })
}

/** Outbound + inbound links for a page (`/api/v1/page/links`) — the edit panel. */
export interface PageLinks {
  status: string
  target: string
  outlinks: string[]
  backlinks: string[]
}

export function getPageLinks(kb: string, path: string): Promise<PageLinks> {
  return apiFetch<PageLinks>("/api/v1/page/links", { body: { kb, path } })
}

/** Result of editing a page (`PUT /api/v1/page`). `ghosts_stripped` are the
 *  dead [[links]] demoted to plain text on save. */
export interface PageEditResult {
  status: string
  target: string
  ghosts_stripped: string[]
  content: string | null
}

/** Save new BODY for a concept/entity page (frontmatter preserved server-side). */
export function editPage(kb: string, path: string, content: string): Promise<PageEditResult> {
  return apiFetch<PageEditResult>("/api/v1/page", { method: "PUT", body: { kb, path, content } })
}
