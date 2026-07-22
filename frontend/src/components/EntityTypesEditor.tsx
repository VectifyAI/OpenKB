import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { X } from 'lucide-react'

/** The backend's catch-all bucket: it is re-appended server-side on every
 *  write, so the editor renders it as a fixed, non-removable chip. */
const ALWAYS_INCLUDED = 'other'

/**
 * Chips editor for the entity-extraction vocabulary, shared by the per-KB
 * override row (KbSettingsSheet) and the global editor (Settings › general).
 * Controlled: renders `value` as removable chips + an add input (Enter to add,
 * commas split), and calls `onChange` with the FULL next list on each
 * add/remove. Client-side it only trims/lowercases and drops empties/dupes —
 * the authoritative cleaning (charset, dedupe, "other") happens server-side.
 */
export default function EntityTypesEditor({
  value,
  disabled,
  onChange,
}: {
  /** Current list (server-cleaned effective list, or local draft state). */
  value: string[]
  disabled?: boolean
  /** Receives the complete next list after an add/remove. */
  onChange: (next: string[]) => void
}) {
  const { t } = useTranslation('common')
  const [draft, setDraft] = useState('')

  const add = () => {
    const seen = new Set(value.map((v) => v.toLowerCase()))
    const added: string[] = []
    for (const part of draft.split(',')) {
      const v = part.trim().toLowerCase()
      if (v === '' || seen.has(v)) continue
      seen.add(v)
      added.push(v)
    }
    setDraft('')
    if (added.length > 0) onChange([...value, ...added])
  }

  return (
    <div className="flex flex-wrap items-center gap-1.5 rounded-md border border-input px-2.5 py-2">
      {value.map((item) => (
        <span
          key={item}
          className="inline-flex items-center gap-1 rounded-full border border-[hsl(var(--glass-border))] bg-muted px-2.5 py-1 text-[11.5px] font-mono2 text-foreground"
        >
          {item}
          {item === ALWAYS_INCLUDED ? (
            <span className="text-[10.5px] text-muted-foreground">{t('entityTypes.always')}</span>
          ) : (
            <button
              type="button"
              onClick={() => onChange(value.filter((x) => x !== item))}
              disabled={disabled}
              aria-label={t('entityTypes.removeAria', { type: item })}
              className="grid h-3.5 w-3.5 place-items-center rounded-full text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-60"
            >
              <X className="h-2.5 w-2.5" />
            </button>
          )}
        </span>
      ))}
      <input
        value={draft}
        disabled={disabled}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          // Enter confirms IME composition first; only a real Enter adds.
          if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
            e.preventDefault()
            add()
          }
        }}
        placeholder={t('entityTypes.placeholder')}
        aria-label={t('entityTypes.placeholder')}
        className="h-6 min-w-[140px] flex-1 bg-transparent text-[12.5px] font-mono2 outline-none placeholder:text-muted-foreground/70 disabled:opacity-60"
      />
    </div>
  )
}
