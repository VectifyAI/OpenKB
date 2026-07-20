import { useCallback, useEffect, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import type { TFunction } from "i18next"
import { motion, useReducedMotion } from "motion/react"
import { Presentation, Waypoints, FileCode, ExternalLink, Download, X, Loader2 } from "lucide-react"
import { toast } from "sonner"
import { getDeckBlobUrl, getGraphBlobUrl, getOutputBlobUrl } from "@/api/artifacts"
import type { Artifact } from "@/components/ArtifactCard"

/** Stable identity for a viewable artifact (graph is one-per-kb; deck by name). */
export function artifactKey(a: Artifact): string {
  if (a.type === "graph") return `graph:${a.kb}`
  if (a.type === "file") return `file:${a.kb}/${a.path}`
  return `${a.type}:${a.kb}/${a.name}`
}

/** Human label for the header + switcher pill. */
function artifactLabel(a: Artifact, t: TFunction): string {
  if (a.type === "graph") return t("artifacts:graph.label")
  return a.name
}

/** Fetch the artifact's self-contained HTML as an authenticated blob: URL. */
function fetchArtifactHtml(a: Artifact, t: TFunction): Promise<string> {
  if (a.type === "deck") return getDeckBlobUrl(a.kb, a.name)
  if (a.type === "graph") return getGraphBlobUrl(a.kb)
  if (a.type === "file") return getOutputBlobUrl(a.kb, a.path)
  // Skills are archives, not viewable HTML — they never reach the panel.
  return Promise.reject(new Error(t("artifacts:previewUnsupported")))
}

const MIN_W = 380
const MAX_FRAC = 0.65
const DEFAULT_W = 520
const WIDTH_KEY = "openkb.artifactPanelWidth"

/** Clamp a width to [MIN_W, 65% of the viewport]. */
function clampWidth(w: number): number {
  const maxW = Math.max(MIN_W, window.innerWidth * MAX_FRAC)
  return Math.min(Math.max(w, MIN_W), maxW)
}

function loadWidth(): number {
  const raw = Number(localStorage.getItem(WIDTH_KEY))
  return clampWidth(Number.isFinite(raw) && raw >= MIN_W ? raw : DEFAULT_W)
}

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e))

/**
 * Claude-style docked artifact panel: a non-modal, resizable right column that
 * renders the active artifact's self-contained HTML in a sandboxed iframe. The
 * panel enter/exit springs (via AnimatePresence in the parent); switching the
 * active artifact is an INSTANT content swap (the component instance persists,
 * so no re-animation — only the iframe blob reloads).
 */
