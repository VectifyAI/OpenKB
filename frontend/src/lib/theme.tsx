import { createContext, useContext, useEffect, useState, type ReactNode } from "react"
import { Sun, Moon, Monitor } from "lucide-react"

type Theme = "light" | "dark" | "system"
type Resolved = "light" | "dark"

const KEY = "openkb_theme"
const ThemeCtx = createContext<{
  theme: Theme
  resolved: Resolved
  setTheme: (t: Theme) => void
} | null>(null)

function systemDark(): boolean {
  return window.matchMedia("(prefers-color-scheme: dark)").matches
}

/** Apply the resolved theme to <html>: toggle shadcn's `.dark` class AND stamp
 * data-theme so the Apple-token media-query override wins in both directions. */
function apply(theme: Theme): Resolved {
  const resolved: Resolved = theme === "system" ? (systemDark() ? "dark" : "light") : theme
  const root = document.documentElement
  root.classList.toggle("dark", resolved === "dark")
  if (theme === "system") root.removeAttribute("data-theme")
  else root.setAttribute("data-theme", resolved)
  return resolved
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(
    () => (localStorage.getItem(KEY) as Theme) || "system",
  )
  const [resolved, setResolved] = useState<Resolved>(() => apply(theme))

  const setTheme = (t: Theme) => {
    localStorage.setItem(KEY, t)
    setThemeState(t)
    setResolved(apply(t))
  }

  // When on "system", follow OS changes live.
  useEffect(() => {
    if (theme !== "system") return
    const mq = window.matchMedia("(prefers-color-scheme: dark)")
    const onChange = () => setResolved(apply("system"))
    mq.addEventListener("change", onChange)
    return () => mq.removeEventListener("change", onChange)
  }, [theme])

  return <ThemeCtx.Provider value={{ theme, resolved, setTheme }}>{children}</ThemeCtx.Provider>
}

export function useTheme() {
  const ctx = useContext(ThemeCtx)
  if (!ctx) throw new Error("useTheme must be used within ThemeProvider")
  return ctx
}

/** Theme toggle icon button (cycles light → dark → system). */
export function ThemeToggle({ className }: { className?: string }) {
  const { theme, setTheme } = useTheme()
  const next = theme === "light" ? "dark" : theme === "dark" ? "system" : "light"
  const label = theme === "light" ? "浅色" : theme === "dark" ? "深色" : "跟随系统"
  const Icon = theme === "light" ? Sun : theme === "dark" ? Moon : Monitor
  return (
    <button
      onClick={() => setTheme(next)}
      title={`主题：${label}（点击切换）`}
      aria-label={`主题：${label}`}
      className={`grid h-8 w-8 place-items-center rounded-lg ${className ?? ""}`}
    >
      <Icon className="w-4 h-4" />
    </button>
  )
}
