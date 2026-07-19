import { useCallback, useEffect, useRef, useState } from "react"
import { motion, useReducedMotion } from "motion/react"
import { Presentation, Waypoints, ExternalLink, Download, X, Loader2 } from "lucide-react"
import { toast } from "sonner"
import { getDeckBlobUrl, getGraphBlobUrl } from "@/api/artifacts"
import type { Artifact } from "@/components/ArtifactCard"

/** Stable identity for a viewable artifact (graph is one-per-kb; deck by name). */
export function artifactKey(a: Artifact): string {
  return a.type === "graph" ? `graph:${a.kb}` : `${a.type}:${a.kb}/${a.name}`
}

/** Human label for the header + switcher pill. */
function artifactLabel(a: Artifact): string {
  return a.type === "graph" ? "知识图谱" : a.name
}

/** Fetch the artifact's self-contained HTML as an authenticated blob: URL. */
function fetchArtifactHtml(a: Artifact): Promise<string> {
  if (a.type === "deck") return getDeckBlobUrl(a.kb, a.name)
  if (a.type === "graph") return getGraphBlobUrl(a.kb)
  // Skills are archives, not viewable HTML — they never reach the panel.
  return Promise.reject(new Error("该产物类型不可预览"))
}

const MIN_W = 380
const MAX_FRAC = 0.65
const DEFAULT_W = 520
const WIDTH_KEY = "openkb.artifactPanelWidth"

function loadWidth(): number {
  const raw = Number(localStorage.getItem(WIDTH_KEY))
  return Number.isFinite(raw) && raw >= MIN_W ? raw : DEFAULT_W
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
  const reduce = useReducedMotion()
  const [width, setWidth] = useState<number>(loadWidth)
  const [blobUrl, setBlobUrl] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const blobRef = useRef<string | null>(null)
  const activeKey = artifactKey(active)

  // Persist the chosen width across reloads.
  useEffect(() => {
    localStorage.setItem(WIDTH_KEY, String(Math.round(width)))
  }, [width])

  // (Re)load the active artifact's HTML blob whenever the active identity
  // changes; revoke the blob it replaces.
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setBlobUrl(null)
    fetchArtifactHtml(active)
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
        if (!cancelled) toast.error(`无法加载产物：${errMsg(e)}`)
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

  // Resize by dragging the left edge (1:1 pointer tracking; dragging left grows).
  const startResize = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault()
      const startX = e.clientX
      const startW = width
      const maxW = window.innerWidth * MAX_FRAC
      const onMove = (ev: PointerEvent) => {
        setWidth(Math.min(maxW, Math.max(MIN_W, startW + (startX - ev.clientX))))
      }
      const onUp = () => {
        window.removeEventListener("pointermove", onMove)
        window.removeEventListener("pointerup", onUp)
      }
      window.addEventListener("pointermove", onMove)
      window.addEventListener("pointerup", onUp)
    },
    [width],
  )

  // Open the active artifact full-screen in a new tab. Open the tab SYNCHRONOUSLY
  // (inside the click gesture) to dodge popup blockers, then set its location once
  // the authenticated blob resolves.
  const openInNewTab = () => {
    const w = window.open("", "_blank")
    fetchArtifactHtml(active)
      .then((url) => {
        if (w) w.location.href = url
        // The tab owns the URL now; revoke generously once it has loaded.
        window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
      })
      .catch((e) => {
        if (w) w.close()
        toast.error(`无法打开：${errMsg(e)}`)
      })
  }

  const download = () => {
    fetchArtifactHtml(active)
      .then((url) => {
        const a = document.createElement("a")
        a.href = url
        a.download = active.type === "graph" ? `${active.kb}-graph.html` : `${active.name}.html`
        document.body.appendChild(a)
        a.click()
        a.remove()
        window.setTimeout(() => URL.revokeObjectURL(url), 10_000)
      })
      .catch((e) => toast.error(`下载失败：${errMsg(e)}`))
  }

  const HeaderIcon = active.type === "graph" ? Waypoints : Presentation

  return (
    <motion.aside
      className="shrink-0 relative flex flex-col glass border-l border-[hsl(var(--glass-border))] shadow-glass"
      style={{ width }}
      initial={reduce ? { opacity: 0 } : { opacity: 0, x: 24, scale: 0.98 }}
      animate={reduce ? { opacity: 1 } : { opacity: 1, x: 0, scale: 1 }}
      exit={reduce ? { opacity: 0 } : { opacity: 0, x: 24, scale: 0.98 }}
      transition={reduce ? { duration: 0.12 } : { type: "spring", bounce: 0, duration: 0.3 }}
    >
      {/* left-edge resize handle */}
      <div
        onPointerDown={startResize}
        title="拖动调整宽度"
        className="absolute left-0 top-0 z-10 h-full w-1.5 -translate-x-1/2 cursor-col-resize hover:bg-accent-brand/30"
      />

      {/* header */}
      <div className="shrink-0 h-12 flex items-center gap-2 px-3 border-b border-[hsl(var(--glass-border))]">
        <HeaderIcon className="w-4 h-4 text-accent-brand shrink-0" />
        <span className="min-w-0 truncate text-[13px] font-semibold text-foreground">
          {artifactLabel(active)}
        </span>
        <span className="shrink-0 text-[11px] uppercase tracking-wide text-muted-foreground">
          {active.type === "graph" ? "GRAPH" : "DECK"}
        </span>
        <div className="ml-auto flex shrink-0 items-center gap-1">
          <button
            onClick={openInNewTab}
            title="在新标签页打开"
            className="grid h-7 w-7 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          >
            <ExternalLink className="w-4 h-4" />
          </button>
          <button
            onClick={download}
            title="下载"
            className="grid h-7 w-7 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          >
            <Download className="w-4 h-4" />
          </button>
          <button
            onClick={onClose}
            title="关闭"
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
                ) : (
                  <Presentation className="w-3 h-3" />
                )}
                <span className="max-w-[120px] truncate">{artifactLabel(a)}</span>
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
