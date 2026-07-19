import { useEffect, useRef, useState } from "react"
import { motion, AnimatePresence, useReducedMotion } from "motion/react"
import {
  Presentation, Sparkles, Waypoints, ExternalLink, Download, Check,
  Loader2, FileText, FileArchive,
} from "lucide-react"
import { toast } from "sonner"
import { getDeckBlobUrl, getSkillArchiveBlobUrl } from "@/api/artifacts"
import type { GraphData } from "@/api/wiki"

/**
 * A generated artifact produced by a `/deck`, `/skill`, or `/visualize` command.
 *
 * These carry only REAL identifiers from the backend's `final` event
 * (`{ name, status, path }`) plus, for graphs, the real `getGraph` payload — no
 * fabricated slide/frontmatter/eval data (the reference's mock content is
 * intentionally dropped). Bytes for previews/downloads are fetched lazily and
 * client-side with the bearer token via `getDeckBlobUrl`/`getSkillArchiveBlobUrl`.
 */
export type Artifact =
  | { type: "deck"; kb: string; name: string; status: string; path: string }
  | { type: "skill"; kb: string; name: string; status: string; path: string }
  | { type: "graph"; kb: string; graph: GraphData }

/** Server output_dir is an absolute path; show only its final segment. */
function baseName(path: string): string {
  const trimmed = path.replace(/[\\/]+$/, "")
  const idx = Math.max(trimmed.lastIndexOf("/"), trimmed.lastIndexOf("\\"))
  return idx >= 0 ? trimmed.slice(idx + 1) : trimmed
}

/**
 * Trigger a browser download from a `blob:` URL via a transient `<a download>`.
 * The blob URL is client-fetched (bearer attached) so this never points `<a>`
 * at a raw `/api/...` path. The URL is revoked after the click has dispatched.
 */
function triggerBlobDownload(blobUrl: string, filename: string): void {
  const a = document.createElement("a")
  a.href = blobUrl
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  // Revoke after the download has been handed to the browser; the click is
  // dispatched synchronously above, so a short delay is safe and avoids a leak.
  window.setTimeout(() => URL.revokeObjectURL(blobUrl), 10_000)
}

function StatusBadge({ status }: { status: string }) {
  const ok = status === "done"
  return (
    <span
      className={
        ok
          ? "inline-flex items-center gap-1 text-[11px] text-emerald-700 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-500/10 border border-emerald-200 dark:border-emerald-500/25 rounded-full px-2 py-0.5"
          : "inline-flex items-center gap-1 text-[11px] text-muted-foreground bg-muted border border-[hsl(var(--glass-border))] rounded-full px-2 py-0.5"
      }
    >
      {ok && <Check className="w-3 h-3" />}
      {ok ? "生成完成" : status}
    </span>
  )
}

