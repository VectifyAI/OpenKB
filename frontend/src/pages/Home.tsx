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
    <div className="h-full overflow-y-auto">
      <div className="max-w-[760px] mx-auto px-6 pt-[9vh] pb-16">
        {/* 问候 */}
        <div className="anim-fade-up">
          <h1 className="text-[30px] font-extrabold tracking-tight text-neutral-900">
            有什么想问的？
          </h1>
          <p className="mt-1.5 text-[14px] text-neutral-400">
            知识已编译就绪 · {totalDocs} 篇文档 · {kbs.length} 个知识库
          </p>
        </div>

        {/* 输入框 */}
        <div className="mt-7 anim-fade-up anim-d1">
          <ChatInput kbId={kbId} onKbChange={setKbId} onSend={send} autoFocus large />
        </div>

        {/* 最近会话 */}
        {recent.length > 0 && (
          <div className="mt-12 anim-fade-up anim-d3">
            <div className="flex items-center gap-2 text-[12px] font-semibold text-neutral-400 tracking-wide">
              <Clock className="w-3.5 h-3.5" />最近会话
            </div>
            <div className="mt-3 grid grid-cols-1 sm:grid-cols-3 gap-3">
              {recent.map((s, i) => (
                <button
                  key={`${s.kb}/${s.id}`}
                  onClick={() => navigate(`/chat/${encodeURIComponent(s.id)}`, { state: { kbId: s.kb } })}
                  className={cn(
                    "group text-left rounded-2xl border border-black/8 bg-white p-4 hover:shadow-md hover:border-blue-200 hover:-translate-y-0.5 transition-all anim-fade-up",
                    `anim-d${(i % 4) + 1}`,
                  )}
                >
                  <div className="text-[14px] font-semibold text-neutral-800 leading-snug line-clamp-2 min-h-[40px]">
                    {s.title || "未命名会话"}
                  </div>
                  <div className="mt-3 flex items-center gap-1.5 text-[11.5px] text-neutral-400">
                    <span className={cn("w-1.5 h-1.5 rounded-full", dotFor(s.kbIndex))} />
                    {s.kb}
                    {s.updated_at && (
                      <>
                        <span className="text-neutral-300">·</span>
                        {formatWhen(s.updated_at)}
                      </>
                    )}
                    <ArrowRight className="w-3.5 h-3.5 ml-auto opacity-0 -translate-x-1 group-hover:opacity-100 group-hover:translate-x-0 transition-all text-blue-500" />
                  </div>
                </button>
              ))}
            </div>
          </div>
        )}

        <p className="mt-14 text-center text-[12px] text-neutral-300 anim-fade-up anim-d4">
          由 OpenKB 驱动 · wiki 是纯 Markdown，随时可用 Obsidian 打开
        </p>
      </div>
    </div>
  )
}
