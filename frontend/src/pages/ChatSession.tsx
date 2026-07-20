import { useEffect, useRef, useState } from "react"
import { useLocation, useNavigate, useParams } from "react-router"
import { useTranslation, Trans } from "react-i18next"
import type { TFunction } from "i18next"
import { ArrowLeft, FileText, FolderInput, Loader2, Sparkles, BookText, CheckCircle2, CircleStop } from "lucide-react"
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
  // Whether the running turn is abortable — i.e. a live AbortController exists.
  // Chat/query and deck/skill turns set this; the `/visualize` graph turn is a
  // one-shot `getGraph` with no signal to thread, so it is NOT abortable. The
  // Stop button gates on this so it never appears as a visible no-op.
  const [stoppable, setStoppable] = useState(false)
  const [panel, setPanel] = useState<PanelState>(CLOSED_PANEL)

  // AbortController for the in-flight chat/query stream — one per turn. The Stop
  // button and the unmount cleanup both abort via this ref; runTurn passes its
  // `.signal` to streamChat and clears the ref once the turn settles.
  const abortRef = useRef<AbortController | null>(null)

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
  // The session id whose turns are currently held in `msgs`. Set both when the
  // restore effect finishes loading a session AND when runTurn adopts a real
  // session id mid-turn (the new→<sid> self-navigate, which sets this to <sid>
  // just before navigating). The restore effect reloads ONLY when the route
  // `id` differs from this — so the self-navigate is recognised as "msgs already
  // are this session" and does NOT clobber the just-finished turn (including its
  // turn.artifacts open-cards); a genuine switch to a different session reloads.
  const msgsSessionIdRef = useRef<string | null>(null)
  // Pending deferred unmount-abort timer — see the abort-on-unmount effect. Held
  // so a React 18 StrictMode dev remount can cancel it before it fires.
  const pendingUnmountAbort = useRef<ReturnType<typeof setTimeout> | null>(null)

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

    // Deck/skill stream a cancellable fetch — give them a controller and expose
    // it via abortRef (same pattern as chat/query) so the Stop button aborts the
    // fetch → the backend disconnects. Graph is a one-shot getGraph with no
    // signal to thread, so it gets none and stays non-abortable (Stop hidden).
    const controller = kind === "graph" ? null : new AbortController()
    if (controller) {
      abortRef.current = controller
      setStoppable(true)
    }

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
        ? runDeckCommand(activeKb, name, intent, controller!.signal)
        : runSkillCommand(activeKb, name, intent, controller!.signal)
      for await (const event of stream) {
        patch((a) => foldArtifactEvent(a, event, kind, activeKb, t))
      }
    } catch (e) {
      // A user-initiated abort (Stop button / session switch) is a CLEAN stop,
      // not a failure: no error toast — the finally settles the streaming strip.
      // Everything else surfaces as a real error, same as before.
      const aborted = controller?.signal.aborted || (e as { name?: string })?.name === "AbortError"
      if (!aborted) {
        const message = errMsg(e)
        patch((a) => ({ ...a, status: a.status === "done" ? a.status : "error", error: a.error ?? message }))
        toast.error(t("chat:genErrorToast", { error: message }))
      }
    } finally {
      // A stream that ended without a terminal `final`/`error` (or was aborted)
      // still settles.
      patch((a) => (a.status === "streaming" ? { ...a, status: a.error ? "error" : "done" } : a))
      // Relinquish the shared ref only if this turn still owns it.
      if (controller && abortRef.current === controller) abortRef.current = null
      setStoppable(false)
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

    // Fresh controller for this turn — the Stop button / unmount abort it.
    const controller = new AbortController()
    abortRef.current = controller
    setStoppable(true)

    try {
      const stream = streamChat(activeKb, sessionIdRef.current, question, controller.signal)
      for await (const event of stream) {
        patch((t) => foldSseEvent(t, event, activeKb))
        if (event.event === "final" && typeof event.data?.session_id === "string") {
          const sid = event.data.session_id as string
          if (sid && sid !== sessionIdRef.current) {
            sessionIdRef.current = sid
            // The live turn (incl. its streamed artifacts) IS this session — tell
            // the restore effect so its id-change re-run treats <sid> as already
            // loaded and does NOT reload over the live msgs. Must be set BEFORE
            // navigate() triggers the id change.
            msgsSessionIdRef.current = sid
            // Make the session addressable/reloadable without remounting.
            navigate(`/chat/${encodeURIComponent(sid)}`, { replace: true, state: { kbId: activeKb } })
          }
        }
      }
    } catch (e) {
      // A user-initiated abort (Stop button or navigate-away unmount) is a CLEAN
      // stop, not a failure: settle the turn with whatever streamed so far and
      // NO error toast. Everything else keeps the real error handling below.
      const aborted = controller.signal.aborted || (e as { name?: string })?.name === "AbortError"
      if (!aborted) {
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
      }
    } finally {
      // Only relinquish the shared ref if this turn still owns it (a later turn
      // may have already replaced it).
      if (abortRef.current === controller) abortRef.current = null
      patch((t) => ({ ...t, reading: null, done: true, steps: markToolStepsDone(t.steps) }))
      setStoppable(false)
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

  // Existing session (deep link / reload), or a genuine switch to a DIFFERENT
  // session: resolve its KB, then restore turns. Re-runs on every `id` change —
  // there is no key={id} remount — so navigating between two saved sessions
  // (/chat/A → /chat/B) reloads the right one, fixing the stale-session bug the
  // remount used to guard against. But it is a deliberate no-op when `msgs`
  // already correspond to `id`, which is what lets runTurn's mid-turn
  // self-navigate (new→<sid>) keep the just-finished turn (incl. turn.artifacts)
  // instead of reloading over it.
  useEffect(() => {
    // A brand-new session ("new") has nothing to restore — the seeded effect
    // owns it.
    if (id === "new" || !id) return
    // msgs already ARE this session (self-navigate adoption, or we just restored
    // it): reloading would drop the live turn's artifact open-cards and flash
    // the answer. Skip.
    if (msgsSessionIdRef.current === id) return

    // A real (re)load of a different session. The key={id} remount used to reset
    // per-session state for us; without it we reset here:
    //  - abort any stream still live for the previous session (no unmount fires
    //    on an in-place id change, so nothing else would stop it);
    //  - reset the KB (prefer the nav-provided kbId, else clear so the loop
    //    below resolves the owning KB);
    //  - clear the previous session's msgs and any open panels.
    abortRef.current?.abort()
    abortRef.current = null
    setRunning(false)
    setStoppable(false)
    setKb(location.state?.kbId ?? "")
    sessionIdRef.current = id
    setMsgs([])
    setPanel(CLOSED_PANEL)
    setPanelArtifact(null)

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
      try {
        const loaded = await loadSession(resolvedKb, id)
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
                sessionId: id,
              },
            })
          }
        }
        setMsgs(restored)
        // msgs now correspond to this id — subsequent effect runs for the same
        // id (and runTurn) treat it as already-loaded and won't reload over it.
        msgsSessionIdRef.current = id
      } catch (e) {
        if (!cancelled) toast.error(t("chat:errors.loadSession", { error: errMsg(e) }))
      }
    }
    void restore()
    return () => { cancelled = true }
    // Keyed on `id` only: KB, location.state, and t are read as "latest at run
    // time"; listing them would re-restore on unrelated changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  // Auto-scroll to the newest content.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" })
  }, [msgs])

  // Abort the in-flight turn's fetch when the user navigates away (real
  // unmount), so a half-finished stream stops burning tokens and can't fire
  // setMsgs after the component is gone. A settled turn already cleared the ref,
  // so this is a no-op unless a stream is actually live.
  //
  // The abort is DEFERRED one macrotask: React 18 StrictMode runs this cleanup
  // and then SYNCHRONOUSLY re-mounts the component in dev, so the re-mount's
  // setup below cancels the pending timer before it fires — otherwise the dev
  // double-invoke would abort the Home-seeded first turn, and startedRef (a ref,
  // it persists) would block its retry, making a new chat look broken in dev. A
  // real navigation-away has no such re-mount, so the timer fires and aborts —
  // no uncancelled-stream leak.
  useEffect(() => {
    // Re-mounted (StrictMode dev, or any future remount): we're still here, so
    // cancel a pending unmount-abort scheduled by the just-run cleanup.
    if (pendingUnmountAbort.current) {
      clearTimeout(pendingUnmountAbort.current)
      pendingUnmountAbort.current = null
    }
    return () => {
      pendingUnmountAbort.current = setTimeout(() => abortRef.current?.abort(), 0)
    }
  }, [])

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

  // Stop the in-flight turn — aborts the SSE fetch; the turn's catch treats the
  // abort as a clean stop (no error toast). Wired for chat/query AND deck/skill
  // (all set abortRef). The `/visualize` graph turn has no controller, so the
  // Stop button is gated on `stoppable` (below) and never shown for it — this is
  // only ever called when a live controller exists.
  const stopTurn = () => abortRef.current?.abort()

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
          {/* 停止按钮：仅当当前回合可中止（存在实时 AbortController）时显示，避免
              对 /visualize 图谱回合成为可见的空操作。中止当前回合的 SSE 流。 */}
          {running && stoppable && (
            <div className="flex justify-center pb-2">
              <button
                type="button"
                onClick={stopTurn}
                className="inline-flex items-center gap-1.5 h-8 px-3.5 rounded-full border border-[hsl(var(--glass-border))] glass-2 text-[12.5px] font-medium text-muted-foreground shadow-sm transition duration-fast ease-out-apple hover:text-foreground hover:border-accent-brand/40 active:scale-[0.97]"
              >
                <CircleStop className="w-3.5 h-3.5" />
                {t("chat:input.stop")}
              </button>
            </div>
          )}
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
