import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from 'react'
import { useNavigate, useParams } from 'react-router'
import { useTranslation } from 'react-i18next'
import { motion, useReducedMotion } from 'motion/react'
import { FileText, Loader2, Upload, RefreshCw, Settings2, Trash2, Circle, CheckCircle2, CircleSlash2, XCircle } from 'lucide-react'
import { toast } from 'sonner'
import { getKbInventory, getPage, type KbInventory, type WikiDocument } from '@/api/wiki'
import { streamUpload, removeDocument, type AddResult } from '@/api/maintenance'
import { ApiError } from '@/api/client'
import MarkdownView from '@/components/MarkdownView'
import PageList from '@/components/PageList'
import ConnectorCards from '@/components/ConnectorCards'
import KbOverviewCards, { type Section } from '@/components/KbOverviewCards'
import KbSettingsSheet from '@/components/KbSettingsSheet'
import { useAnimatedSwitch } from '@/hooks/useAnimatedSwitch'
import { cn } from '@/lib/utils'

/** True when `line` looks like a line of a YAML frontmatter block: a blank line,
 *  a `#` comment, a `- ` list item, an indented continuation, or a `key: value`
 *  mapping entry whose value is YAML-shaped (empty, quoted, a `[`/`{` flow
 *  collection, or a single bare token). A mapping value that is free prose
 *  (multiple unquoted words, e.g. `see below`) is NOT YAML-shaped — that is what
 *  separates real OKF frontmatter (values are always JSON-quoted) from a prose
 *  line like `Note: see below`. ASCII-only. */
function looksLikeYamlLine(line: string): boolean {
  if (line.trim() === '') return true
  if (/^[ \t]*#/.test(line)) return true // comment
  if (/^[ \t]*-([ \t]|$)/.test(line)) return true // list item
  if (/^[ \t]+\S/.test(line)) return true // indented continuation / nested block
  const m = /^[ \t]*[\w.-]+[ \t]*:([ \t]+(.*))?$/.exec(line)
  if (!m) return false // no `key:` mapping — a prose line
  const value = (m[2] ?? '').trim()
  if (value === '') return true // `key:` with an empty / block value
  if (/^["'[{]/.test(value)) return true // quoted string or flow collection
  return !/\s/.test(value) // a single bare scalar (Concept / 42 / true), not prose
}

/** Strip a leading YAML frontmatter block (`--- ... ---`) from a raw wiki page.
 *  Pages are served verbatim by `GET /api/v1/page`, so an OKF frontmatter block
 *  would otherwise render in the reader as junk metadata lines — and, now that
 *  MarkdownView renders thematic breaks, its `---` delimiters as horizontal
 *  rules. The block is stripped ONLY when it genuinely looks like frontmatter:
 *  it opens at the VERY START of the document, is closed by a line-anchored
 *  `---`, and EVERY non-blank inner line looks like YAML (see `looksLikeYamlLine`).
 *  A block containing a prose line is left intact, so a body that legitimately
 *  opens with a `---` thematic break followed by prose (`---\nIntro paragraph\n---`
 *  or `---\nNote: see below\n---`) is NOT mistaken for frontmatter. Real OKF
 *  frontmatter (`title:`/`type:`/`links:`, values JSON-quoted) still strips.
 *  No-op when there is no leading frontmatter. Only the reader strips it; chat
 *  answers (which carry no frontmatter) go through MarkdownView untouched.
 *  ASCII-only. */
function stripFrontmatter(md: string): string {
  const m = /^---[ \t]*\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)/.exec(md)
  if (!m) return md
  if (!m[1].split(/\r?\n/).every(looksLikeYamlLine)) return md
  return md.slice(m[0].length)
}

/** Per-file lifecycle during a streaming upload. `pending` → `processing`
 *  (backend `file_start`) → terminal `added`/`skipped`/`failed` (`file_done`,
 *  the `AddFileItem.status`). */
type UploadStatus = 'pending' | 'processing' | 'added' | 'skipped' | 'failed'
interface UploadFileState {
  /** Stable generated id, assigned once at seed time — the React key. Rows are
   *  correlated to backend events by their array index, not this id or the
   *  basename (two same-basename files from different folders must not collide). */
  id: string
  name: string
  status: UploadStatus
  message?: string
}

/** One selected wiki page, derived from its `<type>/<name>` path. */
interface SelectedPage {
  /** Path passed to `/api/v1/page` (relative to `wiki/`). */
  path: string
  /** Folder label with trailing slash, e.g. `concepts/`. */
  group: string
  /** Display filename, always with `.md`. */
  title: string
}

/**
 * Parse a `<type>/<name>` wiki path into its display parts. `reports` names
 * already carry `.md`; `summaries`/`concepts`/`entities` are stems, so append
 * `.md` for display only.
 */
function parseSelected(path: string | null): SelectedPage | null {
  if (!path) return null
  const slash = path.indexOf('/')
  if (slash < 0) return { path, group: '', title: path }  // root file, e.g. index.md
  const type = path.slice(0, slash)
  const name = path.slice(slash + 1)
  return { path, group: `${type}/`, title: type === 'reports' ? name : `${name}.md` }
}

/** Total compiled pages across all wiki types. */
function pageTotal(inv: KbInventory): number {
  return inv.concepts.length + inv.entities.length + inv.summaries.length + inv.reports.length
}

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e))

