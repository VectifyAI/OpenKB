import { useEffect, useRef, useState } from "react"
import { useLocation, useNavigate, useParams } from "react-router"
import { ArrowLeft, FileText, FolderInput, Loader2, Sparkles, BookText } from "lucide-react"
import { toast } from "sonner"
import ChatInput, { slashCommands, type SlashCommand } from "@/components/ChatInput"
import MarkdownView from "@/components/MarkdownView"
import ArtifactCard, { type Artifact } from "@/components/ArtifactCard"
import {
  Sheet, SheetContent, SheetHeader, SheetTitle,
} from "@/components/ui/sheet"
import { getGraph, getPage } from "@/api/wiki"
import { listKbs } from "@/api/kb"
import { runDeckCommand, runSkillCommand } from "@/api/artifacts"
import type { SseEvent } from "@/api/client"
import {
  foldSseEvent, initialTurnState, listSessions, loadSession,
  streamChat, streamQuery,
  type ChatTurnState, type Source,
} from "@/api/chat"

let seq = 100
const nid = () => `m${seq++}`

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e))

/**
 * A generator turn (`/deck`, `/skill`, `/visualize`). Deck/skill accumulate an
 * SSE stream whose `final` event is `{ name, status, path }` (a DIFFERENT shape
 * than chat/query's `final` — folded by {@link foldArtifactEvent}, never
 * `foldSseEvent`). `/visualize` is a one-shot fetch, not a stream.
 */
interface ArtifactTurn {
  kind: "deck" | "skill" | "graph"
  status: "streaming" | "done" | "error"
  /** Human-readable phase label shown while streaming. */
  phase: string
  error: string | null
  /** The finished artifact, present once generation succeeds. */
  artifact: Artifact | null
}

/** A user turn, an assistant chat turn, or a generator (artifact) turn. */
type Msg =
  | { id: string; role: "user"; text: string; command?: string }
  | { id: string; role: "assistant"; turn: ChatTurnState }
  | { id: string; role: "artifact"; art: ArtifactTurn }

/**
 * Fold one deck/skill SSE event into the running artifact turn. Deliberately a
 * dedicated accumulator (NOT `foldSseEvent`): the deck/skill `final` carries
 * `{ name, status, path }`, whereas chat/query's `final` carries answer text /
 * session id — reusing that mapper would silently drop the artifact identity.
 */
