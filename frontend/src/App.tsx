import { useEffect, useState } from "react"
import { Routes, Route, useParams } from "react-router"
import { MotionConfig } from "motion/react"
import { KeyRound, Loader2 } from "lucide-react"
import AppSidebar from "@/components/AppSidebar"
import TitleBar from "@/components/TitleBar"
import { ThemeToggle } from "@/lib/theme"
import { LanguageToggle } from "@/lib/language"
import { useTranslation } from "react-i18next"
import { cn } from "@/lib/utils"
import { Toaster } from "@/components/ui/sonner"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { getApiBase, getToken, onUnauthorized, setConnection } from "@/api/client"
import Home from "@/pages/Home"
import ChatSession from "@/pages/ChatSession"
import KbList from "@/pages/KbList"
import KbDetail from "@/pages/KbDetail"
import Settings from "@/pages/Settings"

/** Remount KbDetail per KB so its page/tree state resets cleanly on nav. */
function KbDetailRoute() {
  const { id = "" } = useParams()
  return <KbDetail key={id} />
}

/** Remount ChatSession per session id so its restore effect (which guards on a
 *  module-instance restoredRef and only depends on [id]) re-runs on a genuine
 *  navigation between sessions — otherwise back/forward or a hash edit between
 *  /chat/A and /chat/B changes the id without a remount and shows the stale
 *  session. */
function ChatSessionRoute() {
  const { id = "" } = useParams()
  return <ChatSession key={id} />
}

const inputCls =
  "mt-1.5 w-full h-9 rounded-md border border-input bg-transparent px-3 text-[13px] font-mono2 outline-none focus-visible:ring-2 focus-visible:ring-ring focus:border-accent-brand"

/**
 * Reactive connection prompt. Opened only when a request 401s — i.e. the server
 * has a bearer token configured and this client isn't sending the right one.
 * Mirrors the opt-in auth model: local-first (no token) works with no prompt;
 * we ask for a token only when the server actually demands one.
 */
function ConnectionDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
}) {
  const { t } = useTranslation("common")
  const [base, setBase] = useState("")
  const [token, setToken] = useState("")
  const [submitting, setSubmitting] = useState(false)

  // Prefill from the stored connection each time the dialog opens.
  useEffect(() => {
    if (open) {
      setBase(getApiBase())
      setToken(getToken())
      setSubmitting(false)
    }
  }, [open])

  const submit = () => {
    setSubmitting(true)
    setConnection(base.trim(), token.trim())
    // Full reload re-runs every page's fetches with the new credentials. The
    // HashRouter route lives in the URL fragment, so the current view is kept.
    window.location.reload()
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <KeyRound className="w-4 h-4 text-accent-brand" />
            {t("auth.title")}
          </DialogTitle>
          <DialogDescription>{t("auth.description")}</DialogDescription>
        </DialogHeader>

        <form
          onSubmit={(e) => {
            e.preventDefault()
            submit()
          }}
          className="space-y-3"
        >
          <div>
            <label className="text-[12px] font-medium text-muted-foreground">
              {t("auth.tokenLabel")}
            </label>
            <input
              autoFocus
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="Bearer token"
              className={inputCls}
            />
          </div>
          <div>
            <label className="text-[12px] font-medium text-muted-foreground">
              {t("auth.baseLabel")}
            </label>
            <input
              value={base}
              onChange={(e) => setBase(e.target.value)}
              placeholder="http://127.0.0.1:8000"
              className={inputCls}
            />
          </div>

          <DialogFooter>
            <Button type="submit" disabled={submitting}>
              {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
              {t("auth.connect")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

export default function App() {
  const [authOpen, setAuthOpen] = useState(false)

  const isDesktopShell =
    typeof (window as { __OPENKB_DESKTOP__?: unknown }).__OPENKB_DESKTOP__ !== "undefined"

  // Register the reactive 401 handler once. Any request that 401s opens the
  // connection prompt (idempotent — repeated 401s just keep it open).
  useEffect(() => {
    onUnauthorized(() => setAuthOpen(true))
  }, [])

  return (
    <MotionConfig reducedMotion="user">
      <div className="ambient-ground h-screen w-screen flex overflow-hidden">
        {isDesktopShell && (
          <div className="absolute top-0 inset-x-0 z-50">
            <TitleBar />
          </div>
        )}
        <div className="flex flex-1 min-h-0 w-full">
          <AppSidebar />
          <main className="relative flex-1 min-w-0 overflow-hidden">
            {/* Global floating chrome cluster: overlays content on every route
                (not the sidebar). It owns a reserved top-right "chrome lane"
                (~112px from main's right edge) sized for the theme toggle NOW
                plus Sub-project H's future i18n switcher + gaps. Page-level
                right-anchored controls reserve that lane with `pr-28` so they
                always clear the pill — see KbList's header row and KbDetail's
                gear row. Clears the desktop TitleBar via a top offset. */}
            <div
              className={cn(
                "absolute right-3 z-40 flex items-center gap-1 rounded-full glass px-1 py-1",
                isDesktopShell ? "top-10" : "top-2.5",
              )}
            >
              <ThemeToggle className="text-muted-foreground hover:text-foreground transition-colors" />
              <LanguageToggle className="text-muted-foreground hover:text-foreground transition-colors" />
            </div>
            <Routes>
              <Route path="/" element={<Home />} />
              <Route path="/chat/:id" element={<ChatSessionRoute />} />
              <Route path="/kb" element={<KbList />} />
              <Route path="/kb/:id" element={<KbDetailRoute />} />
              <Route path="/settings" element={<Settings />} />
            </Routes>
          </main>
        </div>
        <ConnectionDialog open={authOpen} onOpenChange={setAuthOpen} />
        <Toaster />
      </div>
    </MotionConfig>
  )
}
