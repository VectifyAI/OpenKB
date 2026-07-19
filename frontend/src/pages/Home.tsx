import { useEffect, useState } from "react"
import { useLocation, useNavigate } from "react-router"
import { ArrowRight, Clock } from "lucide-react"
import ChatInput, { type SlashCommand } from "@/components/ChatInput"
import { listKbs, type KbSummary } from "@/api/kb"
import { listSessions, type ChatSessionItem } from "@/api/chat"
import { cn } from "@/lib/utils"

/** Decorative accent colors, cycled by KB position — the API carries no color. */
const DOTS = ["bg-blue-500", "bg-emerald-500", "bg-amber-500", "bg-violet-500", "bg-rose-500"]
const dotFor = (i: number) => DOTS[i % DOTS.length]

/** A session enriched with the KB it belongs to (sessions are per-KB). */
interface RecentSession extends ChatSessionItem {
  kb: string
  kbIndex: number
}

function formatWhen(iso: string): string {
  if (!iso) return ""
  return iso.replace("T", " ").replace("Z", "").slice(0, 16)
}

export default function Home() {
  const navigate = useNavigate()
  const location = useLocation() as { state?: { kbId?: string } }
  const [kbs, setKbs] = useState<KbSummary[]>([])
  const [kbId, setKbId] = useState<string>(location.state?.kbId ?? "")
  const [recent, setRecent] = useState<RecentSession[]>([])

  useEffect(() => {
    let cancelled = false
    listKbs()
      .then(async (r) => {
        if (cancelled) return
        const list = r.knowledge_bases
        setKbs(list)
        setKbId((prev) => prev || list[0]?.name || "")
        // No cross-KB aggregate endpoint — fetch each KB's sessions and merge.
        const perKb = await Promise.all(
          list.map((kb, i) =>
            listSessions(kb.name)
              .then((res) => res.sessions.map((s): RecentSession => ({ ...s, kb: kb.name, kbIndex: i })))
              .catch(() => [] as RecentSession[]),
          ),
        )
        if (cancelled) return
        const merged = perKb
          .flat()
          .sort((a, b) => (a.updated_at < b.updated_at ? 1 : a.updated_at > b.updated_at ? -1 : 0))
          .slice(0, 6)
        setRecent(merged)
      })
      .catch(() => {
        if (!cancelled) setKbs([])
      })
    return () => { cancelled = true }
  }, [])

  const totalDocs = kbs.reduce((a, k) => a + k.document_count, 0)

  const send = (text: string, command: SlashCommand | null) => {
    // A selected command may carry no text (e.g. `/visualize` takes no args).
    if (!kbId || (!text.trim() && !command)) return
    navigate("/chat/new", {
      state: { text, commandId: command?.id ?? null, cmd: command?.cmd ?? null, kbId },
    })
  }

  return (
    <div className="h-full flex flex-col">
      {/* 上方：问候 + 最近会话（可滚动） */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        <div className="max-w-[1100px] mx-auto px-6 lg:px-8 pt-[7vh] pb-6">
          <div className="anim-fade-up">
            <h1 className="text-[30px] font-bold tracking-[-0.02em]">有什么想问的？</h1>
            <p className="mt-1.5 text-[14px] text-muted-foreground">
              知识已编译就绪 · <span className="tabular-nums">{totalDocs}</span> 篇文档 ·{" "}
              <span className="tabular-nums">{kbs.length}</span> 个知识库
            </p>
          </div>

          {recent.length > 0 && (
            <div className="mt-8 anim-fade-up anim-d2">
              <div className="flex items-center gap-2 text-[12px] font-semibold text-muted-foreground tracking-wide">
                <Clock className="w-3.5 h-3.5" />
                最近会话
              </div>
              <div className="mt-3 grid grid-cols-1 sm:grid-cols-3 xl:grid-cols-4 gap-3">
                {recent.map((s, i) => (
                  <button
                    key={`${s.kb}/${s.id}`}
                    onClick={() =>
                      navigate(`/chat/${encodeURIComponent(s.id)}`, { state: { kbId: s.kb } })
                    }
                    className={cn(
                      "group text-left rounded-apple-md glass-2 border border-[hsl(var(--glass-border))] p-4 hover:shadow-glass hover:-translate-y-0.5 transition-all duration-fast ease-out-apple anim-fade-up",
                      `anim-d${(i % 4) + 1}`,
                    )}
                  >
                    <div className="text-[14px] font-semibold leading-snug line-clamp-2 min-h-[40px]">
                      {s.title || "未命名会话"}
                    </div>
                    <div className="mt-3 flex items-center gap-1.5 text-[11.5px] text-muted-foreground">
                      <span className={cn("w-1.5 h-1.5 rounded-full", dotFor(s.kbIndex))} />
                      {s.kb}
                      {s.updated_at && (
                        <>
                          <span className="opacity-50">·</span>
                          {formatWhen(s.updated_at)}
                        </>
                      )}
                      <ArrowRight className="w-3.5 h-3.5 ml-auto opacity-0 -translate-x-1 group-hover:opacity-100 group-hover:translate-x-0 transition-all text-accent-brand" />
                    </div>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* 底部：输入框（钉底，斜杠菜单向上弹有干净空间）+ tagline footer */}
      <div className="shrink-0 border-t border-[hsl(var(--glass-border))] glass-2">
        <div className="max-w-[1100px] mx-auto px-6 lg:px-8 pt-3 pb-2">
          <ChatInput kbId={kbId} onKbChange={setKbId} onSend={send} autoFocus large />
          <p className="mt-2 text-center text-[11px] text-muted-foreground/70">
            由 OpenKB 驱动 · 你的知识始终是纯 Markdown，归你所有
          </p>
        </div>
      </div>
    </div>
  )
}
