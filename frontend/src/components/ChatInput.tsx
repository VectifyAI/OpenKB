import { useEffect, useMemo, useRef, useState } from "react"
import { useTranslation, Trans } from "react-i18next"
import {
  ArrowUp, BookOpen, ChevronDown, X,
  Presentation, Sparkles, Waypoints,
  type LucideIcon,
} from "lucide-react"
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { listKbs, type KbSummary } from "@/api/kb"
import { cn } from "@/lib/utils"

export interface SlashCommand {
  id: string
  cmd: string
  icon: LucideIcon
}

/**
 * Slash commands for generators. Plain text (no command) is a grounded chat turn
 * that answers questions from the wiki — asking IS the default, so there is no
 * separate `/ask` command. `/html` is intentionally absent. KB-lifecycle actions
 * (recompile / add documents) deliberately live on the KB's own surface (the
 * settings gear + Documents pane), NOT here — chat is for Q&A and generation.
 *
 * `id`/`cmd`/`icon` are code; user-facing `label`/`desc` are looked up at render
 * time via `t(\`commands.${id}.label|desc\`)` (i18n `chat` namespace).
 */
export const slashCommands: SlashCommand[] = [
  { id: "deck", cmd: "/deck", icon: Presentation },
  { id: "skill", cmd: "/skill", icon: Sparkles },
  { id: "visualize", cmd: "/visualize", icon: Waypoints },
]

/** Decorative accent colors, cycled by position — the API carries no color. */
const DOTS = ["bg-blue-500", "bg-emerald-500", "bg-amber-500", "bg-violet-500", "bg-rose-500"]
const dotFor = (i: number) => DOTS[i % DOTS.length]

interface Props {
  /** Selected KB *name* (the real KB identifier; there is no "auto" scope). */
  kbId: string
  onKbChange?: (id: string) => void
  onSend: (text: string, command: SlashCommand | null) => void
  autoFocus?: boolean
  placeholder?: string
  disabled?: boolean
}

