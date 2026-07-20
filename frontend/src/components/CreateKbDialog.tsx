import { useCallback, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router'
import { useTranslation } from 'react-i18next'
import * as Dialog from '@radix-ui/react-dialog'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { Loader2, Plus } from 'lucide-react'
import { createKb } from '@/api/kb'

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e))

/**
 * Modal "new knowledge base" dialog. The single required field is the KB name;
 * model/credentials inherit global defaults and are edited later via the per-KB
 * gear (KbSettingsSheet). We wrap the caller's control as `Dialog.Trigger
 * asChild`, so Radix owns focus-trap, Escape, and focus-return-to-trigger; we
 * still drive `open` from state to close programmatically after a create.
 * Backend errors (invalid name / already exists) surface INLINE so the user can
 * correct the name and retry.
 */
export default function CreateKbDialog({ children }: { children: ReactNode }) {
  const { t } = useTranslation(['kbList', 'common'])
  const navigate = useNavigate()
  const reduce = useReducedMotion()

  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  // Reset transient form state on every open/close so a reopened dialog is
  // clean and a Radix-initiated close (Escape / scrim) can't strand an error.
  const onOpenChange = useCallback((next: boolean) => {
    if (submitting) return
    setOpen(next)
    setName('')
    setError(null)
  }, [submitting])

  const trimmed = name.trim()

  const submit = useCallback(async () => {
    if (!trimmed || submitting) return
    setSubmitting(true)
    setError(null)
    try {
      await createKb({ kb: trimmed })
      // Broadcast so any live KB list (e.g. AppSidebar) re-fetches without a
      // full reload; mirrors the old app's `openkb:reload-kbs` signal.
      window.dispatchEvent(new CustomEvent('openkb:reload-kbs'))
      setSubmitting(false)
      setOpen(false)
      setName('')
      navigate(`/kb/${encodeURIComponent(trimmed)}`)
    } catch (e) {
      setError(errMsg(e))
      setSubmitting(false)
    }
  }, [trimmed, submitting, navigate])

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Trigger asChild>{children}</Dialog.Trigger>
      <AnimatePresence>
        {open && (
          <Dialog.Portal forceMount>
            <Dialog.Overlay asChild forceMount>
              <motion.div
                className="fixed inset-0 z-50 bg-black/30"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={reduce ? { duration: 0.12 } : { duration: 0.2 }}
              />
            </Dialog.Overlay>
            <div className="fixed inset-0 z-50 grid place-items-center p-4">
              <Dialog.Content asChild forceMount>
                <motion.div
                  className="w-full max-w-[440px] glass border border-[hsl(var(--glass-border))] shadow-glass-lg rounded-apple-lg p-6"
                  initial={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.96, y: 8 }}
                  animate={reduce ? { opacity: 1 } : { opacity: 1, scale: 1, y: 0 }}
                  exit={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.96, y: 8 }}
                  transition={reduce ? { duration: 0.12 } : { type: 'spring', bounce: 0, duration: 0.3 }}
                >
                  <Dialog.Title asChild>
                    <h2 className="text-[16px] font-bold text-foreground">{t('common:actions.newKb')}</h2>
                  </Dialog.Title>
                  <Dialog.Description asChild>
                    <p className="mt-1 text-[13px] text-muted-foreground">{t('create.description')}</p>
                  </Dialog.Description>

                  <form
                    className="mt-5"
                    onSubmit={(e) => {
                      e.preventDefault()
                      void submit()
                    }}
                  >
                    <label htmlFor="create-kb-name" className="text-[12px] font-medium text-muted-foreground">
                      {t('create.nameLabel')}
                    </label>
                    {/* autoFocus is safe here: Radix moves focus into Content on
                        open, and this is the first focusable control. */}
                    <input
                      id="create-kb-name"
                      // eslint-disable-next-line jsx-a11y/no-autofocus
                      autoFocus
                      value={name}
                      disabled={submitting}
                      onChange={(e) => {
                        setName(e.target.value)
                        if (error) setError(null)
                      }}
                      placeholder={t('create.namePlaceholder')}
                      className="mt-1.5 w-full h-9 rounded-md border border-input bg-transparent px-3 text-[13px] font-mono2 outline-none focus-visible:ring-2 focus-visible:ring-ring focus:border-accent-brand"
                    />

                    {error && (
                      <div className="mt-2.5 rounded-lg bg-red-50 dark:bg-red-500/10 border border-red-200/70 dark:border-red-500/25 px-3 py-2 text-[12.5px] text-red-600 dark:text-red-400">
                        {error}
                      </div>
                    )}

                    <div className="mt-6 flex items-center justify-end gap-2.5">
                      <Dialog.Close asChild>
                        <button
                          type="button"
                          disabled={submitting}
                          className="inline-flex items-center h-9 px-4 rounded-xl border border-[hsl(var(--glass-border))] text-[13px] font-medium text-muted-foreground hover:bg-accent hover:text-foreground transition-colors disabled:opacity-60"
                        >
                          {t('create.cancel')}
                        </button>
                      </Dialog.Close>
                      <button
                        type="submit"
                        disabled={submitting || trimmed === ''}
                        className="inline-flex items-center gap-1.5 h-9 px-4 rounded-xl bg-accent-brand text-white text-[13px] font-medium hover:opacity-90 shadow-sm transition disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
                        {submitting ? t('create.submitting') : t('create.submit')}
                      </button>
                    </div>
                  </form>
                </motion.div>
              </Dialog.Content>
            </div>
          </Dialog.Portal>
        )}
      </AnimatePresence>
    </Dialog.Root>
  )
}