function foldArtifactEvent(
  state: ArtifactTurn,
  event: SseEvent,
  kind: "deck" | "skill",
  kb: string,
): ArtifactTurn {
  const data = (event?.data ?? {}) as Record<string, unknown>
  switch (event?.event) {
    case "start":
      return { ...state, phase: "生成中（LLM 调用，可能需要数分钟）…" }
    case "error": {
      const message = typeof data.message === "string" ? data.message : "生成失败"
      return { ...state, status: "error", error: message }
    }
    case "final": {
      const name = typeof data.name === "string" ? data.name : ""
      const status = typeof data.status === "string" ? data.status : "done"
      const path = typeof data.path === "string" ? data.path : ""
      const artifact: Artifact = { type: kind, kb, name, status, path }
      return { ...state, status: "done", phase: "完成", artifact }
    }
    case "done":
      // Terminal frame. Keep an error already recorded; otherwise settle "done".
      if (state.status === "error") return state
      return { ...state, status: state.artifact ? "done" : state.status }
    default:
      // "start" handled above; ignore anything else.
      return state
  }
}

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
      <div className="mb-1.5 text-[11px] font-semibold text-muted-foreground tracking-wide">参考来源</div>
      <div className="flex flex-wrap gap-1.5">
        {sources.map((s, i) =>
          s.kind === "page" ? (
            <button
              key={`${s.path}-${i}`}
              onClick={() => onOpen(s)}
              title="点击查看该 wiki 页面"
              className="inline-flex items-center gap-1.5 h-6.5 px-2.5 rounded-md glass-2 border border-[hsl(var(--glass-border))] text-[11.5px] font-mono2 text-muted-foreground hover:text-accent-brand hover:border-accent-brand/40 transition duration-fast ease-out-apple active:scale-[0.97]"
            >
              <FileText className="w-3 h-3" />{s.label}
            </button>
          ) : (
            <span
              key={`${s.docName}-${i}`}
              title="长文档内部内容（PageIndex），无独立页面可打开"
              className="inline-flex items-center gap-1.5 h-6.5 px-2.5 rounded-md bg-muted/50 border border-[hsl(var(--glass-border))] text-[11.5px] font-mono2 text-muted-foreground"
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
      <span className="w-7 h-7 rounded-lg bg-accent-brand text-white grid place-items-center shrink-0 mt-0.5">
        <Sparkles className="w-3.5 h-3.5" />
      </span>
      <div className="min-w-0 flex-1 max-w-[640px]">
        {/* 执行状态：正在查阅 X…（来自实时 tool_call 流） */}
        {streaming && (turn.reading || showThinking) && (
          <div className="mb-3 inline-flex items-center gap-2 rounded-xl border border-[hsl(var(--glass-border))] glass-2 px-3 py-1.5 text-[12.5px] text-muted-foreground">
            <Loader2 className="w-3.5 h-3.5 animate-spin text-accent-brand" />
            {turn.reading
              ? (turn.reading.kind === "page"
                  ? <>正在查阅 <span className="font-mono2 text-foreground">{turn.reading.label}</span></>
                  : <>正在读取长文档 <span className="font-mono2 text-foreground">{turn.reading.label}</span></>)
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
          <div className="mt-1 rounded-lg bg-red-50 dark:bg-red-500/10 border border-red-200/70 dark:border-red-500/25 px-3 py-2 text-[13px] text-red-600 dark:text-red-400">
            请求失败：{turn.error}
          </div>
        )}

        {/* 参考来源（whitelist 过滤、按轮去重） */}
        <SourceChips sources={turn.sources} onOpen={onOpen} />

        {/* 沉淀提示（query 的 saved_path） */}
        {turn.savedPath && (
          <div className="mt-3 inline-flex items-center gap-1.5 text-[12px] text-emerald-700 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-500/10 border border-emerald-200/70 dark:border-emerald-500/25 rounded-lg px-2.5 py-1.5">
            <FolderInput className="w-3.5 h-3.5" />已保存到 {turn.savedPath}
          </div>
        )}
      </div>
    </div>
  )
}

/** Renders a generator turn: a live status strip, then the finished artifact. */
function ArtifactMessage({ art }: { art: ArtifactTurn }) {
  return (
    <div className="flex gap-3 anim-fade-up">
      <span className="w-7 h-7 rounded-lg bg-accent-brand text-white grid place-items-center shrink-0 mt-0.5">
        <Sparkles className="w-3.5 h-3.5" />
      </span>
      <div className="min-w-0 flex-1 max-w-[640px] space-y-3">
        {art.status === "streaming" && (
          <div className="inline-flex items-center gap-2 rounded-xl border border-[hsl(var(--glass-border))] glass-2 px-3 py-1.5 text-[12.5px] text-muted-foreground">
            <Loader2 className="w-3.5 h-3.5 animate-spin text-accent-brand" />
            {art.phase}
          </div>
        )}
        {art.status === "error" && (
          <div className="rounded-lg bg-red-50 dark:bg-red-500/10 border border-red-200/70 dark:border-red-500/25 px-3 py-2 text-[13px] text-red-600 dark:text-red-400">
            {art.error ?? "生成失败"}
          </div>
        )}
        {art.artifact && <ArtifactCard artifact={art.artifact} />}
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

  /**
   * Run a generator command (`/deck`, `/skill`, `/visualize`) against the real
   * backend and render its result as an {@link ArtifactCard}. Deck/skill stream
   * SSE (accumulated via {@link foldArtifactEvent}); `/visualize` is a one-shot
   * `getGraph` fetch (not SSE).
   */
  const runArtifactTurn = async (kind: "deck" | "skill" | "graph", text: string) => {
    const activeKb = kbRef.current
    if (!activeKb) return
    setRunning(true)
    const artId = nid()
    setMsgs((m) => [
      ...m,
      { id: artId, role: "artifact", art: { kind, status: "streaming", phase: "准备中…", error: null, artifact: null } },
    ])
    const patch = (fn: (a: ArtifactTurn) => ArtifactTurn) =>
      setMsgs((m) => m.map((x) => (x.id === artId && x.role === "artifact" ? { ...x, art: fn(x.art) } : x)))

    try {
      if (kind === "graph") {
        patch((a) => ({ ...a, phase: "构建概念图谱…" }))
        const graph = await getGraph(activeKb)
        patch((a) => ({ ...a, status: "done", phase: "完成", artifact: { type: "graph", kb: activeKb, graph } }))
        return
      }
      // deck / skill: first token is the artifact name (kebab-case slug), the
      // rest is the free-text intent. Both are required (backend rejects empty).
      const parts = text.trim().split(/\s+/)
      const name = parts[0] ?? ""
      const intent = parts.slice(1).join(" ").trim()
      if (!name || !intent) {
        const usage =
          kind === "deck"
            ? "用法：/deck <名称> <意图>，例如 “retrieval-intro 面向工程师介绍无向量检索”"
            : "用法：/skill <名称> <意图>，例如 “pageindex-expert 长文档检索问答专家”"
        patch((a) => ({ ...a, status: "error", error: usage }))
        return
      }
      const stream = kind === "deck"
        ? runDeckCommand(activeKb, name, intent)
        : runSkillCommand(activeKb, name, intent)
      for await (const event of stream) {
        patch((a) => foldArtifactEvent(a, event, kind, activeKb))
      }
    } catch (e) {
      const message = errMsg(e)
      patch((a) => ({ ...a, status: a.status === "done" ? a.status : "error", error: a.error ?? message }))
      toast.error(`生成失败：${message}`)
    } finally {
      // A stream that ended without a terminal `final`/`error` still settles.
      patch((a) => (a.status === "streaming" ? { ...a, status: a.error ? "error" : "done" } : a))
      setRunning(false)
    }
  }

  /** Stream one assistant turn, folding each SSE event into its state live. */
  const runTurn = async (question: string, command: SlashCommand | null) => {
    // Generator commands route to real deck/skill/graph endpoints, not chat.
    if (command?.id === "deck") return runArtifactTurn("deck", question)
    if (command?.id === "skill") return runArtifactTurn("skill", question)
    if (command?.id === "visualize") return runArtifactTurn("graph", question)

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
    // Seed on any text OR a bare command (e.g. `/visualize` with no text).
    if (id !== "new" || startedRef.current || (!st?.text && !st?.commandId)) return
    startedRef.current = true
    setKb(st.kbId ?? "")
    const command = st.commandId ? slashCommands.find((c) => c.id === st.commandId) ?? null : null
    const text = st.text ?? ""
    setMsgs([{ id: nid(), role: "user", text, command: command?.cmd }])
    void runTurn(text, command)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  // Existing session (deep link / reload): resolve its KB, then restore turns.
  useEffect(() => {
    if (restoredRef.current) return
    restoredRef.current = true
    // A brand-new session ("new") has nothing to restore — but we still mark
    // restoration as done here so that runTurn's self-triggered navigate() (which
    // adopts the real session id mid-conversation, changing `id` without a
    // remount) can't re-run this effect and clobber the live streamed msgs. A
    // genuine cold navigation to /chat/<id> remounts with a fresh restoredRef,
    // so real prior history still restores normally.
    if (id === "new") return
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
    // A selected command may carry no text (e.g. `/visualize` takes no args).
    if (running || !kbRef.current || (!text.trim() && !command)) return
    setMsgs((m) => [...m, { id: nid(), role: "user", text, command: command?.cmd }])
    void runTurn(text, command)
  }

  const firstUser = msgs.find((m) => m.role === "user") as Extract<Msg, { role: "user" }> | undefined
  const title = firstUser?.text.slice(0, 24) || "新会话"

  return (
    <div className="h-full flex flex-col">
      {/* 会话头 */}
      <div className="shrink-0 h-12 flex items-center gap-3 px-5 border-b border-[hsl(var(--glass-border))] glass-2 backdrop-blur">
        <button onClick={() => navigate("/")} className="w-7 h-7 rounded-lg grid place-items-center text-muted-foreground hover:bg-accent hover:text-foreground transition-colors">
          <ArrowLeft className="w-4 h-4" />
        </button>
        <div className="min-w-0">
          <div className="text-[14px] font-semibold text-foreground truncate">{title}</div>
        </div>
        {kb && (
          <span className="ml-auto inline-flex items-center gap-1.5 text-[11.5px] text-muted-foreground bg-muted rounded-full px-2.5 py-1">
            <span className="w-1.5 h-1.5 rounded-full bg-accent-brand" />{kb}
          </span>
        )}
      </div>

      {/* 消息流 */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        <div className="max-w-[760px] mx-auto px-6 py-6 space-y-6">
          {msgs.map((m) =>
            m.role === "user" ? (
              <div key={m.id} className="flex justify-end anim-fade-up">
                <div className="max-w-[560px] rounded-2xl rounded-br-md bg-accent-brand text-white px-4 py-2.5">
                  {m.command && (
                    <span className="inline-block font-mono2 text-[11.5px] text-white/90 bg-white/15 rounded px-1.5 py-0.5 mr-2 mb-0.5">{m.command}</span>
                  )}
                  <span className="text-[14px] leading-relaxed">{m.text}</span>
                </div>
              </div>
            ) : m.role === "artifact" ? (
              <ArtifactMessage key={m.id} art={m.art} />
            ) : (
              <AssistantMessage key={m.id} turn={m.turn} onOpen={openSource} />
            ),
          )}
          <div className="h-2" />
        </div>
      </div>

      {/* 底部输入 */}
      <div className="shrink-0 px-6 pb-5 pt-2 bg-gradient-to-t from-[hsl(var(--ambient))] via-[hsl(var(--ambient))] to-transparent">
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
            <SheetTitle className="font-mono2 text-[13px] text-muted-foreground break-all">wiki/{panel.path}</SheetTitle>
          </SheetHeader>
          <div className="px-4 pb-8">
            {panel.loading && (
              <div className="flex items-center gap-2 text-[13px] text-muted-foreground">
                <Loader2 className="w-3.5 h-3.5 animate-spin" />加载中…
              </div>
            )}
            {panel.error && (
              <div className="rounded-lg bg-red-50 dark:bg-red-500/10 border border-red-200/70 dark:border-red-500/25 px-3 py-2 text-[13px] text-red-600 dark:text-red-400">
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
