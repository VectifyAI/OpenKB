import { useEffect, useRef, useState } from "react"
import { useLocation, useNavigate, useParams } from "react-router"
import { ArrowLeft, FileText, FolderInput, Loader2, Sparkles, BookText } from "lucide-react"
import { toast } from "sonner"
import ChatInput, { slashCommands, type SlashCommand } from "@/components/ChatInput"
import MarkdownView from "@/components/MarkdownView"
import {
  Sheet, SheetContent, SheetHeader, SheetTitle,
} from "@/components/ui/sheet"
import { getPage } from "@/api/wiki"
import { listKbs } from "@/api/kb"
import {
  foldSseEvent, initialTurnState, listSessions, loadSession,
  streamChat, streamQuery,
  type ChatTurnState, type Source,
} from "@/api/chat"

let seq = 100
const nid = () => `m${seq++}`

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e))

/** A user turn, or an assistant turn holding its folded SSE state. */
type Msg =
  | { id: string; role: "user"; text: string; command?: string }
  | { id: string; role: "assistant"; turn: ChatTurnState }

interface NavState {
  text?: string
  commandId?: string | null
  cmd?: string | null
  kbId?: string
}

/** The side panel that renders a clicked `read_file` source's real page. */
interface PanelState {
  open: boolean
  path: string
  content: string | null
  error: string | null
  loading: boolean
}

const CLOSED_PANEL: PanelState = { open: false, path: "", content: null, error: null, loading: false }

function SourceChips({ sources, onOpen }: { sources: Source[]; onOpen: (s: Source) => void }) {
  if (sources.length === 0) return null
  return (
    <div className="mt-3">
      <div className="mb-1.5 text-[11px] font-semibold text-neutral-400 tracking-wide">参考来源</div>
      <div className="flex flex-wrap gap-1.5">
        {sources.map((s, i) =>
          s.kind === "page" ? (
            <button
              key={`${s.path}-${i}`}
              onClick={() => onOpen(s)}
              title="点击查看该 wiki 页面"
              className="inline-flex items-center gap-1.5 h-6.5 px-2.5 rounded-md bg-white border border-black/8 text-[11.5px] font-mono2 text-neutral-500 hover:text-blue-600 hover:border-blue-300 transition-colors"
            >
              <FileText className="w-3 h-3" />{s.label}
            </button>
          ) : (
            <span
              key={`${s.docName}-${i}`}
              title="长文档内部内容（PageIndex），无独立页面可打开"
              className="inline-flex items-center gap-1.5 h-6.5 px-2.5 rounded-md bg-neutral-50 border border-black/6 text-[11.5px] font-mono2 text-neutral-400"
            >
              <BookText className="w-3 h-3" />{s.label}
            </span>
          ),
        )}
      </div>
    </div>
  )
}

function AssistantMessage({ turn, onOpen }: { turn: ChatTurnState; onOpen: (s: Source) => void }) {
  const streaming = !turn.done
  const showThinking = streaming && !turn.answer && !turn.error
  return (
    <div className="flex gap-3 anim-fade-up">
      <span className="w-7 h-7 rounded-lg bg-blue-600 text-white grid place-items-center shrink-0 mt-0.5">
        <Sparkles className="w-3.5 h-3.5" />
      </span>
      <div className="min-w-0 flex-1 max-w-[640px]">
        {/* 执行状态：正在查阅 X…（来自实时 tool_call 流） */}
        {streaming && (turn.reading || showThinking) && (
          <div className="mb-3 inline-flex items-center gap-2 rounded-xl border border-black/6 bg-neutral-50/80 px-3 py-1.5 text-[12.5px] text-neutral-500">
            <Loader2 className="w-3.5 h-3.5 animate-spin text-blue-500" />
            {turn.reading
              ? (turn.reading.kind === "page"
                  ? <>正在查阅 <span className="font-mono2 text-neutral-600">{turn.reading.label}</span></>
                  : <>正在读取长文档 <span className="font-mono2 text-neutral-600">{turn.reading.label}</span></>)
              : <>正在思考…</>}
          </div>
        )}

        {/* 正文（随 delta 流式渲染） */}
        {turn.answer && (
          <div className="text-[14px]">
            <MarkdownView source={turn.answer} />
          </div>
        )}

        {/* 错误 */}
        {turn.error && (
          <div className="mt-1 rounded-lg bg-red-50 border border-red-200/70 px-3 py-2 text-[13px] text-red-600">
            请求失败：{turn.error}
          </div>
        )}

        {/* 参考来源（whitelist 过滤、按轮去重） */}
        <SourceChips sources={turn.sources} onOpen={onOpen} />

        {/* 沉淀提示（query 的 saved_path） */}
        {turn.savedPath && (
          <div className="mt-3 inline-flex items-center gap-1.5 text-[12px] text-emerald-700 bg-emerald-50 border border-emerald-200/70 rounded-lg px-2.5 py-1.5">
            <FolderInput className="w-3.5 h-3.5" />已保存到 {turn.savedPath}
          </div>
        )}
      </div>
    </div>
  )
}

