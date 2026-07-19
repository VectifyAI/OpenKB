import { useMemo, useState } from "react"
import { FileText } from "lucide-react"
import type { KbInventory } from "@/api/wiki"
import { cn } from "@/lib/utils"

const PAGE_SIZE = 50

export type TypeKey = "concepts" | "entities" | "summaries" | "reports"

/** Build the wiki path for a page name given its type. summaries/concepts/
 * entities are stems (endpoint appends .md); reports are full names. */
function pathFor(type: TypeKey, name: string): string {
  return `${type}/${name}`
}

/**
 * Paginated page list for ONE wiki type. The type is fixed by the active
 * Overview card (Sub-project G removed the internal segmented control); only
 * pagination is local state. Callers remount this via `key={type}` so page
 * resets on a type switch — mirroring the old selectType reset.
 */
export default function PageList({
  inv,
  type,
  activePath,
  onOpen,
}: {
  inv: KbInventory | null
  type: TypeKey
  activePath: string | null
  onOpen: (path: string) => void
}) {
  const [page, setPage] = useState(0)

  const names = useMemo<string[]>(() => (inv ? (inv[type] ?? []) : []), [inv, type])

  const pageCount = Math.max(1, Math.ceil(names.length / PAGE_SIZE))
  const clampedPage = Math.min(page, pageCount - 1)
  const slice = names.slice(clampedPage * PAGE_SIZE, (clampedPage + 1) * PAGE_SIZE)

  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="flex-1 min-h-0 overflow-y-auto scroll-edge-top px-2 pt-2 pb-2">
        {slice.length === 0 ? (
          <div className="text-[12px] text-muted-foreground px-2 py-6 text-center">
            此类型暂无页面
          </div>
        ) : (
          slice.map((name) => {
            const path = pathFor(type, name)
            return (
              <button
                key={path}
                onClick={() => onOpen(path)}
                className={cn(
                  "w-full flex items-center gap-2 px-2.5 h-8 rounded-apple-sm text-left text-[12.5px] transition-colors duration-fast ease-out-apple",
                  path === activePath
                    ? "bg-accent-brand/10 text-accent-brand font-medium"
                    : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
                )}
              >
                <FileText className="w-3.5 h-3.5 shrink-0 opacity-50" />
                <span className="truncate font-mono2 text-[12px]">{name}</span>
              </button>
            )
          })
        )}
      </div>

      {pageCount > 1 && (
        <div className="shrink-0 flex items-center justify-between px-3 py-1.5 border-t border-[hsl(var(--glass-border))] text-[11px] text-muted-foreground">
          <button
            disabled={clampedPage === 0}
            onClick={() => setPage(clampedPage - 1)}
            className="disabled:opacity-40 hover:text-foreground"
          >
            上一页
          </button>
          <span className="tabular-nums">
            {clampedPage + 1} / {pageCount}
          </span>
          <button
            disabled={clampedPage >= pageCount - 1}
            onClick={() => setPage(clampedPage + 1)}
            className="disabled:opacity-40 hover:text-foreground"
          >
            下一页
          </button>
        </div>
      )}
    </div>
  )
}
