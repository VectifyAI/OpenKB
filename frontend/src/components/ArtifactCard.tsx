import { useState } from "react"
import {
  Presentation, Sparkles, Waypoints, Check, Loader2, FileText, FileCode, FileArchive, ArrowUpRight,
  type LucideIcon,
} from "lucide-react"
import { toast } from "sonner"
import { getSkillArchiveBlobUrl } from "@/api/artifacts"
import type { GraphData } from "@/api/wiki"

/**
 * A generated artifact produced by a `/deck`, `/skill`, or `/visualize` command.
 * Carries only REAL identifiers from the backend `final` event (`{name,status,
 * path}`) plus, for graphs, the real `getGraph` payload — no fabricated content.
 * Deck/graph render in the docked {@link ArtifactPanel} (opened via `onOpen`);
 * skill is an archive (download inline, never the panel).
 */
export type Artifact =
  | { type: "deck"; kb: string; name: string; status: string; path: string }
  | { type: "skill"; kb: string; name: string; status: string; path: string }
  | { type: "graph"; kb: string; graph: GraphData }
  | { type: "file"; kb: string; name: string; path: string }

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

/** Compact, clickable card for a viewable artifact — opens the docked panel. */
function OpenableCard({
  icon: Icon,
  tint,
  label,
  subtitle,
  onOpen,
}: {
  icon: LucideIcon
  tint: string
  label: string
  subtitle: string
  onOpen: () => void
}) {
  return (
    <button
      onClick={onOpen}
      className="group w-full text-left rounded-apple-lg border border-[hsl(var(--glass-border))] glass-2 px-4 py-3.5 flex items-center gap-3 hover:shadow-glass hover:-translate-y-0.5 transition duration-fast ease-out-apple active:scale-[0.99]"
    >
      <span className={`w-9 h-9 rounded-xl grid place-items-center shrink-0 ${tint}`}>
        <Icon className="w-[18px] h-[18px]" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="text-[14px] font-semibold text-foreground truncate">{label}</div>
        <div className="text-[12px] text-muted-foreground truncate">{subtitle}</div>
      </div>
      <span className="shrink-0 inline-flex items-center gap-1 text-[12px] font-medium text-accent-brand">
        打开
        <ArrowUpRight className="w-3.5 h-3.5 transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5" />
      </span>
    </button>
  )
}

function DeckCard({ a, onOpen }: { a: Extract<Artifact, { type: "deck" }>; onOpen?: (a: Artifact) => void }) {
  return (
    <OpenableCard
      icon={Presentation}
      tint="bg-blue-100 text-blue-600 dark:bg-blue-500/15 dark:text-blue-400"
      label={a.name}
      subtitle={`HTML 幻灯片 · ${baseName(a.path)}`}
      onOpen={() => onOpen?.(a)}
    />
  )
}

function FileCard({ a, onOpen }: { a: Extract<Artifact, { type: "file" }>; onOpen?: (a: Artifact) => void }) {
  return (
    <OpenableCard
      icon={FileCode}
      tint="bg-amber-100 text-amber-600 dark:bg-amber-500/15 dark:text-amber-400"
      label={a.name}
      subtitle={`HTML 文件 · ${baseName(a.path)}`}
      onOpen={() => onOpen?.(a)}
    />
  )
}

function GraphCard({ a, onOpen }: { a: Extract<Artifact, { type: "graph" }>; onOpen?: (a: Artifact) => void }) {
  const { graph } = a
  const subtitle =
    graph.nodes.length === 0
      ? "暂无可视化的概念图谱"
      : `${graph.nodes.length} 概念 · ${graph.edges.length} 关联${
          graph.types.length > 0 ? ` · ${graph.types.length} 类型` : ""
        }`
  return (
    <OpenableCard
      icon={Waypoints}
      tint="bg-emerald-100 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-400"
      label="知识图谱"
      subtitle={subtitle}
      onOpen={() => onOpen?.(a)}
    />
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
        <span className="w-9 h-9 rounded-xl bg-violet-100 text-violet-600 dark:bg-violet-500/15 dark:text-violet-400 grid place-items-center shrink-0">
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

export default function ArtifactCard({
  artifact,
  onOpen,
}: {
  artifact: Artifact
  onOpen?: (a: Artifact) => void
}) {
  switch (artifact.type) {
    case "deck":
      return <DeckCard a={artifact} onOpen={onOpen} />
    case "skill":
      return <SkillCard a={artifact} />
    case "graph":
      return <GraphCard a={artifact} onOpen={onOpen} />
    case "file":
      return <FileCard a={artifact} onOpen={onOpen} />
    default:
      return (
        <div className="rounded-apple-lg border border-[hsl(var(--glass-border))] glass-2 px-5 py-4 flex items-center gap-3">
          <FileText className="w-4 h-4 text-muted-foreground" />
          <span className="text-[13px] text-foreground">未知产物</span>
        </div>
      )
  }
}
