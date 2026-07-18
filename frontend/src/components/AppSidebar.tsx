import { useEffect, useState } from 'react'
import { NavLink, useNavigate } from 'react-router'
import { MessageSquare, Library, Settings2, Plus } from 'lucide-react'
import { listKbs, type KbSummary } from '@/api/kb'
import { cn } from '@/lib/utils'

/** Decorative accent colors, cycled by position — the API carries no color. */
const DOTS = ['bg-blue-500', 'bg-emerald-500', 'bg-amber-500', 'bg-violet-500', 'bg-rose-500']
const dotFor = (i: number) => DOTS[i % DOTS.length]

function NavItem({ to, icon, label, end }: { to: string; icon: React.ReactNode; label: string; end?: boolean }) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        cn(
          'flex items-center gap-2.5 px-3 h-8 rounded-lg text-[13px] font-medium transition-colors',
          isActive ? 'bg-white text-neutral-900 shadow-sm' : 'text-neutral-600 hover:bg-white/60 hover:text-neutral-900',
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
  const [kbs, setKbs] = useState<KbSummary[]>([])

  useEffect(() => {
    listKbs()
      .then(r => setKbs(r.knowledge_bases))
      .catch(() => setKbs([]))
  }, [])

  return (
    <aside className="w-[232px] shrink-0 flex flex-col px-3 pb-3 pt-1">
      {/* 品牌 */}
      <div className="flex items-center gap-2 px-2 h-10 mb-1">
        <div className="w-6 h-6 rounded-md bg-blue-600 text-white grid place-items-center text-[13px] font-extrabold tracking-tighter">K</div>
        <div className="text-[14px] font-bold text-neutral-800 tracking-tight">OpenKB Studio</div>
        <span className="text-[10px] font-mono2 text-neutral-400 mt-0.5">0.1</span>
      </div>

      {/* 主导航 */}
      <nav className="space-y-0.5">
        <NavItem to="/" end icon={<MessageSquare className="w-4 h-4" />} label="首页" />
        <NavItem to="/kb" icon={<Library className="w-4 h-4" />} label="知识库" />
        <NavItem to="/settings" icon={<Settings2 className="w-4 h-4" />} label="设置" />
      </nav>

      {/* 知识库列表 */}
      <div className="mt-5 px-3 flex items-center justify-between">
        <span className="text-[11px] font-semibold text-neutral-400 tracking-wide">知识库</span>
        <button className="text-neutral-400 hover:text-neutral-700 transition-colors" title="新建知识库">
          <Plus className="w-3.5 h-3.5" />
        </button>
      </div>
      <div className="mt-1 space-y-0.5">
        {kbs.map((kb, i) => (
          <button
            key={kb.name}
            onClick={() => navigate(`/kb/${encodeURIComponent(kb.name)}`)}
            className="w-full flex items-center gap-2.5 px-3 h-8 rounded-lg text-[13px] text-neutral-600 hover:bg-white/60 hover:text-neutral-900 transition-colors"
          >
            <span className={cn('w-2 h-2 rounded-full shrink-0', dotFor(i))} />
            <span className="truncate">{kb.name}</span>
            <span className="ml-auto text-[11px] text-neutral-400 font-mono2">{kb.document_count}</span>
          </button>
        ))}
      </div>

      <div className="flex-1" />

      {/* 底部：知识库计数（后台任务 widget 在 Task 9 接入，此处不渲染占位） */}
      <div className="rounded-xl bg-white/70 border border-black/5 p-3">
        <div className="flex items-center justify-between text-[11px] text-neutral-400">
          <span className="font-mono2">OpenKB</span>
          <span>{kbs.length} 个知识库</span>
        </div>
      </div>
    </aside>
  )
}