export default function ChatSession() {
  const { id } = useParams()
  const location = useLocation() as { state?: NavState }
  const navigate = useNavigate()

  const [kb, setKbState] = useState<string>(location.state?.kbId ?? "")
  const kbRef = useRef(kb)
  const setKb = (v: string) => { kbRef.current = v; setKbState(v) }

  const sessionIdRef = useRef<string | null>(id && id !== "new" ? id : null)

  const [msgs, setMsgs] = useState<Msg[]>([])
  const [running, setRunning] = useState(false)
  const [panel, setPanel] = useState<PanelState>(CLOSED_PANEL)

  const scrollRef = useRef<HTMLDivElement>(null)
  const startedRef = useRef(false)
  const restoredRef = useRef(false)

  /** Stream one assistant turn, folding each SSE event into its state live. */
  const runTurn = async (question: string, command: SlashCommand | null) => {
    const activeKb = kbRef.current
    if (!activeKb) return
    setRunning(true)
    const assistantId = nid()
    setMsgs((m) => [...m, { id: assistantId, role: "assistant", turn: initialTurnState() }])

    const patch = (fn: (t: ChatTurnState) => ChatTurnState) =>
      setMsgs((m) => m.map((x) => (x.id === assistantId && x.role === "assistant" ? { ...x, turn: fn(x.turn) } : x)))

    // `/ask` is a stateless one-off query; everything else is a chat turn that
    // persists into a session.
    const isAsk = command?.id === "ask"
    try {
      const stream = isAsk
        ? streamQuery(activeKb, question, { save: false })
        : streamChat(activeKb, sessionIdRef.current, question)
      for await (const event of stream) {
        patch((t) => foldSseEvent(t, event))
        if (!isAsk && event.event === "final" && typeof event.data?.session_id === "string") {
          const sid = event.data.session_id as string
          if (sid && sid !== sessionIdRef.current) {
            sessionIdRef.current = sid
            // Make the session addressable/reloadable without remounting.
            navigate(`/chat/${encodeURIComponent(sid)}`, { replace: true, state: { kbId: activeKb } })
          }
        }
      }
    } catch (e) {
      const message = errMsg(e)
      patch((t) => ({ ...t, reading: null, error: t.error ?? message, done: true }))
      toast.error(`请求失败：${message}`)
    } finally {
      patch((t) => ({ ...t, reading: null, done: true }))
      setRunning(false)
    }
  }

  // New session opened from Home: seed the user message and run the agent once.
  useEffect(() => {
    const st = location.state
    if (id !== "new" || !st?.text || startedRef.current) return
    startedRef.current = true
    setKb(st.kbId ?? "")
    const command = st.commandId ? slashCommands.find((c) => c.id === st.commandId) ?? null : null
    setMsgs([{ id: nid(), role: "user", text: st.text, command: command?.cmd }])
    void runTurn(st.text, command)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  // Existing session (deep link / reload): resolve its KB, then restore turns.
  useEffect(() => {
    if (id === "new" || restoredRef.current) return
    restoredRef.current = true
    let cancelled = false

    const restore = async () => {
      let resolvedKb = kbRef.current
      if (!resolvedKb) {
        // No nav state (cold reload): find which KB owns this session id.
        try {
          const r = await listKbs()
          for (const k of r.knowledge_bases) {
            const res = await listSessions(k.name).catch(() => null)
            if (res && res.sessions.some((s) => s.id === id)) { resolvedKb = k.name; break }
          }
        } catch {
          // ignore — handled by the empty resolvedKb check below
        }
      }
      if (cancelled) return
      if (!resolvedKb) {
        toast.error("无法定位该会话所属的知识库，请从首页进入")
        return
      }
      setKb(resolvedKb)
      sessionIdRef.current = id ?? null
      try {
        const loaded = await loadSession(resolvedKb, id as string)
        if (cancelled) return
        const restored: Msg[] = []
        const n = Math.max(loaded.user_turns.length, loaded.assistant_texts.length)
        for (let i = 0; i < n; i++) {
          if (loaded.user_turns[i] !== undefined)
            restored.push({ id: nid(), role: "user", text: loaded.user_turns[i] })
          if (loaded.assistant_texts[i] !== undefined) {
            // Restored turns carry no sources (they are live-derived, not stored).
            restored.push({
              id: nid(),
              role: "assistant",
              turn: { ...initialTurnState(), answer: loaded.assistant_texts[i], done: true, sessionId: id ?? null },
            })
          }
        }
        setMsgs(restored)
      } catch (e) {
        if (!cancelled) toast.error(`加载会话失败：${errMsg(e)}`)
      }
    }
    void restore()
    return () => { cancelled = true }
  }, [id])

  // Auto-scroll to the newest content.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" })
  }, [msgs])

  const openSource = async (s: Source) => {
    if (s.kind !== "page" || !s.path) return
    setPanel({ open: true, path: s.path, content: null, error: null, loading: true })
    try {
      const r = await getPage(kbRef.current, s.path)
      setPanel({ open: true, path: s.path, content: r.content, error: null, loading: false })
    } catch (e) {
      // A tool_call firing never guaranteed the read succeeded, and there is no
      // tool_result event to confirm it — so a click can 404. Fail gracefully.
      const message = errMsg(e)
      setPanel((p) => ({ ...p, loading: false, error: message }))
      toast.error(`无法打开来源 ${s.path}：${message}`)
    }
  }

  const send = (text: string, command: SlashCommand | null) => {
    if (running || !text.trim() || !kbRef.current) return
    setMsgs((m) => [...m, { id: nid(), role: "user", text, command: command?.cmd }])
    void runTurn(text, command)
  }

  const firstUser = msgs.find((m) => m.role === "user") as Extract<Msg, { role: "user" }> | undefined
  const title = firstUser?.text.slice(0, 24) || "新会话"

  return (
    <div className="h-full flex flex-col">
      {/* 会话头 */}
      <div className="shrink-0 h-12 flex items-center gap-3 px-5 border-b border-black/6 bg-white/60 backdrop-blur">
        <button onClick={() => navigate("/")} className="w-7 h-7 rounded-lg grid place-items-center text-neutral-400 hover:bg-neutral-100 hover:text-neutral-700 transition-colors">
          <ArrowLeft className="w-4 h-4" />
        </button>
        <div className="min-w-0">
          <div className="text-[14px] font-semibold text-neutral-800 truncate">{title}</div>
        </div>
        {kb && (
          <span className="ml-auto inline-flex items-center gap-1.5 text-[11.5px] text-neutral-400 bg-neutral-100 rounded-full px-2.5 py-1">
            <span className="w-1.5 h-1.5 rounded-full bg-blue-500" />{kb}
          </span>
        )}
      </div>

      {/* 消息流 */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        <div className="max-w-[760px] mx-auto px-6 py-6 space-y-6">
          {msgs.map((m) =>
            m.role === "user" ? (
              <div key={m.id} className="flex justify-end anim-fade-up">
                <div className="max-w-[560px] rounded-2xl rounded-br-md bg-neutral-900 text-white px-4 py-2.5">
                  {m.command && (
                    <span className="inline-block font-mono2 text-[11.5px] text-blue-300 bg-white/10 rounded px-1.5 py-0.5 mr-2 mb-0.5">{m.command}</span>
                  )}
                  <span className="text-[14px] leading-relaxed">{m.text}</span>
                </div>
              </div>
            ) : (
              <AssistantMessage key={m.id} turn={m.turn} onOpen={openSource} />
            ),
          )}
          <div className="h-2" />
        </div>
      </div>

      {/* 底部输入 */}
      <div className="shrink-0 px-6 pb-5 pt-2 bg-gradient-to-t from-[#f7f7f4] via-[#f7f7f4] to-transparent">
        <div className="max-w-[760px] mx-auto">
          <ChatInput
            kbId={kb}
            onKbChange={setKb}
            onSend={send}
            disabled={running}
            placeholder="继续追问，或输入 / 使用命令…"
          />
        </div>
      </div>

      {/* 来源侧栏：点击 read_file 来源打开真实页面 */}
      <Sheet open={panel.open} onOpenChange={(o) => setPanel((p) => ({ ...p, open: o }))}>
        <SheetContent side="right" className="w-full sm:max-w-[560px] overflow-y-auto">
          <SheetHeader>
            <SheetTitle className="font-mono2 text-[13px] text-neutral-600 break-all">wiki/{panel.path}</SheetTitle>
          </SheetHeader>
          <div className="px-4 pb-8">
            {panel.loading && (
              <div className="flex items-center gap-2 text-[13px] text-neutral-400">
                <Loader2 className="w-3.5 h-3.5 animate-spin" />加载中…
              </div>
            )}
            {panel.error && (
              <div className="rounded-lg bg-red-50 border border-red-200/70 px-3 py-2 text-[13px] text-red-600">
                页面加载失败：{panel.error}
              </div>
            )}
            {panel.content !== null && <MarkdownView source={panel.content} />}
          </div>
        </SheetContent>
      </Sheet>
    </div>
  )
}
