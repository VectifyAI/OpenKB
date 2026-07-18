import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useParams } from 'react-router'
import {
  FileText, ExternalLink, Loader2, Upload, Cloud, HardDrive,
  RefreshCw, Play, CheckCircle2, AlertCircle, ShieldCheck, Clock, Radio,
} from 'lucide-react'
import { toast } from 'sonner'
import { getKbInventory, getPage, type KbInventory } from '@/api/wiki'
import {
  uploadDocuments, watchStart, watchStop, watchStatus, runRecompile, runLint,
  type WatchStatus,
} from '@/api/maintenance'
import type { SseEvent } from '@/api/client'
import MarkdownView from '@/components/MarkdownView'
import { cn } from '@/lib/utils'

const tabs = [
  { id: 'browse', label: '浏览' },
  { id: 'sources', label: '来源' },
  { id: 'jobs', label: '任务' },
] as const

/**
 * Remote connectors are NOT implemented — there is no backend for Drive/S3/
 * OneDrive sync. These render as disabled "coming soon" cards so the UI never
 * fakes a connected/synced state. The only real sources are uploaded docs.
 */
const connectors = [
  { id: 'gdrive', label: 'Google Drive', icon: Cloud },
  { id: 's3', label: 'Amazon S3', icon: HardDrive },
  { id: 'onedrive', label: 'OneDrive', icon: Cloud },
] as const

/** One selectable wiki page in the browse tree. */
interface TreeItem {
  /** Stable id, also the group-prefixed path shown in the chip. */
  id: string
  /** Folder label, e.g. `summaries/` (trailing slash matches the reference). */
  group: string
  /** Display filename, always with `.md`. */
  title: string
  /** Path passed to `/api/v1/page` (relative to `wiki/`). */
  path: string
}

/** Per-doc row accumulated from a recompile SSE stream (from the `doc` event). */
interface RecompileDoc {
  name: string
  type: string
  status: string
  elapsed: number | null
  message: string | null
}

/** Live recompile progress, folded from `runRecompile`'s SSE events. */
interface RecompileState {
  status: 'idle' | 'running' | 'done' | 'error'
  docs: RecompileDoc[]
  summary: { total: number; recompiled: number; skipped: number } | null
  error: string | null
}

const initialRecompile: RecompileState = { status: 'idle', docs: [], summary: null, error: null }

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e))

/**
 * Fold one recompile SSE event into the running job state. Mirrors the
 * per-event accumulation used by the chat/deck streams: `start` resets, each
 * `doc` appends a row, `final` records the summary, `error` is terminal.
 */
function foldRecompile(s: RecompileState, ev: SseEvent): RecompileState {
  switch (ev.event) {
    case 'start':
      return { status: 'running', docs: [], summary: null, error: null }
    case 'doc':
      return {
        ...s,
        docs: [
          ...s.docs,
          {
            name: ev.data?.doc_name ?? ev.data?.name ?? '(未命名)',
            type: ev.data?.type ?? '',
            status: ev.data?.status ?? '',
            elapsed: ev.data?.elapsed ?? null,
            message: ev.data?.message ?? null,
          },
        ],
      }
    case 'final':
      return {
        ...s,
        status: s.status === 'error' ? 'error' : 'done',
        summary: {
          total: ev.data?.total ?? 0,
          recompiled: ev.data?.recompiled ?? 0,
          skipped: ev.data?.skipped ?? 0,
        },
      }
    case 'error':
      return { ...s, status: 'error', error: ev.data?.message ?? '重新编译失败' }
    default:
      return s
  }
}

/**
 * Flatten the `/api/v1/list` inventory into a folder-grouped page list.
 * `summaries`/`concepts` are stems (no extension); `reports` are full names.
 * `entities/` is intentionally absent — the list endpoint does not surface it.
 */
