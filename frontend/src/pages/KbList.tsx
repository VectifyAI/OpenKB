import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router'
import { Plus, FileText } from 'lucide-react'
import { listKbs, type KbSummary } from '@/api/kb'
import { cn } from '@/lib/utils'

/** Decorative accent colors, cycled by position — the API carries no color. */
const DOTS = ['bg-blue-500', 'bg-emerald-500', 'bg-amber-500', 'bg-violet-500', 'bg-rose-500']
const dotFor = (i: number) => DOTS[i % DOTS.length]

function formatCompile(last: string | null): string {
  if (!last) return '尚未编译'
  return `更新于 ${last.replace('T', ' ').slice(0, 16)}`
}

export default function KbList() {
  const navigate = useNavigate()
  const [kbs, setKbs] = useState<KbSummary[]>([])

  useEffect(() => {
    listKbs()
      .then(r => setKbs(r.knowledge_bases))
      .catch(() => setKbs([]))
  }, [])

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-[900px] mx-auto px-6 py-8">
        <div className="flex items-end justify-between anim-fade-up">
          <div>
            <h1 className="text-[22px] font-extrabold tracking-tight text-foreground">知识库</h1>
            <p className="mt-1 text-[13px] text-muted-foreground">每个知识库是一个持续编译的 wiki，可绑定多个数据源</p>
          </div>
          <button className="inline-flex items-center gap-1.5 h-9 px-4 rounded-xl bg-accent-brand text-white text-[13px] font-medium hover:opacity-90 shadow-sm transition duration-fast ease-out-apple active:scale-[0.97]">
            <Plus className="w-4 h-4" />新建知识库
          </button>
        </div>

        <div className="mt-6 grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {kbs.map((kb, i) => (
            <button
              key={kb.name}
              onClick={() => navigate(`/kb/${encodeURIComponent(kb.name)}`)}
              className={cn('anim-fade-up text-left rounded-2xl border border-[hsl(var(--glass-border))] glass-2 p-5 hover:shadow-glass hover:-translate-y-0.5 transition-[transform,box-shadow] duration-fast ease-out-apple active:scale-[0.98]', `anim-d${(i % 4) + 1}`)}
            >
              <div className="flex items-center gap-2.5">
                <span className={cn('w-2.5 h-2.5 rounded-full', dotFor(i))} />
                <span className="text-[16px] font-bold text-foreground">{kb.name}</span>
              </div>

              <div className="mt-4">
                <div className="inline-flex items-center gap-3 rounded-xl bg-muted/50 border border-[hsl(var(--glass-border))] px-3.5 py-2.5">
                  <FileText className="w-4 h-4 text-muted-foreground" />
                  <div>
                    <div className="text-[17px] font-bold text-foreground leading-none tabular-nums tracking-[-0.02em]">{kb.document_count}</div>
                    <div className="mt-1 text-[11px] text-muted-foreground">文档</div>
                  </div>
                </div>
              </div>

              <div className="mt-4 flex items-center justify-between text-[11.5px] text-muted-foreground">
                <span className={cn('font-mono2 rounded px-1.5 py-0.5', kb.has_raw ? 'bg-muted' : 'bg-amber-50 dark:bg-amber-500/10 text-amber-600 dark:text-amber-400')}>
                  {kb.has_raw ? 'raw/ 已就绪' : '无 raw/'}
                </span>
                <span>{formatCompile(kb.last_compile)}</span>
              </div>
            </button>
          ))}

          {/* 新建虚线卡 */}
          <button className="anim-fade-up anim-d4 rounded-2xl border-2 border-dashed border-[hsl(var(--glass-border))] p-5 grid place-items-center text-muted-foreground hover:text-accent-brand hover:border-accent-brand/40 hover:bg-accent-brand/5 transition-[color,background-color,border-color,transform] duration-fast ease-out-apple active:scale-[0.97] min-h-[160px]">
            <span className="flex flex-col items-center gap-2">
              <Plus className="w-6 h-6" />
              <span className="text-[13px] font-medium">新建知识库</span>
            </span>
          </button>
        </div>
      </div>
    </div>
  )
}