export default function KbDetail() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const { t } = useTranslation(['kb', 'common'])

  const [inv, setInv] = useState<KbInventory | null>(null)
  const [invError, setInvError] = useState<string | null>(null)
  const [selectedPath, setSelectedPath] = useState<string | null>(null)

  // Nav-card selection (Sub-project G): the six cards ARE the tab bar. The
  // active card drives which below-area layout renders (Task 13).
  const [section, setSection] = useState<Section>('index')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const reduce = useReducedMotion()
  // Frequency-gated enter spring (Apple §3): rapid card clicking renders the
  // next section instantly; a settled selection gets the gentle spring.
  const animateSwitch = useAnimatedSwitch(section)

  // Page content and error are tagged with the path they belong to so a stale
  // response never renders under a newly selected page.
  const [page, setPage] = useState<{ path: string; content: string } | null>(null)
  const [pageError, setPageError] = useState<{ path: string; message: string } | null>(null)

  // Documents section: upload state. `uploadFiles` tracks per-file progress
  // for the current/most-recent streaming upload; it is reset at the start of
  // each upload and kept afterwards so final per-file statuses stay visible.
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadFiles, setUploadFiles] = useState<UploadFileState[]>([])
  const [dragActive, setDragActive] = useState(false)
  // Monotonic counter for per-row ids (React keys), and the AbortController for
  // the in-flight upload — aborted on unmount so navigating away mid-upload
  // cancels the request (mirrors the recompile/chat abort discipline).
  const rowIdSeq = useRef(0)
  const uploadAbortRef = useRef<AbortController | null>(null)
  useEffect(() => () => uploadAbortRef.current?.abort(), [])

  const selected = useMemo(() => parseSelected(selectedPath), [selectedPath])
  const openPath = useCallback((path: string) => setSelectedPath(path), [])

  /** Navigate a `[[type/name]]` wikilink: open its exact page, and if the
   *  type matches a section card (concepts/entities/summaries/reports),
   *  switch the active card so the Overview highlight follows the link —
   *  mirrors `selectSection`'s card-highlight behavior, but opens the
   *  clicked target instead of the section's first page. */
  const onWikiLink = useCallback(
    (target: string) => {
      const slash = target.indexOf('/')
      const type = slash < 0 ? '' : target.slice(0, slash)
      if (type === 'concepts' || type === 'entities' || type === 'summaries' || type === 'reports') {
        setSection(type)
      }
      openPath(target)
    },
    [openPath],
  )

  // Load the inventory, then auto-select the first page. State is only ever
  // set inside the async callbacks (never synchronously in the effect body).
  // The component is remounted per KB via `key` in App, so no reset is needed.
  useEffect(() => {
    let cancelled = false
    getKbInventory(id)
      .then((r) => {
        if (cancelled) return
        setInv(r)
        // Land on the wiki home (index.md) like a real wiki, not the first concept.
        setSection('index')
        setSelectedPath('index.md')
      })
      .catch((e) => {
        if (!cancelled) setInvError(errMsg(e))
      })
    return () => {
      cancelled = true
    }
  }, [id])

  // Fetch the selected page's Markdown from the real endpoint.
  useEffect(() => {
    if (!selectedPath) return
    const path = selectedPath
    let cancelled = false
    getPage(id, path)
      .then((r) => {
        if (cancelled) return
        setPage({ path, content: stripFrontmatter(r.content) })
        setPageError(null)
      })
      .catch((e) => {
        if (!cancelled) setPageError({ path, message: errMsg(e) })
      })
    return () => {
      cancelled = true
    }
  }, [id, selectedPath])

  /** Re-fetch the inventory (after an upload / recompile that changed docs). */
  const refreshInventory = useCallback(async () => {
    try {
      const r = await getKbInventory(id)
      setInv(r)
      setInvError(null)
    } catch (e) {
      setInvError(errMsg(e))
    }
  }, [id])

  const doUpload = useCallback(
    async (files: File[]) => {
      if (files.length === 0 || uploading) return
      setUploading(true)
      // Seed one row per selected file so the UI shows the full set immediately,
      // then flip each to processing/terminal as SSE events arrive. Each row gets
      // a stable generated id (its React key); rows are correlated to backend
      // events by array index (event order == files order), NOT by basename, so
      // two same-basename files from different folders no longer collide.
      setUploadFiles(files.map((f) => ({ id: String(rowIdSeq.current++), name: f.name, status: 'pending' as const })))
      // Fresh controller for this upload — aborted on unmount.
      const controller = new AbortController()
      uploadAbortRef.current = controller
      let summary: AddResult | null = null
      let streamError: string | null = null
      try {
        await streamUpload(
          id,
          files,
          (ev) => {
            if (ev.type === 'file_start') {
              setUploadFiles((prev) =>
                prev.map((f, i) => (i === ev.index ? { ...f, status: 'processing' } : f)),
              )
            } else if (ev.type === 'file_done') {
              setUploadFiles((prev) =>
                prev.map((f, i) =>
                  i === ev.index
                    ? { ...f, status: ev.file.status as UploadStatus, message: ev.file.message }
                    : f,
                ),
              )
            } else if (ev.type === 'final') {
              summary = ev.result
            } else if (ev.type === 'error') {
              streamError = ev.message
            }
          },
          controller.signal,
        )
        // A stream-level `error` frame, or a stream that ends WITHOUT a `final`
        // summary, must not leave rows spinning forever — settle any still-
        // unresolved (pending/processing) rows to failed.
        if (streamError || !summary) {
          setUploadFiles((prev) =>
            prev.map((f) =>
              f.status === 'pending' || f.status === 'processing' ? { ...f, status: 'failed' as const } : f,
            ),
          )
        }
        // `/api/v1/add` reports HTTP 200 even when per-file compile fails, so a
        // clean stream does NOT mean success — branch on the real
        // added/failed/skipped counts from the `final` summary event.
        if (streamError) {
          toast.error(streamError)
        } else if (summary) {
          const res: AddResult = summary
          const parts = [t('kb:upload.added', { count: res.added_count })]
          if (res.skipped_count) parts.push(t('kb:upload.skipped', { count: res.skipped_count }))
          if (res.failed_count) parts.push(t('kb:upload.failed', { count: res.failed_count }))
          const line = parts.join(' · ')
          if (res.added_count === 0 && res.failed_count > 0) {
            // Every file failed — surface the first failure's message so the
            // user learns WHY (e.g. compile error / missing LLM API key).
            const reason = res.files.find((f) => f.status === 'failed')?.message
            toast.error(t('kb:upload.errorToast', { summary: line }) + (reason ? t('kb:upload.reasonSuffix', { reason }) : ''))
          } else if (res.failed_count > 0) {
            // Some added, some failed — not a clean success.
            toast.warning(t('kb:upload.partialToast', { summary: line }))
          } else if (res.added_count > 0) {
            toast.success(t('kb:upload.successToast', { summary: line }))
          } else {
            // Nothing added or failed (all skipped duplicates) — neutral, not
            // an error and not a "added" success.
            toast.info(t('kb:upload.existsToast', { summary: line }))
          }
        } else {
          // Stream ended cleanly but never delivered a `final` summary and no
          // explicit `error` frame — treat as an interrupted upload so the user
          // isn't left with a silent no-op.
          toast.error(t('kb:upload.incompleteToast'))
        }
        // Refresh regardless of outcome: a failed compile may still have
        // written a raw file, and the user needs to see current state.
        await refreshInventory()
      } catch (e) {
        // An abort (component unmounted / navigated away mid-upload) is a clean
        // cancel: no toast, no refresh — the component is gone. Real failures
        // still surface.
        const aborted = controller.signal.aborted || (e as { name?: string })?.name === 'AbortError'
        if (!aborted) toast.error(errMsg(e))
      } finally {
        setUploading(false)
      }
    },
    [id, uploading, refreshInventory, t],
  )

  /** Remove one document via `/api/v1/remove`, then refresh + toast. The
   *  identifier is the document's original filename (`WikiDocument.name`),
   *  which the backend resolves by exact-name match first. `/api/v1/remove`
   *  returns HTTP 200 for both `removed` (full success) and `partial` (local
   *  files gone, PageIndex cleanup failed), so success is claimed ONLY on
   *  `removed`; `partial` warns and surfaces the PageIndex error. A 409
   *  multiple-match carries a structured `{ message, candidates }` detail — its
   *  candidate names are shown so the user can disambiguate. */
  const onDeleteDocument = useCallback(
    async (identifier: string) => {
      try {
        const res = await removeDocument(id, identifier)
        if (res.status === 'partial') {
          // HTTP 200, but PageIndex cleanup failed: local wiki files were removed
          // while the remote index was not. Warn (not success) and say why.
          const reason = res.pageindex_error || res.message || ''
          toast.warning(
            t('kb:docs.delete.partial', { name: res.name || identifier }) +
              (reason ? t('kb:docs.delete.reasonSuffix', { reason }) : ''),
          )
        } else {
          toast.success(t('kb:docs.delete.success', { name: res.name || identifier }))
        }
        await refreshInventory()
      } catch (e) {
        // A 409 multiple-match carries a structured detail `{ message, candidates }`
        // (see client.ts `ApiError.detail`); show the message + candidate names so
        // the user can pick a more specific identifier, instead of a raw JSON blob.
        const detail =
          e instanceof ApiError
            ? (e.detail as { message?: string; candidates?: Array<{ name?: string; doc_name?: string }> } | undefined)
            : undefined
        const candidates = detail?.candidates
        if (candidates && candidates.length > 0) {
          const names = candidates.map((c) => c.name || c.doc_name || '?').join(', ')
          toast.error(t('kb:docs.delete.multiple', { message: detail?.message || errMsg(e), names }))
        } else {
          toast.error(t('kb:docs.delete.error', { error: errMsg(e) }))
        }
      }
    },
    [id, refreshInventory, t],
  )

  // Card selection handler: Index opens index.md, a type card auto-selects
  // its first page, Documents shows no reader.
  const selectSection = useCallback(
    (next: Section) => {
      setSection(next)
      if (next === 'index') {
        openPath('index.md')
      } else if (next !== 'documents') {
        const first = inv?.[next]?.[0]
        setSelectedPath(first ? `${next}/${first}` : null)
      }
    },
    [inv, openPath],
  )

  const docCount = inv?.document_count ?? 0
  const documents = inv?.documents ?? []
  const hasPages = inv ? pageTotal(inv) > 0 : false

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="shrink-0 px-6 pt-5 pb-3 glass-2 relative">
        {/* pr-28 reserves the global top-right chrome lane (theme pill + future
            i18n switcher, see App.tsx) so the ml-auto gear clears the pill with
            room to spare. The reserve lives on this control row (not the header
            div) so the overview cards below keep symmetric px-6 width. */}
        <div className="flex items-center gap-3 pr-28">
          <span className="w-3 h-3 rounded-full bg-accent-brand" />
          <h1 className="text-[19px] font-extrabold tracking-tight text-foreground">{id}</h1>
          <button
            onClick={() => setSettingsOpen(true)}
            title={t('kb:settingsButton')}
            aria-label={t('kb:settingsButton')}
            className="ml-auto grid h-8 w-8 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          >
            <Settings2 className="w-4 h-4" />
          </button>
        </div>
        {inv && (
          <KbOverviewCards inv={inv} docCount={docCount} active={section} onSelect={selectSection} />
        )}
      </div>

      <motion.section
        key={section}
        className="flex-1 min-h-0"
        initial={reduce || !animateSwitch ? false : { opacity: 0, y: 8, scale: 0.995 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={reduce ? { duration: 0.12 } : { type: 'spring', bounce: 0, duration: 0.3 }}
      >
        {section === 'index' ? (
          <IndexReader
            selected={selected}
            page={page}
            pageError={pageError}
            selectedPath={selectedPath}
            hasPages={hasPages}
            inv={inv}
            onWikiLink={onWikiLink}
          />
        ) : section === 'documents' ? (
          <DocumentsPane
            documents={documents}
            invError={invError}
            uploading={uploading}
            uploadFiles={uploadFiles}
            dragActive={dragActive}
            fileInputRef={fileInputRef}
            onDragActiveChange={setDragActive}
            onUpload={doUpload}
            onRefresh={refreshInventory}
            onDelete={onDeleteDocument}
          />
        ) : (
          <div className="h-full flex">
            <div className="w-[300px] shrink-0 border-r border-[hsl(var(--glass-border))] glass-2 flex flex-col min-h-0">
              {invError ? (
                <div className="m-2 rounded-lg bg-red-50 dark:bg-red-500/10 border border-red-200/70 dark:border-red-500/25 px-3 py-2 text-[12px] text-red-600 dark:text-red-400">
                  {t('kb:loadError', { error: invError })}
                </div>
              ) : !inv ? (
                <div className="px-4 py-3 text-[12px] text-muted-foreground">{t('common:loading')}</div>
              ) : (
                <div className="flex-1 min-h-0">
                  <PageList key={section} inv={inv} type={section} activePath={selected?.path ?? null} onOpen={openPath} />
                </div>
              )}
              <div className="shrink-0 m-2 mt-1 rounded-lg border border-dashed border-[hsl(var(--glass-border))] px-3 py-2 text-[11px] text-muted-foreground leading-relaxed">
                {t('kb:wikiNote')}
              </div>
            </div>
            <Reader selected={selected} page={page} pageError={pageError} selectedPath={selectedPath} hasPages={hasPages} inv={inv} onWikiLink={onWikiLink} />
          </div>
        )}
      </motion.section>

      <KbSettingsSheet
        kb={id}
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        docCount={docCount}
        onChanged={refreshInventory}
        onDeleted={() => {
          setSettingsOpen(false)
          navigate('/kb')
        }}
      />
    </div>
  )
}