function buildItems(inv: KbInventory | null): TreeItem[] {
  if (!inv) return []
  return [
    ...inv.summaries.map((s) => ({
      id: `summaries/${s}`,
      group: 'summaries/',
      title: `${s}.md`,
      path: `summaries/${s}`,
    })),
    ...inv.concepts.map((c) => ({
      id: `concepts/${c}`,
      group: 'concepts/',
      title: `${c}.md`,
      path: `concepts/${c}`,
    })),
    ...inv.reports.map((r) => ({
      id: `reports/${r}`,
      group: 'reports/',
      title: r,
      path: `reports/${r}`,
    })),
  ]
}

export default function KbDetail() {
  const { id = '' } = useParams()
  const [tab, setTab] = useState<string>('browse')

  const [inv, setInv] = useState<KbInventory | null>(null)
  const [invError, setInvError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)

  // Page content and error are tagged with the id they belong to so a stale
  // response never renders under a newly selected page.
  const [page, setPage] = useState<{ id: string; content: string } | null>(null)
  const [pageError, setPageError] = useState<{ id: string; message: string } | null>(null)

  // 来源 tab: upload state.
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const [dragActive, setDragActive] = useState(false)

  // 任务 tab: watcher status + live recompile + lint.
  const [watch, setWatch] = useState<WatchStatus | null>(null)
  const [watchError, setWatchError] = useState<string | null>(null)
  const [watchBusy, setWatchBusy] = useState(false)
  const [recompile, setRecompile] = useState<RecompileState>(initialRecompile)
  const [recompileRunning, setRecompileRunning] = useState(false)
  const [lintBusy, setLintBusy] = useState(false)
  const [lintResult, setLintResult] = useState<string | null>(null)

  const items = useMemo(() => buildItems(inv), [inv])
  const groups = useMemo(() => [...new Set(items.map((i) => i.group))], [items])
  const selected = useMemo(
    () => items.find((i) => i.id === selectedId) ?? null,
    [items, selectedId],
  )

  // Load the folder tree, then auto-select the first page. State is only ever
  // set inside the async callbacks (never synchronously in the effect body).
  // The component is remounted per KB via `key` in App, so no reset is needed.
  useEffect(() => {
    let cancelled = false
    getKbInventory(id)
      .then((r) => {
        if (cancelled) return
        setInv(r)
        const first = buildItems(r)[0]
        if (first) setSelectedId(first.id)
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
    const item = items.find((i) => i.id === selectedId)
    if (!item) return
    let cancelled = false
    getPage(id, item.path)
      .then((r) => {
        if (cancelled) return
        setPage({ id: item.id, content: r.content })
        setPageError(null)
      })
      .catch((e) => {
        if (!cancelled) setPageError({ id: item.id, message: errMsg(e) })
      })
    return () => {
      cancelled = true
    }
  }, [id, selectedId, items])

  // Poll watcher status while the 任务 tab is open so counters reflect reality
  // (added/skipped/failed tick up as the watcher ingests files).
  useEffect(() => {
    if (tab !== 'jobs') return
    let cancelled = false
    const tick = () => {
      watchStatus(id)
        .then((s) => {
          if (cancelled) return
          setWatch(s)
          setWatchError(null)
        })
        .catch((e) => {
          if (!cancelled) setWatchError(errMsg(e))
        })
    }
    tick()
    const iv = setInterval(tick, 5000)
    return () => {
      cancelled = true
      clearInterval(iv)
    }
  }, [tab, id])

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
      try {
        // `/api/v1/add` returns HTTP 200 even when per-file compile fails, so
        // a non-throwing result does NOT mean success — branch on the real
        // added/failed/skipped counts instead of blindly reporting success.
        const res = await uploadDocuments(id, files)
        const parts = [`新增 ${res.added_count}`]
        if (res.skipped_count) parts.push(`跳过 ${res.skipped_count}`)
        if (res.failed_count) parts.push(`失败 ${res.failed_count}`)
        const summary = parts.join(' · ')
        if (res.added_count === 0 && res.failed_count > 0) {
          // Every file failed — surface the first failure's message so the
          // user learns WHY (e.g. compile error / missing LLM API key).
          const reason = res.files.find((f) => f.status === 'failed')?.message
          toast.error(`上传失败：${summary}${reason ? `（${reason}）` : ''}`)
        } else if (res.failed_count > 0) {
          // Some added, some failed — not a clean success.
          toast.warning(`部分成功：${summary}`)
        } else if (res.added_count > 0) {
          toast.success(`上传完成：${summary}`)
        } else {
          // Nothing added or failed (all skipped duplicates) — neutral, not
          // an error and not a "新增" success.
          toast.info(`文档已存在，未新增：${summary}`)
        }
        // Refresh regardless of outcome: a failed compile may still have
        // written a raw file, and the user needs to see current state.
        await refreshInventory()
      } catch (e) {
        toast.error(errMsg(e))
      } finally {
        setUploading(false)
      }
    },
    [id, uploading, refreshInventory],
  )

  const toggleWatch = useCallback(async () => {
    if (watchBusy) return
    setWatchBusy(true)
    const wasActive = watch?.active === true
    try {
      const s = wasActive ? await watchStop(id) : await watchStart(id)
      setWatch(s)
      setWatchError(null)
      toast.success(wasActive ? '已停止文件监听' : '已启动文件监听')
    } catch (e) {
      toast.error(errMsg(e))
    } finally {
      setWatchBusy(false)
    }
  }, [id, watch?.active, watchBusy])

  const startRecompile = useCallback(async () => {
    if (recompileRunning) return
    setRecompileRunning(true)
    setRecompile({ status: 'running', docs: [], summary: null, error: null })
    try {
      for await (const ev of runRecompile(id)) {
        setRecompile((s) => foldRecompile(s, ev))
      }
    } catch (e) {
      const message = errMsg(e)
      setRecompile((s) => ({ ...s, status: 'error', error: s.error ?? message }))
      toast.error(`重新编译失败：${message}`)
    } finally {
      // A stream that ended without a terminal `final`/`error` still settles.
      setRecompile((s) => (s.status === 'running' ? { ...s, status: s.error ? 'error' : 'done' } : s))
      setRecompileRunning(false)
      // Recompiled pages change the wiki tree; keep the browse/来源 lists fresh.
      refreshInventory()
    }
  }, [id, recompileRunning, refreshInventory])

  const runStructuralLint = useCallback(async () => {
    if (lintBusy) return
    setLintBusy(true)
    try {
      const res = (await runLint(id, false)) as { message?: string; skipped?: boolean }
      setLintResult(res.message ?? (res.skipped ? '检查已跳过' : '检查完成'))
      toast.success('结构检查完成')
    } catch (e) {
      toast.error(`检查失败：${errMsg(e)}`)
    } finally {
      setLintBusy(false)
    }
  }, [id, lintBusy])

  const docCount = inv?.document_count ?? 0
  const documents = inv?.documents ?? []
  const pageReady = page && page.id === selectedId
  const pageFailed = pageError && pageError.id === selectedId
  const watchActive = watch?.active === true

  return (
    <div className="h-full flex flex-col">
      {/* 头部 */}
      <div className="shrink-0 px-6 pt-5 pb-0 border-b border-black/6 bg-white/60">
        <div className="flex items-center gap-3">
          <span className="w-3 h-3 rounded-full bg-blue-500" />
          <h1 className="text-[19px] font-extrabold tracking-tight text-neutral-900">{id}</h1>
        </div>
        <p className="mt-1 text-[13px] text-neutral-400">
          {docCount} 篇文档 · {inv?.concepts.length ?? 0} 概念 · {inv?.summaries.length ?? 0} 摘要
          {inv && inv.reports.length > 0 && <> · {inv.reports.length} 报告</>}
        </p>
        <div className="mt-3 flex gap-1">
          {tabs.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={cn(
                'px-3.5 h-9 rounded-t-lg text-[13px] font-medium transition-colors relative',
                tab === t.id ? 'text-blue-600 bg-[#f7f7f4]' : 'text-neutral-500 hover:text-neutral-800',
              )}
            >
              {t.label}
              {tab === t.id && (
                <span className="absolute bottom-0 left-3 right-3 h-0.5 rounded-full bg-blue-600" />
              )}
            </button>
          ))}
        </div>
      </div>

      {/* 浏览 */}
      {tab === 'browse' && (
        <div className="flex-1 min-h-0 flex">
          <div className="w-[240px] shrink-0 border-r border-black/6 overflow-y-auto p-3 bg-white/40">
            {invError && (
              <div className="mx-2 rounded-lg bg-red-50 border border-red-200/70 px-3 py-2 text-[12px] text-red-600">
                加载失败：{invError}
              </div>
            )}
            {!invError && !inv && (
              <div className="px-2 py-3 text-[12px] text-neutral-400">加载中…</div>
            )}
            {inv && items.length === 0 && (
              <div className="px-2 py-3 text-[12px] text-neutral-400">此知识库暂无已编译页面</div>
            )}
            {groups.map((g) => (
              <div key={g || 'root'} className="mb-3">
                {g && (
                  <div className="px-2 mb-1 text-[11px] font-mono2 font-semibold text-neutral-400">{g}</div>
                )}
                {items
                  .filter((p) => p.group === g)
                  .map((p) => (
                    <button
                      key={p.id}
                      onClick={() => setSelectedId(p.id)}
                      className={cn(
                        'w-full flex items-center gap-2 px-2.5 h-8 rounded-lg text-left text-[12.5px] transition-colors',
                        p.id === selectedId
                          ? 'bg-blue-50 text-blue-700 font-medium'
                          : 'text-neutral-600 hover:bg-white',
                      )}
                    >
                      <FileText className="w-3.5 h-3.5 shrink-0 opacity-50" />
                      <span className="truncate font-mono2 text-[12px]">{p.title}</span>
                    </button>
                  ))}
              </div>
            ))}
            <div className="mt-4 mx-2 rounded-lg bg-neutral-50 border border-dashed border-neutral-200 px-3 py-2 text-[11px] text-neutral-400 leading-relaxed">
              wiki/ 是纯 Markdown 目录，可直接用 Obsidian 打开
            </div>
          </div>
          <div className="flex-1 min-w-0 overflow-y-auto">
            {selected ? (
              <div className="max-w-[640px] mx-auto px-8 py-7 anim-fade-up" key={selected.id}>
                <div className="flex items-center gap-2 text-[11.5px] text-neutral-400 mb-4">
                  <span className="font-mono2 bg-neutral-100 rounded px-1.5 py-0.5">
                    wiki/{selected.group}
                    {selected.title}
                  </span>
                  <button
                    type="button"
                    disabled
                    title="wiki/ 是本机的纯 Markdown 目录，请在本地用 Obsidian 打开此文件；浏览器标签页无法直接跳转到本地文件。"
                    className="ml-auto inline-flex items-center gap-1 text-neutral-400 cursor-not-allowed"
                  >
                    <ExternalLink className="w-3 h-3" />在 Obsidian 中打开
                  </button>
                </div>
                {pageFailed ? (
                  <div className="rounded-lg bg-red-50 border border-red-200/70 px-3 py-2 text-[13px] text-red-600">
                    页面加载失败：{pageError.message}
                  </div>
                ) : pageReady ? (
                  <MarkdownView source={page.content} />
                ) : (
                  <div className="flex items-center gap-2 text-[13px] text-neutral-400">
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />加载中…
                  </div>
                )}
              </div>
            ) : (
              <div className="h-full grid place-items-center text-[13px] text-neutral-400">
                {inv && items.length === 0 ? '此知识库暂无已编译页面' : '选择左侧页面以查看内容'}
              </div>
            )}
          </div>
        </div>
      )}

      {/* 来源 */}
      {tab === 'sources' && (
        <div className="flex-1 overflow-y-auto">
          <div className="max-w-[760px] mx-auto px-6 py-6">
            <p className="text-[13px] text-neutral-400">
              上传的文档进入本地 raw/ 并自动编译进知识库；文件仅保存在本机
            </p>

            {/* 上传拖放区（真实 /api/v1/add） */}
            <div
              onDragOver={(e) => {
                e.preventDefault()
                setDragActive(true)
              }}
              onDragLeave={() => setDragActive(false)}
              onDrop={(e) => {
                e.preventDefault()
                setDragActive(false)
                doUpload(Array.from(e.dataTransfer.files))
              }}
              onClick={() => !uploading && fileInputRef.current?.click()}
              className={cn(
                'mt-4 rounded-2xl border-2 border-dashed px-6 py-9 grid place-items-center text-center cursor-pointer transition-colors',
                dragActive ? 'border-blue-400 bg-blue-50/50' : 'border-neutral-200 hover:border-neutral-300 bg-white/50',
                uploading && 'pointer-events-none opacity-70',
              )}
            >
              <input
                ref={fileInputRef}
                type="file"
                multiple
                className="hidden"
                onChange={(e) => {
                  doUpload(Array.from(e.target.files ?? []))
                  e.target.value = ''
                }}
              />
              {uploading ? (
                <div className="flex items-center gap-2 text-[13px] text-neutral-500">
                  <Loader2 className="w-4 h-4 animate-spin" />正在上传并编译…
                </div>
              ) : (
                <>
                  <Upload className="w-6 h-6 text-neutral-400" />
                  <div className="mt-2 text-[13.5px] font-medium text-neutral-700">拖放文件到此处，或点击选择</div>
                  <div className="mt-1 text-[12px] text-neutral-400">支持 PDF / Word / Markdown / 文本等，可多选</div>
                </>
              )}
            </div>

            {/* 已上传文档（真实 /api/v1/list） */}
            <div className="mt-6 flex items-center justify-between">
              <h2 className="text-[13.5px] font-semibold text-neutral-700">已上传文档 · {documents.length}</h2>
              <button
                onClick={() => refreshInventory()}
                className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-lg border border-black/10 text-[12px] font-medium text-neutral-600 hover:bg-white transition-colors"
              >
                <RefreshCw className="w-3 h-3" />刷新
              </button>
            </div>
            <div className="mt-3 space-y-2">
              {invError && (
                <div className="rounded-lg bg-red-50 border border-red-200/70 px-3 py-2 text-[12px] text-red-600">
                  加载失败：{invError}
                </div>
              )}
              {!invError && documents.length === 0 && (
                <div className="rounded-2xl border border-dashed border-neutral-200 py-10 text-center text-[13px] text-neutral-400">
                  暂无文档 · 上传后会自动编译进知识库
                </div>
              )}
              {documents.map((d, i) => (
                <div
                  key={d.hash || d.name || i}
                  className={cn(
                    'anim-fade-up rounded-2xl border border-black/8 bg-white px-4 py-3 flex items-center gap-3',
                    `anim-d${Math.min(i + 1, 4)}`,
                  )}
                >
                  <span className="w-9 h-9 rounded-xl bg-neutral-50 border border-black/5 grid place-items-center shrink-0">
                    <FileText className="w-4 h-4 text-neutral-500" />
                  </span>
                  <div className="min-w-0">
                    <div className="text-[13.5px] font-medium text-neutral-800 truncate">{d.name}</div>
                    <div className="text-[12px] text-neutral-400 mt-0.5">
                      {d.display_type}
                      {d.pages != null && <> · {d.pages} 页</>}
                    </div>
                  </div>
                  {d.hash && (
                    <span className="ml-auto font-mono2 text-[11px] text-neutral-400 bg-neutral-100 rounded px-1.5 py-0.5 shrink-0">
                      {d.hash.slice(0, 8)}
                    </span>
                  )}
                </div>
              ))}
            </div>

            {/* 远程连接器：无后端，明确标注即将推出，绝不伪造已连接状态 */}
            <h2 className="mt-8 text-[13.5px] font-semibold text-neutral-700">远程数据源</h2>
            <p className="mt-1 text-[12px] text-neutral-400">云端连接器尚未实现，敬请期待</p>
            <div className="mt-3 grid grid-cols-1 sm:grid-cols-3 gap-2.5">
              {connectors.map((c) => (
                <div
                  key={c.id}
                  aria-disabled="true"
                  title="即将推出"
                  className="rounded-2xl border border-dashed border-neutral-200 bg-neutral-50/60 px-4 py-3.5 flex items-center gap-3 opacity-70 cursor-not-allowed select-none"
                >
                  <span className="w-9 h-9 rounded-xl bg-white border border-black/5 grid place-items-center shrink-0">
                    <c.icon className="w-4 h-4 text-neutral-400" />
                  </span>
                  <div className="min-w-0">
                    <div className="text-[13px] font-medium text-neutral-500 truncate">{c.label}</div>
                    <div className="text-[11.5px] text-neutral-400 mt-0.5">即将推出</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* 任务 */}
      {tab === 'jobs' && (
        <div className="flex-1 overflow-y-auto">
          <div className="max-w-[760px] mx-auto px-6 py-6 space-y-5">
            {/* 文件监听（真实 /api/v1/watch/status） */}
            <div className="rounded-2xl border border-black/8 bg-white px-4 py-3.5">
              <div className="flex items-center gap-3">
                <span
                  className={cn(
                    'w-8 h-8 rounded-lg grid place-items-center shrink-0',
                    watchActive ? 'bg-emerald-50 text-emerald-500' : 'bg-neutral-100 text-neutral-400',
                  )}
                >
                  <Radio className={cn('w-4 h-4', watchActive && 'animate-pulse')} />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="text-[13.5px] font-medium text-neutral-800">文件监听</div>
                  <div className="text-[12px] text-neutral-400 mt-0.5 truncate">
                    {watchActive
                      ? `监听中${watch?.raw_dir ? ` · ${watch.raw_dir}` : ''}`
                      : '未运行 · 启动后新文件会自动编译'}
                  </div>
                </div>
                <button
                  onClick={toggleWatch}
                  disabled={watchBusy}
                  className={cn(
                    'inline-flex items-center gap-1.5 h-8 px-3 rounded-lg text-[12.5px] font-medium transition-colors shrink-0 disabled:opacity-60',
                    watchActive
                      ? 'border border-black/10 text-neutral-600 hover:bg-neutral-50'
                      : 'bg-blue-600 text-white hover:bg-blue-700',
                  )}
                >
                  {watchBusy ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  ) : watchActive ? (
                    <>停止</>
                  ) : (
                    <>
                      <Play className="w-3.5 h-3.5" />启动监听
                    </>
                  )}
                </button>
              </div>
              {watchError && (
                <div className="mt-2.5 rounded-lg bg-red-50 border border-red-200/70 px-3 py-2 text-[12px] text-red-600">
                  {watchError}
                </div>
              )}
              {watch?.counters && Object.keys(watch.counters).length > 0 && (
                <div className="mt-3 flex gap-2 flex-wrap">
                  {Object.entries(watch.counters).map(([k, v]) => (
                    <span
                      key={k}
                      className="inline-flex items-center gap-1.5 text-[11.5px] text-neutral-600 bg-neutral-50 border border-black/5 rounded-full px-2.5 py-1"
                    >
                      <span className="text-neutral-400">{k}</span>
                      <span className="font-semibold text-neutral-800">{v}</span>
                    </span>
                  ))}
                </div>
              )}
              {watch?.recent_events && watch.recent_events.length > 0 && (
                <div className="mt-3 border-t border-black/5 pt-2.5 space-y-1">
                  {watch.recent_events.slice(-5).reverse().map((e, i) => (
                    <div key={i} className="flex items-center gap-2 text-[11.5px] text-neutral-400">
                      <Clock className="w-3 h-3 shrink-0" />
                      <span className="font-mono2 text-neutral-500">{e.event}</span>
                      <span className="truncate">
                        {typeof e.data?.original_name === 'string' ? e.data.original_name : ''}
                        {typeof e.data?.status === 'string' ? ` · ${e.data.status}` : ''}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* 维护动作 */}
            <div className="flex items-center gap-2">
              <button
                onClick={startRecompile}
                disabled={recompileRunning || docCount === 0}
                title={docCount === 0 ? '暂无已编译文档' : undefined}
                className="inline-flex items-center gap-1.5 h-8 px-3 rounded-lg bg-neutral-900 text-white text-[12.5px] font-medium hover:bg-neutral-700 transition-colors disabled:opacity-50"
              >
                {recompileRunning ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
                重新编译全部
              </button>
              <button
                onClick={runStructuralLint}
                disabled={lintBusy}
                className="inline-flex items-center gap-1.5 h-8 px-3 rounded-lg border border-black/10 text-[12.5px] font-medium text-neutral-600 hover:bg-white transition-colors disabled:opacity-60"
              >
                {lintBusy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ShieldCheck className="w-3.5 h-3.5" />}
                结构检查
              </button>
            </div>

            {lintResult && (
              <div className="rounded-2xl border border-black/8 bg-white px-4 py-3 text-[12.5px] text-neutral-600 whitespace-pre-wrap">
                {lintResult}
              </div>
            )}

            {/* 重新编译进度（真实 SSE） */}
            {recompile.status !== 'idle' && (
              <div className="space-y-2.5">
                <div className="flex items-center gap-2 text-[13px] font-medium text-neutral-700">
                  {recompile.status === 'running' && <Loader2 className="w-4 h-4 animate-spin text-blue-500" />}
                  {recompile.status === 'done' && <CheckCircle2 className="w-4 h-4 text-emerald-500" />}
                  {recompile.status === 'error' && <AlertCircle className="w-4 h-4 text-red-500" />}
                  重新编译
                  {recompile.summary && (
                    <span className="text-[12px] font-normal text-neutral-400">
                      · 共 {recompile.summary.total} · 编译 {recompile.summary.recompiled} · 跳过 {recompile.summary.skipped}
                    </span>
                  )}
                </div>
                {recompile.error && (
                  <div className="rounded-lg bg-red-50 border border-red-200/70 px-3 py-2 text-[12px] text-red-600">
                    {recompile.error}
                  </div>
                )}
                {recompile.docs.map((d, i) => (
                  <div key={`${d.name}-${i}`} className="rounded-2xl border border-black/8 bg-white px-4 py-3 flex items-center gap-3">
                    <span
                      className={cn(
                        'w-8 h-8 rounded-lg grid place-items-center shrink-0',
                        d.status === 'ok'
                          ? 'bg-emerald-50 text-emerald-500'
                          : d.status === 'error'
                            ? 'bg-red-50 text-red-500'
                            : 'bg-neutral-100 text-neutral-400',
                      )}
                    >
                      {d.status === 'ok' ? (
                        <CheckCircle2 className="w-4 h-4" />
                      ) : d.status === 'error' ? (
                        <AlertCircle className="w-4 h-4" />
                      ) : (
                        <Clock className="w-4 h-4" />
                      )}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="text-[13.5px] font-medium text-neutral-800 truncate">{d.name}</div>
                      <div className="text-[12px] text-neutral-400 mt-0.5 truncate">
                        {d.type}
                        {d.message ? ` · ${d.message}` : ''}
                      </div>
                    </div>
                    <span className="text-[11.5px] text-neutral-400 shrink-0">
                      {d.status}
                      {d.elapsed != null ? ` · ${d.elapsed}s` : ''}
                    </span>
                  </div>
                ))}
                {recompile.status === 'running' && recompile.docs.length === 0 && (
                  <div className="text-[12.5px] text-neutral-400">正在准备…</div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
