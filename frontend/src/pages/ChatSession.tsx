import { useEffect, useRef, useState } from "react"
import { useLocation, useNavigate, useParams } from "react-router"
import { useTranslation, Trans } from "react-i18next"
import type { TFunction } from "i18next"
import { ArrowLeft, FileText, FolderInput, Loader2, Sparkles, BookText, CheckCircle2 } from "lucide-react"
import { toast } from "sonner"
import ChatInput, { slashCommands, type SlashCommand } from "@/components/ChatInput"
import MarkdownView from "@/components/MarkdownView"
import ArtifactCard, { type Artifact } from "@/components/ArtifactCard"
import { AnimatePresence } from "motion/react"
import ArtifactPanel, { artifactKey } from "@/components/ArtifactPanel"
import {
  Sheet, SheetContent, SheetHeader, SheetTitle,
} from "@/components/ui/sheet"
import { getGraph, getPage } from "@/api/wiki"
import { listKbs } from "@/api/kb"
import { runDeckCommand, runSkillCommand } from "@/api/artifacts"
import type { SseEvent } from "@/api/client"
import {
  foldSseEvent, initialTurnState, listSessions, loadSession, markToolStepsDone,
  stepsFromTrace, streamChat,
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
  t: TFunction,
): ArtifactTurn {
  const data = (event?.data ?? {}) as Record<string, unknown>
  switch (event?.event) {
    case "start":
      return { ...state, phase: t("chat:phase.generating") }
    case "error": {
      const message = typeof data.message === "string" ? data.message : t("chat:artifact.failed")
      return { ...state, status: "error", error: message }
    }
    case "final": {
      const name = typeof data.name === "string" ? data.name : ""
      const status = typeof data.status === "string" ? data.status : "done"
      const path = typeof data.path === "string" ? data.path : ""
      const artifact: Artifact = { type: kind, kb, name, status, path }
      return { ...state, status: "done", phase: t("chat:phase.done"), artifact }
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

/**
 * One tool-read step in the interleaved turn trace: a compact status line that
 * shows a spinner while the read is in flight and a green ✅ once it resolves.
 * A `page` read is clickable (opens the real wiki page via {@link openSource},
 * exactly like the old source chip); a `doc` read is a non-clickable label
 * (its PageIndex-internal content has no standalone page to open).
 */
function ToolStep({ source, done, onOpen }: { source: Source; done: boolean; onOpen: (s: Source) => void }) {
  const { t } = useTranslation("chat")
  const TypeIcon = source.kind === "page" ? FileText : BookText
  const base =
    "inline-flex items-center gap-2 max-w-full rounded-xl border border-[hsl(var(--glass-border))] glass-2 px-3 py-1.5 text-[12.5px] text-muted-foreground"
  const inner = (
    <>
      {done ? (
        <CheckCircle2 className="w-3.5 h-3.5 shrink-0 text-emerald-500" />
      ) : (
        <Loader2 className="w-3.5 h-3.5 shrink-0 animate-spin text-accent-brand" />
      )}
      <TypeIcon className="w-3 h-3 shrink-0 opacity-70" />
      <span className="min-w-0 break-all">
        <Trans
          t={t}
          i18nKey="chat:step.read"
          values={{ path: source.label }}
          components={[<span className="font-mono2 text-foreground" />]}
        />
      </span>
    </>
  )
  if (source.kind === "page") {
    return (
      <button
        type="button"
        onClick={() => onOpen(source)}
        title={t("sources.pageTip")}
        className={`${base} text-left hover:text-accent-brand hover:border-accent-brand/40 transition duration-fast ease-out-apple active:scale-[0.97]`}
      >
        {inner}
      </button>
    )
  }
  return (
    <div className={base} title={t("sources.internalTip")}>
      {inner}
    </div>
  )
}

function AssistantMessage({
  turn,
  onOpen,
  onOpenArtifact,
}: {
  turn: ChatTurnState
  onOpen: (s: Source) => void
  onOpenArtifact: (a: Artifact) => void
}) {
  const { t } = useTranslation("chat")
  const streaming = !turn.done
  // "Thinking…" only before any trace exists; once steps arrive, the trailing
  // not-done tool step (or streaming text) carries the in-flight affordance.
  const showThinking = streaming && turn.steps.length === 0 && !turn.error
  return (
    <div className="flex gap-3 anim-fade-up">
      <span className="w-7 h-7 rounded-lg bg-accent-brand text-white grid place-items-center shrink-0 mt-0.5">
        <Sparkles className="w-3.5 h-3.5" />
      </span>
      <div className="min-w-0 flex-1 max-w-[720px]">
        {/* 思考中：尚无任何步骤时的等待态 */}
        {showThinking && (
          <div className="inline-flex items-center gap-2 rounded-xl border border-[hsl(var(--glass-border))] glass-2 px-3 py-1.5 text-[12.5px] text-muted-foreground">
            <Loader2 className="w-3.5 h-3.5 animate-spin text-accent-brand" />
            {t("chat:reading.thinking")}
          </div>
        )}

        {/* 有序步骤轨迹：叙述文本与工具读取按 SSE 到达顺序交错渲染 */}
        <div className="space-y-3">
          {turn.steps.map((step, i) =>
            step.kind === "text" ? (
              step.text.trim() ? (
                <div key={`step-${i}`} className="text-[14px]">
                  <MarkdownView
                    source={step.text}
                    onWikiLink={(target) => onOpen({ kind: "page", label: target, path: target })}
                  />
                </div>
              ) : null
            ) : (
              <div key={`step-${i}`}>
                <ToolStep source={step.source} done={step.done} onOpen={onOpen} />
              </div>
            ),
          )}
        </div>

        {/* 错误 */}
        {turn.error && (
          <div className="mt-3 rounded-lg bg-red-50 dark:bg-red-500/10 border border-red-200/70 dark:border-red-500/25 px-3 py-2 text-[13px] text-red-600 dark:text-red-400">
            {t("chat:requestError", { error: turn.error })}
          </div>
        )}

        {/* 会话中生成的可查看 HTML 文件（来自 write_file 的 artifact 事件） */}
        {turn.artifacts.length > 0 && (
          <div className="mt-3 space-y-2">
            {turn.artifacts.map((f) => (
              <ArtifactCard
                key={f.path}
                artifact={{ type: "file", kb: f.kb, name: f.name, path: f.path }}
                onOpen={onOpenArtifact}
              />
            ))}
          </div>
        )}

        {/* 沉淀提示（query 的 saved_path） */}
        {turn.savedPath && (
          <div className="mt-3 inline-flex items-center gap-1.5 text-[12px] text-emerald-700 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-500/10 border border-emerald-200/70 dark:border-emerald-500/25 rounded-lg px-2.5 py-1.5">
            <FolderInput className="w-3.5 h-3.5" />{t("chat:savedTo", { path: turn.savedPath })}
          </div>
        )}
      </div>
    </div>
  )
}

/** Renders a generator turn: a live status strip, then the finished artifact. */
function ArtifactMessage({ art, onOpen }: { art: ArtifactTurn; onOpen: (a: Artifact) => void }) {
  const { t } = useTranslation("chat")
  return (
    <div className="flex gap-3 anim-fade-up">
      <span className="w-7 h-7 rounded-lg bg-accent-brand text-white grid place-items-center shrink-0 mt-0.5">
        <Sparkles className="w-3.5 h-3.5" />
      </span>
      <div className="min-w-0 flex-1 max-w-[720px] space-y-3">
        {art.status === "streaming" && (
          <div className="inline-flex items-center gap-2 rounded-xl border border-[hsl(var(--glass-border))] glass-2 px-3 py-1.5 text-[12.5px] text-muted-foreground">
            <Loader2 className="w-3.5 h-3.5 animate-spin text-accent-brand" />
            {art.phase}
          </div>
        )}
        {art.status === "error" && (
          <div className="rounded-lg bg-red-50 dark:bg-red-500/10 border border-red-200/70 dark:border-red-500/25 px-3 py-2 text-[13px] text-red-600 dark:text-red-400">
            {art.error ?? t("chat:artifact.failed")}
          </div>
        )}
        {art.artifact && <ArtifactCard artifact={art.artifact} onOpen={onOpen} />}
      </div>
    </div>
  )
}

export default function ChatSession() {
  const { t } = useTranslation(["chat", "common"])
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

  // The docked artifact panel (deck/graph). Distinct from `panel` above, which
  // is the modal source-page Sheet. `panelArtifact` is the currently-open
  // viewable artifact, or null when the panel is closed.
  const [panelArtifact, setPanelArtifact] = useState<Artifact | null>(null)

  // Every viewable artifact this session produced (deck + graph + chat-turn
  // files) — the panel's switcher list. Skills are archives, not viewable, so
  // excluded. Deduped by artifact identity (re-running /visualize yields the
  // same graph key; a same-name /deck overwrites; a re-written output/*.html
  // path collapses to its latest) so the switcher shows one pill per artifact
  // and never emits duplicate React keys; the latest occurrence wins.
  const viewableArtifacts = Array.from(
    msgs
      .flatMap((m): Artifact[] => {
        if (m.role === "artifact" && m.art.artifact && m.art.artifact.type !== "skill") {
          return [m.art.artifact]
        }
        if (m.role === "assistant") {
          return m.turn.artifacts.map((f) => ({ type: "file", kb: f.kb, name: f.name, path: f.path }))
        }
        return []
      })
      .reduce((map, a) => map.set(artifactKey(a), a), new Map<string, Artifact>())
      .values(),
  )

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
      { id: artId, role: "artifact", art: { kind, status: "streaming", phase: t("chat:phase.preparing"), error: null, artifact: null } },
    ])
    const patch = (fn: (a: ArtifactTurn) => ArtifactTurn) =>
      setMsgs((m) => m.map((x) => (x.id === artId && x.role === "artifact" ? { ...x, art: fn(x.art) } : x)))

    try {
      if (kind === "graph") {
        patch((a) => ({ ...a, phase: t("chat:phase.buildingGraph") }))
        const graph = await getGraph(activeKb)
        patch((a) => ({ ...a, status: "done", phase: t("chat:phase.done"), artifact: { type: "graph", kb: activeKb, graph } }))
        return
      }
      // deck / skill: first token is the artifact name (kebab-case slug), the
      // rest is the free-text intent. Both are required (backend rejects empty).
      const parts = text.trim().split(/\s+/)
      const name = parts[0] ?? ""
      const intent = parts.slice(1).join(" ").trim()
      if (!name || !intent) {
        const usage = kind === "deck" ? t("chat:usage.deck") : t("chat:usage.skill")
        patch((a) => ({ ...a, status: "error", error: usage }))
        return
      }
      const stream = kind === "deck"
        ? runDeckCommand(activeKb, name, intent)
        : runSkillCommand(activeKb, name, intent)
      for await (const event of stream) {
        patch((a) => foldArtifactEvent(a, event, kind, activeKb, t))
      }
    } catch (e) {
      const message = errMsg(e)
      patch((a) => ({ ...a, status: a.status === "done" ? a.status : "error", error: a.error ?? message }))
      toast.error(t("chat:genErrorToast", { error: message }))
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

    try {
      const stream = streamChat(activeKb, sessionIdRef.current, question)
      for await (const event of stream) {
        patch((t) => foldSseEvent(t, event, activeKb))
        if (event.event === "final" && typeof event.data?.session_id === "string") {
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
      // Settle any tool read still in flight when the stream threw — otherwise
      // its spinner spins forever (done flips true but the step doesn't).
      patch((prev) => ({
        ...prev,
        reading: null,
        error: prev.error ?? message,
        done: true,
        steps: markToolStepsDone(prev.steps),
      }))
      toast.error(t("chat:requestErrorToast", { error: message }))
    } finally {
      patch((t) => ({ ...t, reading: null, done: true, steps: markToolStepsDone(t.steps) }))
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
        toast.error(t("chat:errors.noKb"))
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
            // Prefer the persisted per-turn trace so a restored turn shows the
            // same interleaved narration + tool reads it did live. Turns saved
            // before traces existed (or via the CLI) have none — fall back to
            // the flat answer as one text step. Sources stay live-only.
            const text = loaded.assistant_texts[i]
            const trace = loaded.assistant_traces?.[i]
            const steps =
              trace && trace.length ? stepsFromTrace(trace) : [{ kind: "text" as const, text }]
            restored.push({
              id: nid(),
              role: "assistant",
              turn: {
                ...initialTurnState(),
                answer: text,
                steps,
                done: true,
                sessionId: id ?? null,
              },
            })
          }
        }
        setMsgs(restored)
      } catch (e) {
        if (!cancelled) toast.error(t("chat:errors.loadSession", { error: errMsg(e) }))
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
      toast.error(t("chat:errors.openSource", { path: s.path, error: message }))
    }
  }

  const send = (text: string, command: SlashCommand | null) => {
    // A selected command may carry no text (e.g. `/visualize` takes no args).
    if (running || !kbRef.current || (!text.trim() && !command)) return
    setMsgs((m) => [...m, { id: nid(), role: "user", text, command: command?.cmd }])
    void runTurn(text, command)
  }

  const firstUser = msgs.find((m) => m.role === "user") as Extract<Msg, { role: "user" }> | undefined
  const title = firstUser?.text.slice(0, 24) || t("chat:newSession")

  return (
    <div className="h-full flex">
      <div className="flex-1 min-w-0 flex flex-col">
        {/* 会话头 */}
      <div className="shrink-0 h-12 flex items-center gap-3 px-5 border-b border-[hsl(var(--glass-border))] glass-2 backdrop-blur">
        <button onClick={() => navigate("/")} className="w-7 h-7 rounded-lg grid place-items-center text-muted-foreground hover:bg-accent hover:text-foreground transition-colors">
          <ArrowLeft className="w-4 h-4" />
        </button>
        <div className="min-w-0">
          <div className="text-[14px] font-semibold text-foreground truncate">{title}</div>
        </div>
      </div>

      {/* 消息流 */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        <div className="max-w-[860px] xl:max-w-[1000px] mx-auto px-6 py-6 space-y-6">
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
              <ArtifactMessage key={m.id} art={m.art} onOpen={setPanelArtifact} />
            ) : (
              <AssistantMessage
                key={m.id}
                turn={m.turn}
                onOpen={openSource}
                onOpenArtifact={setPanelArtifact}
              />
            ),
          )}
          <div className="h-2" />
        </div>
      </div>

      {/* 底部输入 */}
      <div className="shrink-0 px-6 pb-5 pt-2 bg-gradient-to-t from-[hsl(var(--ambient))] via-[hsl(var(--ambient))] to-transparent">
        <div className="max-w-[860px] xl:max-w-[1000px] mx-auto">
          <ChatInput
            kbId={kb}
            onKbChange={setKb}
            onSend={send}
            disabled={running}
            placeholder={t("chat:inputPlaceholder")}
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
                <Loader2 className="w-3.5 h-3.5 animate-spin" />{t("common:loading")}
              </div>
            )}
            {panel.error && (
              <div className="rounded-lg bg-red-50 dark:bg-red-500/10 border border-red-200/70 dark:border-red-500/25 px-3 py-2 text-[13px] text-red-600 dark:text-red-400">
                {t("common:pageLoadError", { error: panel.error })}
              </div>
            )}
            {panel.content !== null && <MarkdownView source={panel.content} />}
          </div>
        </SheetContent>
      </Sheet>
      </div>

      {/* 产物面板（Claude 式右侧停靠；deck / graph 在沙箱 iframe 内全高渲染） */}
      <AnimatePresence>
        {panelArtifact && (
          <ArtifactPanel
            key="artifact-panel"
            artifacts={viewableArtifacts}
            active={panelArtifact}
            onSwitch={setPanelArtifact}
            onClose={() => setPanelArtifact(null)}
          />
        )}
      </AnimatePresence>
    </div>
  )
}