export default function ChatInput({
  kbId, onKbChange, onSend, autoFocus, placeholder, disabled,
}: Props) {
  const { t } = useTranslation("chat")
  const [kbs, setKbs] = useState<KbSummary[]>([])
  const [value, setValue] = useState("")
  const [cmd, setCmd] = useState<SlashCommand | null>(null)
  const [menuOpen, setMenuOpen] = useState(false)
  const [idx, setIdx] = useState(0)
  const taRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    listKbs().then((r) => setKbs(r.knowledge_bases)).catch(() => setKbs([]))
  }, [])

  const query = !cmd && value.startsWith("/") ? value.slice(1) : null
  const filtered = useMemo(() => {
    if (query === null) return []
    const q = query.toLowerCase()
    return slashCommands.filter(
      (c) => c.cmd.slice(1).startsWith(q) || t(`commands.${c.id}.label`).includes(query),
    )
  }, [query, t])

  useEffect(() => {
    setMenuOpen(query !== null && filtered.length > 0)
    setIdx(0)
  }, [query, filtered.length])

  useEffect(() => {
    if (autoFocus) taRef.current?.focus()
  }, [autoFocus])

  const pick = (c: SlashCommand) => {
    setCmd(c)
    setValue("")
    setMenuOpen(false)
    taRef.current?.focus()
  }

  const send = () => {
    if (disabled) return
    if (!value.trim() && !cmd) return
    onSend(value.trim(), cmd)
    setValue("")
    setCmd(null)
    taRef.current?.focus()
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (menuOpen) {
      if (e.key === "ArrowDown") { e.preventDefault(); setIdx((i) => (i + 1) % filtered.length); return }
      if (e.key === "ArrowUp") { e.preventDefault(); setIdx((i) => (i - 1 + filtered.length) % filtered.length); return }
      if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); pick(filtered[idx]); return }
      if (e.key === "Escape") { setMenuOpen(false); return }
    }
    if (e.key === "Backspace" && value === "" && cmd) { setCmd(null); return }
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send() }
  }

  const currentIndex = kbs.findIndex((k) => k.name === kbId)
  const currentKb = currentIndex >= 0 ? kbs[currentIndex] : null

  return (
    <div className="relative">
      {/* 斜杠命令面板 */}
      {menuOpen && (
        <div className="absolute bottom-full left-0 right-0 mb-2 rounded-apple-lg glass overflow-hidden z-20 anim-fade-up">
          <div className="px-3 pt-2.5 pb-1 text-[11px] font-semibold text-muted-foreground tracking-wide">{t("commands.paletteHeading")}</div>
          {filtered.map((c, i) => (
            <button
              key={c.id}
              onMouseDown={(e) => { e.preventDefault(); pick(c) }}
              onMouseEnter={() => setIdx(i)}
              className={cn(
                "w-full flex items-center gap-3 px-3 py-2.5 text-left transition-colors",
                i === idx ? "bg-accent-brand/10" : "",
              )}
            >
              <span className={cn("w-8 h-8 rounded-lg grid place-items-center shrink-0", i === idx ? "bg-accent-brand text-white" : "bg-muted text-muted-foreground")}>
                <c.icon className="w-4 h-4" />
              </span>
              <span className="min-w-0">
                <span className="flex items-baseline gap-2">
                  <span className="font-mono2 text-[13px] font-medium text-accent-brand">{c.cmd}</span>
                  <span className="text-[13px] font-medium text-foreground">{t(`commands.${c.id}.label`)}</span>
                </span>
                <span className="block text-[12px] text-muted-foreground truncate">{t(`commands.${c.id}.desc`)}</span>
              </span>
              {i === idx && <kbd className="ml-auto text-[10px] text-muted-foreground border border-[hsl(var(--glass-border))] rounded px-1 py-0.5">Tab</kbd>}
            </button>
          ))}
        </div>
      )}

      {/* 输入框 */}
      <div className="rounded-apple-lg border border-[hsl(var(--glass-border))] glass-2 shadow-sm transition-shadow focus-within:shadow-md focus-within:border-accent-brand/40">
        {/* 命令 chip */}
        {cmd && (
          <div className="px-3 pt-3">
            <span className="inline-flex items-center gap-1.5 rounded-lg bg-accent-brand/10 border border-accent-brand/20 pl-2 pr-1 py-1">
              <cmd.icon className="w-3.5 h-3.5 text-accent-brand" />
              <span className="font-mono2 text-[12px] font-medium text-accent-brand">{cmd.cmd}</span>
              <span className="text-[12px] text-accent-brand">{t(`commands.${cmd.id}.label`)}</span>
              <button onClick={() => setCmd(null)} className="ml-0.5 rounded p-0.5 hover:bg-accent-brand/15 text-accent-brand/70">
                <X className="w-3 h-3" />
              </button>
            </span>
          </div>
        )}
        <textarea
          ref={taRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKeyDown}
          disabled={disabled}
          rows={2}
          placeholder={placeholder ?? (cmd ? t("input.cmdPlaceholder", { label: t(`commands.${cmd.id}.label`) }) : t("input.defaultPlaceholder"))}
          className="w-full resize-none bg-transparent px-4 pt-3 pb-1 text-[15px] leading-relaxed outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed"
        />
        <div className="flex items-center gap-2 px-3 pb-3">
          {/* 作用域选择：必须选择一个真实知识库（无「自动选择」） */}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button className="flex items-center gap-1.5 h-7 px-2 rounded-apple-sm text-[12px] font-medium text-muted-foreground hover:bg-accent hover:text-foreground transition-colors">
                <BookOpen className="w-3.5 h-3.5" />
                {currentKb?.name || kbId || t("input.selectKb")}
                <ChevronDown className="w-3 h-3 opacity-60" />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="w-52">
              {kbs.length === 0 && (
                <DropdownMenuItem disabled className="text-[13px] text-muted-foreground">
                  {t("input.noKbs")}
                </DropdownMenuItem>
              )}
              {kbs.map((k, i) => (
                <DropdownMenuItem key={k.name} onClick={() => onKbChange?.(k.name)} className="text-[13px]">
                  <span className={cn("w-2 h-2 rounded-full mr-1", dotFor(i))} />
                  {k.name}
                  {k.name === kbId && <span className="ml-auto text-accent-brand text-[11px]">{t("input.current")}</span>}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>

          <div className="flex-1" />
          <span className="text-[11px] text-muted-foreground/70 hidden md:block">
            <Trans t={t} i18nKey="input.hint" components={[<span className="font-mono2 text-accent-brand" />]} />
          </span>
          <button
            onClick={send}
            disabled={disabled || (!value.trim() && !cmd)}
            className={cn(
              "w-8 h-8 rounded-full grid place-items-center transition-all",
              !disabled && (value.trim() || cmd) ? "bg-accent-brand text-white hover:opacity-90 shadow-sm" : "bg-muted text-muted-foreground",
            )}
          >
            <ArrowUp className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  )
}