/** Shared props for the page-content column, whether it renders full-width
 *  (Index) or beside the 300px `PageList` sidebar (a type card). */
interface ReaderProps {
  selected: SelectedPage | null
  page: { path: string; content: string } | null
  pageError: { path: string; message: string } | null
  selectedPath: string | null
  hasPages: boolean
  inv: KbInventory | null
  /** Navigate to a `[[wikilink]]` target clicked inside the rendered page. */
  onWikiLink: (target: string) => void
}

/** The actual page body: breadcrumb + Markdown, or an empty/loading state.
 *  Shared verbatim by `Reader` (Browse) and `IndexReader` (Index, full width) —
 *  they differ only in their outer scroll container. */
function ReaderBody({ selected, page, pageError, selectedPath, hasPages, inv, onWikiLink }: ReaderProps) {
  const { t } = useTranslation(['kb', 'common'])
  const pageReady = page && page.path === selectedPath
  const pageFailed = pageError && pageError.path === selectedPath

  if (!selected) {
    return (
      <div className="h-full grid place-items-center text-[13px] text-muted-foreground">
        {inv && !hasPages ? t('kb:reader.empty') : t('kb:reader.selectPage')}
      </div>
    )
  }

  return (
    <div className="w-full max-w-[1600px] mx-auto px-8 lg:px-12 py-7 anim-fade-up" key={selected.path}>
      <div className="flex items-center gap-2 text-[11.5px] text-muted-foreground mb-4">
        <span className="font-mono2 bg-muted rounded px-1.5 py-0.5">
          wiki/{selected.group}
          {selected.title}
        </span>
      </div>
      {pageFailed ? (
        <div className="rounded-lg bg-red-50 dark:bg-red-500/10 border border-red-200/70 dark:border-red-500/25 px-3 py-2 text-[13px] text-red-600 dark:text-red-400">
          {t('common:pageLoadError', { error: pageError.message })}
        </div>
      ) : pageReady ? (
        <MarkdownView source={page.content} onWikiLink={onWikiLink} />
      ) : (
        <div className="flex items-center gap-2 text-[13px] text-muted-foreground">
          <Loader2 className="w-3.5 h-3.5 animate-spin" />{t('common:loading')}
        </div>
      )}
    </div>
  )
}

