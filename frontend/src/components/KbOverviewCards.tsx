import {
  List, Network, Users, FileText, ClipboardCheck, FolderInput, type LucideIcon,
} from 'lucide-react'
import { motion, useReducedMotion } from 'motion/react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import type { KbInventory } from '@/api/wiki'

export type Section =
  | 'index' | 'concepts' | 'entities' | 'summaries' | 'reports' | 'documents'

interface NavCard {
  section: Section
  label: string
  value: number
  caption: string
  icon: LucideIcon
  chip: string
  num: string
}

/**
 * Apple-design KB navigation (Sub-project G): the six cards ARE the tab bar.
 * The active card carries a shared selection indicator that springs from card
 * to card via a motion `layoutId` (Apple §7 spatial consistency). The app root
 * wraps everything in <MotionConfig reducedMotion="user"> so the layout
 * animation degrades to an instant swap under prefers-reduced-motion.
 */
export default function KbOverviewCards({
  inv,
  docCount,
  active,
  onSelect,
}: {
  inv: KbInventory
  docCount: number
  active: Section
  onSelect: (section: Section) => void
}) {
  const reduce = useReducedMotion()
  const { t } = useTranslation('kb')
  const cards: NavCard[] = [
    { section: 'index', label: t('overview.index.label'), value: 1, caption: t('overview.index.caption'), icon: List,
      chip: 'bg-blue-100 text-blue-600 dark:bg-blue-500/15 dark:text-blue-400',
      num: 'text-blue-600 dark:text-blue-400' },
    { section: 'concepts', label: t('overview.concepts.label'), value: inv.concepts.length, caption: t('overview.concepts.caption'), icon: Network,
      chip: 'bg-cyan-100 text-cyan-600 dark:bg-cyan-500/15 dark:text-cyan-400',
      num: 'text-cyan-600 dark:text-cyan-400' },
    { section: 'entities', label: t('overview.entities.label'), value: inv.entities.length, caption: t('overview.entities.caption'), icon: Users,
      chip: 'bg-violet-100 text-violet-600 dark:bg-violet-500/15 dark:text-violet-400',
      num: 'text-violet-600 dark:text-violet-400' },
    { section: 'summaries', label: t('overview.summaries.label'), value: inv.summaries.length, caption: t('overview.summaries.caption'), icon: FileText,
      chip: 'bg-emerald-100 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-400',
      num: 'text-emerald-600 dark:text-emerald-400' },
    { section: 'reports', label: t('overview.reports.label'), value: inv.reports.length, caption: t('overview.reports.caption'), icon: ClipboardCheck,
      chip: 'bg-amber-100 text-amber-600 dark:bg-amber-500/15 dark:text-amber-400',
      num: 'text-amber-600 dark:text-amber-400' },
    { section: 'documents', label: t('overview.documents.label'), value: docCount, caption: t('overview.documents.caption'), icon: FolderInput,
      chip: 'bg-slate-100 text-slate-600 dark:bg-slate-500/15 dark:text-slate-300',
      num: 'text-slate-600 dark:text-slate-300' },
  ]

  return (
    <div className="mt-3 grid grid-cols-3 gap-2.5 lg:grid-cols-6">
      {cards.map((c) => {
        const on = c.section === active
        return (
          <button
            key={c.section}
            onClick={() => onSelect(c.section)}
            aria-pressed={on}
            className="relative rounded-apple-md glass-2 border border-[hsl(var(--glass-border))] p-3 text-left transition duration-fast ease-out-apple hover:-translate-y-0.5 hover:shadow-glass active:scale-[0.98]"
          >
            {on && (
              <motion.span
                layoutId="kb-nav-active"
                className="pointer-events-none absolute inset-0 rounded-apple-md ring-2 ring-accent-brand/40 bg-accent-brand/[0.08] shadow-[inset_0_1px_0_0_hsl(var(--glass-highlight))]"
                transition={reduce ? { duration: 0 } : { type: 'spring', bounce: 0, duration: 0.4 }}
              />
            )}
            <div className="relative">
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
            </div>
          </button>
        )
      })}
    </div>
  )
}
