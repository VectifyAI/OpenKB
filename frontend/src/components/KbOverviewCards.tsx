import { List, Network, Users, FileText, ClipboardCheck, type LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { KbInventory } from '@/api/wiki'

interface OverviewCard {
  label: string
  value: number
  caption: string
  icon: LucideIcon
  chip: string
  num: string
  onClick?: () => void
}

/** Apple-design KB Overview: five stat cards (Index is clickable → index.md). */
export default function KbOverviewCards({
  inv,
  docCount,
  onOpenIndex,
}: {
  inv: KbInventory
  docCount: number
  onOpenIndex: () => void
}) {
  const cards: OverviewCard[] = [
    { label: 'Index', value: 1, caption: 'wiki root', icon: List,
      chip: 'bg-blue-100 text-blue-600 dark:bg-blue-500/15 dark:text-blue-400',
      num: 'text-blue-600 dark:text-blue-400', onClick: onOpenIndex },
    { label: 'Concepts', value: inv.concepts.length, caption: 'cross-linked', icon: Network,
      chip: 'bg-cyan-100 text-cyan-600 dark:bg-cyan-500/15 dark:text-cyan-400',
      num: 'text-cyan-600 dark:text-cyan-400' },
    { label: 'Entities', value: inv.entities.length, caption: 'people · orgs', icon: Users,
      chip: 'bg-violet-100 text-violet-600 dark:bg-violet-500/15 dark:text-violet-400',
      num: 'text-violet-600 dark:text-violet-400' },
    { label: 'Summaries', value: inv.summaries.length, caption: 'per-document', icon: FileText,
      chip: 'bg-emerald-100 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-400',
      num: 'text-emerald-600 dark:text-emerald-400' },
    { label: 'Reports', value: inv.reports.length, caption: 'lint · recompile', icon: ClipboardCheck,
      chip: 'bg-amber-100 text-amber-600 dark:bg-amber-500/15 dark:text-amber-400',
      num: 'text-amber-600 dark:text-amber-400' },
  ]

  return (
    <div>
      <p className="mt-1 text-[13px] text-muted-foreground">
        由 <span className="tabular-nums">{docCount}</span> 篇文档编译而成
      </p>
      <div className="mt-3 grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-5">
        {cards.map((c) => {
          const inner = (
            <>
              <div className="flex items-center gap-1.5">
                <span className={cn('grid h-6 w-6 place-items-center rounded-lg', c.chip)}>
                  <c.icon className="w-3.5 h-3.5" />
                </span>
                <span className="text-[12px] font-semibold text-foreground">{c.label}</span>
              </div>
              <div className={cn('mt-1.5 text-[24px] font-bold leading-none tabular-nums tracking-tight', c.num)}>
                {c.value}
              </div>
              <div className="mt-1 text-[11px] text-muted-foreground">{c.caption}</div>
            </>
          )
          return c.onClick ? (
            <button
              key={c.label}
              onClick={c.onClick}
              className="rounded-apple-md glass-2 border border-[hsl(var(--glass-border))] p-3 text-left transition duration-fast ease-out-apple hover:-translate-y-0.5 hover:shadow-glass active:scale-[0.98]"
            >
              {inner}
            </button>
          ) : (
            <div
              key={c.label}
              className="rounded-apple-md glass-2 border border-[hsl(var(--glass-border))] p-3"
            >
              {inner}
            </div>
          )
        })}
      </div>
    </div>
  )
}