/** Reader column next to the `PageList` sidebar (a type card section). */
function Reader(props: ReaderProps) {
  return (
    <div className="flex-1 min-w-0 overflow-y-auto scroll-edge-top">
      <ReaderBody {...props} />
    </div>
  )
}

/** Same reader, full width, with no `PageList` sidebar — Index section. */
function IndexReader(props: ReaderProps) {
  return (
    <div className="h-full overflow-y-auto scroll-edge-top">
      <ReaderBody {...props} />
    </div>
  )
}

/** Status glyph for one per-file upload row. */
function UploadStatusIcon({ status }: { status: UploadStatus }) {
  switch (status) {
    case 'processing':
      return <Loader2 className="w-3.5 h-3.5 animate-spin text-muted-foreground" />
    case 'added':
      return <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500 dark:text-emerald-400" />
    case 'skipped':
      return <CircleSlash2 className="w-3.5 h-3.5 text-muted-foreground" />
    case 'failed':
      return <XCircle className="w-3.5 h-3.5 text-red-500 dark:text-red-400" />
    default:
      return <Circle className="w-3.5 h-3.5 text-muted-foreground/50" />
  }
}

/** Documents section: upload dropzone + uploaded-document list + remote
 *  connector cards. Moved verbatim from the old Sources tab body. */