function DeckCard({ a }: { a: Extract<Artifact, { type: "deck" }> }) {
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  // Spring the preview open; degrade to a plain cross-fade under reduced motion.
  const reduce = useReducedMotion()
  // Mirror the live preview URL so unmount cleanup sees the latest value
  // without re-subscribing the effect on every change.
  const previewRef = useRef<string | null>(null)

  // Revoke the (long-lived) preview blob URL when this card unmounts.
  useEffect(
    () => () => {
      if (previewRef.current) URL.revokeObjectURL(previewRef.current)
    },
    [],
  )

  /** Set/clear the preview URL, revoking any URL it replaces. */
  const setPreview = (url: string | null) => {
    if (previewRef.current) URL.revokeObjectURL(previewRef.current)
    previewRef.current = url
    setPreviewUrl(url)
  }

  const togglePreview = async () => {
    if (previewUrl) {
      setPreview(null)
      return
    }
    setLoading(true)
    try {
      setPreview(await getDeckBlobUrl(a.kb, a.name))
    } catch (e) {
      toast.error(`无法加载幻灯片预览：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setLoading(false)
    }
  }

  const download = async () => {
    try {
      triggerBlobDownload(await getDeckBlobUrl(a.kb, a.name), `${a.name}.html`)
    } catch (e) {
      toast.error(`导出失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }

  return (
    <div className="rounded-apple-lg border border-[hsl(var(--glass-border))] overflow-hidden glass-2">
      <div className="bg-gradient-to-br from-blue-600 via-indigo-600 to-violet-600 px-5 py-4 text-white">
        <div className="flex items-center gap-2 text-[11px] font-medium opacity-80">
          <Presentation className="w-3.5 h-3.5" />HTML DECK
        </div>
        <div className="mt-1 text-[16px] font-bold leading-snug font-mono2">{a.name}</div>
        <div className="mt-1 text-[12px] opacity-75">已生成 · {baseName(a.path)}</div>
      </div>

      {/* 预览：真实生成的 HTML，渲染在受限沙箱 iframe 内（allow-scripts）。
          motion 弹簧只包裹面板做进出场动画，iframe 的 sandbox / blob src /
          撤销逻辑保持原样不变。 */}
      <AnimatePresence>
        {previewUrl && (
          <motion.div
            key="deck-preview"
            className="border-b border-[hsl(var(--glass-border))] overflow-hidden"
            initial={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.97, y: 6 }}
            animate={reduce ? { opacity: 1 } : { opacity: 1, scale: 1, y: 0 }}
            exit={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.97, y: 6 }}
            transition={reduce ? { duration: 0.12 } : { type: "spring", bounce: 0, duration: 0.24 }}
          >
            <iframe
              title={`deck-${a.name}`}
              src={previewUrl}
              sandbox="allow-scripts"
              className="w-full h-[420px] bg-white"
            />
          </motion.div>
        )}
      </AnimatePresence>

      <div className="flex items-center gap-2 px-4 py-3 border-t border-[hsl(var(--glass-border))] bg-muted/40">
        <button
          onClick={togglePreview}
          disabled={loading}
          className="inline-flex items-center gap-1.5 h-8 px-3.5 rounded-lg bg-accent-brand text-white text-[12px] font-medium hover:opacity-90 transition-colors disabled:opacity-60"
        >
          {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ExternalLink className="w-3.5 h-3.5" />}
          {previewUrl ? "收起预览" : "预览"}
        </button>
        <button
          onClick={download}
          className="inline-flex items-center gap-1.5 h-8 px-3.5 rounded-lg border border-[hsl(var(--glass-border))] text-[12px] font-medium text-foreground hover:bg-accent transition-colors"
        >
          <Download className="w-3.5 h-3.5" />导出 HTML
        </button>
      </div>
    </div>
  )
}

