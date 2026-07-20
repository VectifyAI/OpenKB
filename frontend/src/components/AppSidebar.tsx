import { useEffect, useState } from "react"
import { NavLink, useNavigate } from "react-router"
import { useTranslation } from "react-i18next"
import { MessageSquare, Library, Settings2, Plus } from "lucide-react"
import { listKbs, type KbSummary } from "@/api/kb"
import { cn } from "@/lib/utils"

/** Decorative accent colors, cycled by position — the API carries no color. */
const DOTS = [
  "bg-blue-500",
  "bg-emerald-500",
  "bg-amber-500",
  "bg-violet-500",
  "bg-rose-500",
]
const dotFor = (i: number) => DOTS[i % DOTS.length]

function NavItem({
  to,
  icon,
  label,
  end,
}: {
  to: string
  icon: React.ReactNode
  label: string
  end?: boolean
}) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        cn(
          "flex items-center gap-2.5 px-3 h-9 rounded-apple-sm text-[14px] font-medium transition-colors duration-fast ease-out-apple active:scale-[0.98]",
          isActive
            ? "bg-accent text-accent-foreground shadow-sm"
            : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
        )
      }
    >
      {icon}
      {label}
    </NavLink>
  )
}

export default function AppSidebar() {
  const navigate = useNavigate()
  const { t } = useTranslation("common")
  const [kbs, setKbs] = useState<KbSummary[]>([])

  useEffect(() => {
    listKbs()
      .then((r) => setKbs(r.knowledge_bases))
      .catch(() => setKbs([]))
  }, [])

  return (
    <aside className="glass m-2 mr-0 w-[236px] shrink-0 flex flex-col rounded-apple-lg px-3 pb-3 pt-2">
      {/* 品牌 */}
      <div className="flex items-center gap-2 px-2 h-10 mb-1">
        <div className="w-6 h-6 rounded-apple-sm bg-accent-brand text-white grid place-items-center text-[13px] font-extrabold tracking-tighter">
          K
        </div>
        <div className="text-[15px] font-bold tracking-tight">OpenKB Studio</div>
      </div>

      {/* 主导航（不含设置，设置已下沉到底部） */}
      <nav className="space-y-0.5">
        <NavItem to="/" end icon={<MessageSquare className="w-4 h-4" />} label={t("nav.home")} />
        <NavItem to="/kb" icon={<Library className="w-4 h-4" />} label={t("nav.kbs")} />
      </nav>

      {/* 知识库列表 */}
      <div className="mt-5 px-3 flex items-center justify-between">
        <span className="text-[12px] font-semibold text-muted-foreground tracking-wide">
          {t("nav.kbs")}
        </span>
        <button
          className="text-muted-foreground hover:text-foreground transition-colors"
          title={t("actions.newKb")}
        >
          <Plus className="w-3.5 h-3.5" />
        </button>
      </div>
      <div className="mt-1 space-y-0.5 overflow-y-auto">
        {kbs.map((kb, i) => (
          <button
            key={kb.name}
            onClick={() => navigate(`/kb/${encodeURIComponent(kb.name)}`)}
            className="w-full flex items-center gap-2.5 px-3 h-9 rounded-apple-sm text-[13.5px] text-muted-foreground hover:bg-accent/60 hover:text-foreground transition-colors duration-fast ease-out-apple active:scale-[0.98]"
          >
            <span className={cn("w-2 h-2 rounded-full shrink-0", dotFor(i))} />
            <span className="truncate">{kb.name}</span>
            <span className="ml-auto text-[11px] text-muted-foreground font-mono2 tabular-nums">
              {kb.document_count}
            </span>
          </button>
        ))}
      </div>

      <div className="flex-1" />

      {/* 底部：设置（主题切换已上移到全局右上角浮层） */}
      <div className="space-y-1.5">
        <NavItem to="/settings" icon={<Settings2 className="w-4 h-4" />} label={t("nav.settings")} />
      </div>
    </aside>
  )
}
