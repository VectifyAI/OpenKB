import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router'
import { FileText, ExternalLink, Loader2 } from 'lucide-react'
import { getKbInventory, getPage, type KbInventory } from '@/api/wiki'
import MarkdownView from '@/components/MarkdownView'
import { cn } from '@/lib/utils'

const tabs = [
  { id: 'browse', label: '浏览' },
  { id: 'sources', label: '来源' },
  { id: 'jobs', label: '任务' },
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

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e))

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

  const docCount = inv?.document_count ?? 0
  const pageReady = page && page.id === selectedId
  const pageFailed = pageError && pageError.id === selectedId

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

      {/* 来源 / 任务：Task 9 接入 */}
      {tab !== 'browse' && (
        <div className="flex-1 grid place-items-center text-[13px] text-neutral-400">
          此标签将在后续任务接入
        </div>
      )}
    </div>
  )
}