export default function ArtifactPanel({
  artifacts,
  active,
  onSwitch,
  onClose,
}: {
  artifacts: Artifact[]
  active: Artifact
  onSwitch: (a: Artifact) => void
  onClose: () => void
}) {
  const { t } = useTranslation(["artifacts", "common"])
  const reduce = useReducedMotion()
  const [width, setWidth] = useState<number>(loadWidth)
  const [blobUrl, setBlobUrl] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const blobRef = useRef<string | null>(null)
  const panelRef = useRef<HTMLElement | null>(null)
  const activeKey = artifactKey(active)

  // Non-modal landmark: move focus to the panel once when it opens so screen
  // readers announce the labeled region. NOT a trap — the panel deliberately
  // coexists with the chat, so Tab still flows out to the conversation. Runs
  // once per open (the instance persists across artifact switches).
  useEffect(() => {
    panelRef.current?.focus({ preventScroll: true })
  }, [])

  // Persist the chosen width across reloads.
  useEffect(() => {
    localStorage.setItem(WIDTH_KEY, String(Math.round(width)))
  }, [width])

  // Re-clamp when the viewport shrinks so a persisted wide panel never exceeds
  // the MAX_FRAC cap on a smaller window.
  useEffect(() => {
    const onResize = () => setWidth((w) => clampWidth(w))
    window.addEventListener("resize", onResize)
    return () => window.removeEventListener("resize", onResize)
  }, [])

  // (Re)load the active artifact's HTML blob whenever the active identity
  // changes; revoke the blob it replaces.
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setBlobUrl(null)
    fetchArtifactHtml(active, t)
      .then((url) => {
        if (cancelled) {
          URL.revokeObjectURL(url)
          return
        }
        if (blobRef.current) URL.revokeObjectURL(blobRef.current)
        blobRef.current = url
        setBlobUrl(url)
      })
      .catch((e) => {
        if (!cancelled) toast.error(t("artifacts:loadError", { error: errMsg(e) }))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeKey])

  // Revoke the last blob on unmount.
  useEffect(
    () => () => {
      if (blobRef.current) URL.revokeObjectURL(blobRef.current)
    },
    [],
  )

  // A ref to the current drag's listener-teardown, so an unmount mid-drag can
  // tear it down (pointerup alone would otherwise be the only remover).
  const resizeCleanup = useRef<(() => void) | null>(null)

  // Resize by dragging the left edge (1:1 pointer tracking; dragging left grows).
  const startResize = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault()
      const startX = e.clientX
      const startW = width
      const onMove = (ev: PointerEvent) => {
        // Recompute the cap each move so a mid-drag window resize is honored.
        const maxW = Math.max(MIN_W, window.innerWidth * MAX_FRAC)
        setWidth(Math.min(maxW, Math.max(MIN_W, startW + (startX - ev.clientX))))
      }
      const cleanup = () => {
        window.removeEventListener("pointermove", onMove)
        window.removeEventListener("pointerup", cleanup)
        document.body.style.userSelect = ""
        resizeCleanup.current = null
      }
      resizeCleanup.current = cleanup
      document.body.style.userSelect = "none"
      window.addEventListener("pointermove", onMove)
      window.addEventListener("pointerup", cleanup)
    },
    [width],
  )

  // Tear down an in-flight resize drag if the panel unmounts mid-drag.
  useEffect(() => () => resizeCleanup.current?.(), [])

  // Open the active artifact full-screen in a new tab. Open the tab SYNCHRONOUSLY
  // (inside the click gesture) to dodge popup blockers, then set its location once
  // the authenticated blob resolves.
  const openInNewTab = () => {
    const w = window.open("", "_blank")
    fetchArtifactHtml(active, t)
      .then((url) => {
        if (w) w.location.href = url
        // The tab owns the URL now; revoke generously once it has loaded.
        window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
      })
      .catch((e) => {
        if (w) w.close()
        toast.error(t("artifacts:openError", { error: errMsg(e) }))
      })
  }

  const download = () => {
    fetchArtifactHtml(active, t)
      .then((url) => {
        const a = document.createElement("a")
        a.href = url
        a.download =
          active.type === "graph"
            ? `${active.kb}-graph.html`
            : active.type === "file"
              ? active.name
              : `${active.name}.html`
        document.body.appendChild(a)
        a.click()
        a.remove()
        window.setTimeout(() => URL.revokeObjectURL(url), 10_000)
      })
      .catch((e) => toast.error(t("artifacts:downloadError", { error: errMsg(e) })))
  }

  const HeaderIcon =
    active.type === "graph" ? Waypoints : active.type === "file" ? FileCode : Presentation

  return (
    <motion.aside
      ref={panelRef}
      role="complementary"
      aria-label={artifactLabel(active, t)}
      tabIndex={-1}
      className="shrink-0 relative flex flex-col glass border-l border-[hsl(var(--glass-border))] shadow-glass outline-none"
      style={{ width }}
      initial={reduce ? { opacity: 0 } : { opacity: 0, x: 24, scale: 0.98 }}
      animate={reduce ? { opacity: 1 } : { opacity: 1, x: 0, scale: 1 }}
      exit={reduce ? { opacity: 0 } : { opacity: 0, x: 24, scale: 0.98 }}
      transition={reduce ? { duration: 0.12 } : { type: "spring", bounce: 0, duration: 0.3 }}
    >
      {/* left-edge resize handle */}
      <div
        onPointerDown={startResize}
        title={t("artifacts:panel.resize")}
        className="absolute left-0 top-0 z-10 h-full w-1.5 -translate-x-1/2 cursor-col-resize hover:bg-accent-brand/30"
      />

      {/* header */}
      <div className="shrink-0 h-12 flex items-center gap-2 px-3 border-b border-[hsl(var(--glass-border))]">
        <HeaderIcon className="w-4 h-4 text-accent-brand shrink-0" />
        <span className="min-w-0 truncate text-[13px] font-semibold text-foreground">
          {artifactLabel(active, t)}
        </span>
        <span className="shrink-0 text-[11px] uppercase tracking-wide text-muted-foreground">
          {active.type === "graph" ? "GRAPH" : active.type === "file" ? "FILE" : "DECK"}
        </span>
        <div className="ml-auto flex shrink-0 items-center gap-1">
          <button
            onClick={openInNewTab}
            title={t("artifacts:panel.openTab")}
            aria-label={t("artifacts:panel.openTab")}
            className="grid h-7 w-7 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          >
            <ExternalLink className="w-4 h-4" />
          </button>
          <button
            onClick={download}
            title={t("artifacts:panel.download")}
            aria-label={t("artifacts:panel.download")}
            className="grid h-7 w-7 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          >
            <Download className="w-4 h-4" />
          </button>
          <button
            onClick={onClose}
            title={t("common:actions.close")}
            aria-label={t("common:actions.close")}
            className="grid h-7 w-7 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* switcher — only when this session produced more than one viewable artifact */}
      {artifacts.length > 1 && (
        <div className="shrink-0 flex items-center gap-1.5 overflow-x-auto border-b border-[hsl(var(--glass-border))] px-3 py-2">
          {artifacts.map((a) => {
            const k = artifactKey(a)
            const on = k === activeKey
            return (
              <button
                key={k}
                onClick={() => onSwitch(a)}
                className={
                  "inline-flex h-7 shrink-0 items-center gap-1.5 rounded-full px-2.5 text-[11.5px] font-medium transition-colors " +
                  (on
                    ? "bg-accent-brand text-white"
                    : "glass-2 border border-[hsl(var(--glass-border))] text-muted-foreground hover:text-foreground")
                }
              >
                {a.type === "graph" ? (
                  <Waypoints className="w-3 h-3" />
                ) : a.type === "file" ? (
                  <FileCode className="w-3 h-3" />
                ) : (
                  <Presentation className="w-3 h-3" />
                )}
                <span className="max-w-[120px] truncate">{artifactLabel(a, t)}</span>
              </button>
            )
          })}
        </div>
      )}

      {/* body — sandboxed iframe of the artifact HTML (blob src, token-auth'd) */}
      <div className="relative flex-1 min-h-0 bg-white">
        {loading && (
          <div className="absolute inset-0 grid place-items-center bg-background/60 text-muted-foreground">
            <Loader2 className="w-5 h-5 animate-spin" />
          </div>
        )}
        {blobUrl && (
          <iframe
            title={`artifact-${activeKey}`}
            src={blobUrl}
            sandbox="allow-scripts"
            className="h-full w-full border-0 bg-white"
          />
        )}
      </div>
    </motion.aside>
  )
}