function DocumentsPane({
  documents,
  invError,
  uploading,
  uploadFiles,
  dragActive,
  fileInputRef,
  onDragActiveChange,
  onUpload,
  onRefresh,
  onDelete,
}: {
  documents: WikiDocument[]
  invError: string | null
  uploading: boolean
  uploadFiles: UploadFileState[]
  dragActive: boolean
  fileInputRef: RefObject<HTMLInputElement | null>
  onDragActiveChange: (active: boolean) => void
  onUpload: (files: File[]) => void
  onRefresh: () => void
  onDelete: (identifier: string) => Promise<void>
}) {
  const { t } = useTranslation(['kb', 'common'])
  // Inline delete confirm: `confirmName` is the row awaiting confirmation;
  // `deletingName` is the row whose remove request is in flight.
  const [confirmName, setConfirmName] = useState<string | null>(null)
  const [deletingName, setDeletingName] = useState<string | null>(null)
  const handleDelete = async (name: string) => {
    setDeletingName(name)
    try {
      await onDelete(name)
    } finally {
      setDeletingName(null)
      setConfirmName(null)
    }
  }
  return (
    <div className="h-full overflow-y-auto scroll-edge-top">
      <div className="max-w-[1280px] mx-auto px-8 lg:px-12 py-6">
        <p className="text-[13px] text-muted-foreground">
          {t('kb:upload.note')}
        </p>

        {/* Upload dropzone (real /api/v1/add) */}
        <div
          onDragOver={(e) => {
            e.preventDefault()
            onDragActiveChange(true)
          }}
          onDragLeave={() => onDragActiveChange(false)}
          onDrop={(e) => {
            e.preventDefault()
            onDragActiveChange(false)
            onUpload(Array.from(e.dataTransfer.files))
          }}
          onClick={() => !uploading && fileInputRef.current?.click()}
          className={cn(
            'mt-4 rounded-2xl border-2 border-dashed px-6 py-9 grid place-items-center text-center cursor-pointer transition-colors',
            dragActive
              ? 'border-accent-brand bg-accent-brand/5'
              : 'border-[hsl(var(--glass-border))] hover:border-foreground/20 glass-2',
            uploading && 'pointer-events-none opacity-70',
          )}
        >
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={(e) => {
              onUpload(Array.from(e.target.files ?? []))
              e.target.value = ''
            }}
          />
          {uploading ? (
            <div className="flex items-center gap-2 text-[13px] text-muted-foreground">
              <Loader2 className="w-4 h-4 animate-spin" />{t('kb:upload.inProgress')}
            </div>
          ) : (
            <>
              <Upload className="w-6 h-6 text-muted-foreground" />
              <div className="mt-2 text-[13.5px] font-medium text-foreground">{t('kb:upload.dropzone')}</div>
              <div className="mt-1 text-[12px] text-muted-foreground">{t('kb:upload.dropzoneHint')}</div>
            </>
          )}
        </div>

        {/* Per-file upload progress (streaming /api/v1/add?stream=true) */}
        {uploadFiles.length > 0 && (
          <div className="mt-4">
            <h2 className="text-[13.5px] font-semibold text-foreground">
              {t('kb:upload.progressHeading', { count: uploadFiles.length })}
            </h2>
            <div className="mt-2 space-y-1.5">
              {uploadFiles.map((f) => (
                <div
                  key={f.id}
                  className="rounded-xl border border-[hsl(var(--glass-border))] glass-2 px-3 py-2 flex items-center gap-2.5"
                >
                  <UploadStatusIcon status={f.status} />
                  <span className="text-[13px] text-foreground truncate">{f.name}</span>
                  <span className="ml-auto text-[11.5px] text-muted-foreground shrink-0">
                    {t(`kb:upload.fileStatus.${f.status}`)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Uploaded documents (real /api/v1/list) */}
        <div className="mt-6 flex items-center justify-between">
          <h2 className="text-[13.5px] font-semibold text-foreground">{t('kb:docs.heading', { count: documents.length })}</h2>
          <button
            onClick={() => onRefresh()}
            className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-lg border border-[hsl(var(--glass-border))] text-[12px] font-medium text-muted-foreground hover:bg-accent transition-colors"
          >
            <RefreshCw className="w-3 h-3" />{t('common:actions.refresh')}
          </button>
        </div>
        <div className="mt-3 space-y-2">
          {invError && (
            <div className="rounded-lg bg-red-50 dark:bg-red-500/10 border border-red-200/70 dark:border-red-500/25 px-3 py-2 text-[12px] text-red-600 dark:text-red-400">
              {t('kb:loadError', { error: invError })}
            </div>
          )}
          {!invError && documents.length === 0 && (
            <div className="rounded-2xl border border-dashed border-[hsl(var(--glass-border))] py-10 text-center text-[13px] text-muted-foreground">
              {t('kb:docs.empty')}
            </div>
          )}
          {documents.map((d, i) => (
            <div
              key={d.hash || d.name || i}
              className={cn(
                'anim-fade-up rounded-2xl border border-[hsl(var(--glass-border))] glass-2 px-4 py-3 flex items-center gap-3',
                `anim-d${Math.min(i + 1, 4)}`,
              )}
            >
              <span className="w-9 h-9 rounded-xl bg-muted border border-[hsl(var(--glass-border))] grid place-items-center shrink-0">
                <FileText className="w-4 h-4 text-muted-foreground" />
              </span>
              <div className="min-w-0">
                <div className="text-[13.5px] font-medium text-foreground truncate">{d.name}</div>
                <div className="text-[12px] text-muted-foreground mt-0.5">
                  {d.display_type}
                  {d.pages != null && <> · {t('kb:docs.pages', { count: d.pages })}</>}
                </div>
              </div>
              <div className="ml-auto flex items-center gap-2 shrink-0">
                {d.hash && (
                  <span className="font-mono2 text-[11px] text-muted-foreground bg-muted rounded px-1.5 py-0.5">
                    {d.hash.slice(0, 8)}
                  </span>
                )}
                {d.name &&
                  (confirmName === d.name ? (
                    <div className="flex items-center gap-1.5">
                      <span className="text-[11.5px] text-muted-foreground">{t('kb:docs.delete.prompt')}</span>
                      <button
                        onClick={() => handleDelete(d.name)}
                        disabled={deletingName === d.name}
                        className="inline-flex items-center gap-1 h-7 px-2 rounded-lg text-[12px] font-medium text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-500/10 transition-colors disabled:opacity-60"
                      >
                        {deletingName === d.name && <Loader2 className="w-3 h-3 animate-spin" />}
                        {t('kb:docs.delete.confirm')}
                      </button>
                      <button
                        onClick={() => setConfirmName(null)}
                        disabled={deletingName === d.name}
                        className="inline-flex items-center h-7 px-2 rounded-lg text-[12px] font-medium text-muted-foreground hover:bg-accent hover:text-foreground transition-colors disabled:opacity-60"
                      >
                        {t('kb:docs.delete.cancel')}
                      </button>
                    </div>
                  ) : (
                    <button
                      onClick={() => setConfirmName(d.name)}
                      title={t('kb:docs.delete.action')}
                      aria-label={t('kb:docs.delete.action')}
                      className="grid h-7 w-7 place-items-center rounded-lg text-muted-foreground hover:bg-red-50 dark:hover:bg-red-500/10 hover:text-red-600 dark:hover:text-red-400 transition-colors"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  ))}
              </div>
            </div>
          ))}
        </div>

        {/* Remote connectors: no backend. Reframed as GitHub feature-request voting; never fake a connected state. */}
        <h2 className="mt-8 text-[13.5px] font-semibold text-foreground">{t('kb:remote.heading')}</h2>
        <p className="mt-1 text-[12px] text-muted-foreground">
          {t('kb:remote.note')}
        </p>
        <div className="mt-3">
          <ConnectorCards />
        </div>
      </div>
    </div>
  )
}
