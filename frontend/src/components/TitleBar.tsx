import { useLocation } from "react-router"
import { useTranslation } from "react-i18next"
import { HardDrive, Cloud } from "lucide-react"
import { getApiBase } from "@/api/client"

/**
 * True only when a real desktop shell (Sub-project C: Tauri/Electron) has
 * injected `window.__OPENKB_DESKTOP__`. That shell has no OS chrome of its own,
 * so it needs the fake traffic-lights + a draggable region. In a browser tab
 * (today's only delivery) this is always false and we render a plain header —
 * no window buttons, no drag region.
 */
const isDesktopShell =
  typeof (window as { __OPENKB_DESKTOP__?: unknown }).__OPENKB_DESKTOP__ !== "undefined"

function useTitle(): string {
  const { pathname } = useLocation()
  const { t } = useTranslation("common")
  const titles: Record<string, string> = {
    "/": t("nav.home"),
    "/kb": t("nav.kbs"),
    "/settings": t("nav.settings"),
  }
  const known = titles[pathname]
  if (known) return known
  if (pathname.startsWith("/chat/")) return t("titlebar.chat")
  if (pathname.startsWith("/kb/")) {
    // The route id IS the KB name (encodeURIComponent on the way in).
    const id = pathname.split("/")[2]
    return id ? decodeURIComponent(id) : t("nav.kbs")
  }
  return ""
}

/** Honest connection indicator — local (same-origin) vs. a configured remote host. */
function ConnectionStatus() {
  const { t } = useTranslation("common")
  const base = getApiBase()
  if (!base) {
    return (
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <HardDrive className="w-3.5 h-3.5 text-emerald-600" />
        <span className="hidden sm:inline">{t("titlebar.local")}</span>
      </div>
    )
  }
  let host = base
  try {
    host = new URL(base).host
  } catch {
    // keep the raw base if it isn't a full URL
  }
  return (
    <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
      <Cloud className="w-3.5 h-3.5 text-accent-brand" />
      <span className="hidden sm:inline font-mono2">{host}</span>
    </div>
  )
}

export default function TitleBar() {
  const title = useTitle()
  const heading = `OpenKB Studio${title ? ` — ${title}` : ""}`

  // Desktop shell: fake traffic-lights + a draggable title region.
  if (isDesktopShell) {
    return (
      <div
        className="h-11 shrink-0 flex items-center px-4 select-none"
        style={{ WebkitAppRegion: "drag" } as React.CSSProperties}
      >
        <div className="flex items-center gap-2">
          <span className="w-3 h-3 rounded-full bg-[#ff5f57] border border-[#e0443e]" />
          <span className="w-3 h-3 rounded-full bg-[#febc2e] border border-[#d89e24]" />
          <span className="w-3 h-3 rounded-full bg-[#28c840] border border-[#1dad2b]" />
        </div>
        <div className="flex-1 text-center text-[13px] font-medium text-muted-foreground -ml-14">
          {heading}
        </div>
        <ConnectionStatus />
      </div>
    )
  }

  // Browser tab (today's only delivery): no title strip at all — the browser
  // tab already shows the app title, so an in-page "OpenKB Studio — <route>"
  // bar is dead chrome. The desktop-shell branch above is kept for Sub-project C.
  return null
}