function SkillCard({ a }: { a: Extract<Artifact, { type: "skill" }> }) {
  const [downloading, setDownloading] = useState(false)

  const download = async () => {
    setDownloading(true)
    try {
      triggerBlobDownload(await getSkillArchiveBlobUrl(a.kb, a.name), `${a.name}.zip`)
    } catch (e) {
      toast.error(`下载技能包失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setDownloading(false)
    }
  }

  return (
    <div className="rounded-apple-lg border border-[hsl(var(--glass-border))] overflow-hidden glass-2">
      <div className="px-5 py-4 border-b border-[hsl(var(--glass-border))] flex items-start gap-3">
        <span className="w-9 h-9 rounded-xl bg-violet-100 text-violet-600 grid place-items-center shrink-0">
          <Sparkles className="w-[18px] h-[18px]" />
        </span>
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-mono2 text-[14px] font-semibold text-foreground">{a.name}</span>
            <StatusBadge status={a.status} />
          </div>
          <div className="text-[12px] text-muted-foreground mt-0.5 truncate">Agent Skill · {baseName(a.path)}</div>
        </div>
      </div>
      <div className="px-5 py-4">
        <p className="text-[12.5px] text-muted-foreground leading-relaxed mb-3">
          技能已生成为可安装的目录，下载 <span className="font-mono2 text-foreground">.zip</span> 后解压到你的
          Agent（Claude Code / Codex / Gemini CLI）技能目录即可使用。
        </p>
        <button
          onClick={download}
          disabled={downloading}
          className="inline-flex items-center gap-1.5 h-8 px-3.5 rounded-lg bg-accent-brand text-white text-[12px] font-medium hover:opacity-90 transition-colors disabled:opacity-60"
        >
          {downloading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <FileArchive className="w-3.5 h-3.5" />}
          下载技能包（.zip）
        </button>
      </div>
    </div>
  )
}

/** Deterministic circle layout over the highest-degree nodes for a preview. */
function graphLayout(graph: GraphData, max = 20) {
  const W = 300
  const H = 200
  const cx = W / 2
  const cy = H / 2
  const R = 76
  const ranked = [...graph.nodes]
    .sort((x, y) => y.in + y.out - (x.in + x.out))
    .slice(0, max)
  const maxDeg = ranked.reduce((m, n) => Math.max(m, n.in + n.out), 1)
  const pos = new Map<string, { x: number; y: number; r: number }>()
  ranked.forEach((n, i) => {
    const theta = (i / ranked.length) * Math.PI * 2 - Math.PI / 2
    const r = 4 + Math.round(((n.in + n.out) / maxDeg) * 7)
    pos.set(n.id, { x: cx + R * Math.cos(theta), y: cy + R * Math.sin(theta), r })
  })
  const edges = graph.edges.filter((e) => pos.has(e.source) && pos.has(e.target))
  return { W, H, ranked, pos, edges }
}

function GraphCard({ a }: { a: Extract<Artifact, { type: "graph" }> }) {
  const { graph } = a
  const { W, H, ranked, pos, edges } = graphLayout(graph)
  const empty = graph.nodes.length === 0

  return (
    <div className="rounded-apple-lg border border-[hsl(var(--glass-border))] overflow-hidden glass-2">
      <div className="px-5 py-3.5 flex items-center gap-3 border-b border-[hsl(var(--glass-border))]">
        <span className="w-9 h-9 rounded-xl bg-emerald-100 text-emerald-600 grid place-items-center shrink-0">
          <Waypoints className="w-[18px] h-[18px]" />
        </span>
        <div className="min-w-0">
          <div className="text-[14px] font-semibold text-foreground">知识图谱</div>
          <div className="text-[12px] text-muted-foreground">
            {graph.nodes.length} 概念 · {graph.edges.length} 关联
            {graph.types.length > 0 && <> · {graph.types.length} 类型</>}
          </div>
        </div>
      </div>
      {empty ? (
        <div className="px-5 py-8 text-center text-[13px] text-muted-foreground">
          该知识库暂无可视化的概念图谱
        </div>
      ) : (
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="w-full bg-gradient-to-b from-muted/30 to-transparent"
        >
          {edges.map((e, i) => {
            const s = pos.get(e.source)!
            const t = pos.get(e.target)!
            return (
              <line
                key={i}
                x1={s.x}
                y1={s.y}
                x2={t.x}
                y2={t.y}
                className={i % 2 ? "stroke-accent-brand/25" : "stroke-accent-brand/40"}
                strokeWidth="1"
              />
            )
          })}
          {ranked.map((n) => {
            const p = pos.get(n.id)!
            return (
              <circle
                key={n.id}
                cx={p.x}
                cy={p.y}
                r={p.r}
                className="fill-background stroke-accent-brand"
                strokeWidth="1.5"
              >
                <title>{n.label}</title>
              </circle>
            )
          })}
        </svg>
      )}
    </div>
  )
}

export default function ArtifactCard({ artifact }: { artifact: Artifact }) {
  switch (artifact.type) {
    case "deck":
      return <DeckCard a={artifact} />
    case "skill":
      return <SkillCard a={artifact} />
    case "graph":
      return <GraphCard a={artifact} />
    default:
      return (
        <div className="rounded-apple-lg border border-[hsl(var(--glass-border))] glass-2 px-5 py-4 flex items-center gap-3">
          <FileText className="w-4 h-4 text-muted-foreground" />
          <span className="text-[13px] text-foreground">未知产物</span>
        </div>
      )
  }
}
