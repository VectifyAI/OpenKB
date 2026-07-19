import { useEffect, useState } from "react"
import { Routes, Route, useParams } from "react-router"
import { KeyRound, Loader2 } from "lucide-react"
import AppSidebar from "@/components/AppSidebar"
import TitleBar from "@/components/TitleBar"
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
            需要访问令牌
          </DialogTitle>
          <DialogDescription>
            此服务器已配置访问令牌（OPENKB_API_TOKEN）。请输入令牌以继续；令牌仅保存在本浏览器中。
          </DialogDescription>
        </DialogHeader>

        <form
          onSubmit={(e) => {
            e.preventDefault()
            submit()
          }}
          className="space-y-3"
        >
          <div>
            <label className="text-[12px] font-medium text-muted-foreground">访问令牌</label>
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
              API 地址（可选，留空使用当前源）
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
              连接
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
    <div className="ambient-ground h-screen w-screen flex overflow-hidden">
      {isDesktopShell && (
        <div className="absolute top-0 inset-x-0 z-50">
          <TitleBar />
        </div>
      )}
      <div className="flex flex-1 min-h-0 w-full">
        <AppSidebar />
        <main className="relative flex-1 min-w-0 overflow-hidden">
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/chat/:id" element={<ChatSession />} />
            <Route path="/kb" element={<KbList />} />
            <Route path="/kb/:id" element={<KbDetailRoute />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </main>
      </div>
      <ConnectionDialog open={authOpen} onOpenChange={setAuthOpen} />
      <Toaster />
    </div>
  )
}
