import { useCallback, useEffect, useState } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import {
  X, KeyRound, Loader2, Trash2, RefreshCw, Play, CheckCircle2, AlertCircle,
  ShieldCheck, Clock, Radio,
} from 'lucide-react'
import { toast } from 'sonner'
import { getKbConfig, patchKbConfig, type KbConfig, type ConfigSource } from '@/api/kb'
import {
  watchStart, watchStop, watchStatus, runRecompile, runLint, type WatchStatus,
} from '@/api/maintenance'
import type { SseEvent } from '@/api/client'
import { Switch } from '@/components/ui/switch'
import { cn } from '@/lib/utils'

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e))

/** Right-anchored settings sheet opened from KbDetail's header gear. Uses the
 *  D-era motion spring pattern (ArtifactPanel), NOT the CSS-animated Radix
 *  sheet, for reduced-motion parity. */
export default function KbSettingsSheet({
  kb,
  open,
  onClose,
  docCount,
  onChanged,
}: {
  kb: string
  open: boolean
  onClose: () => void
  docCount: number
  onChanged?: () => void
}) {
  const reduce = useReducedMotion()

  // Esc dismisses (Apple wayfinding — always an exit).
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            className="fixed inset-0 z-40 bg-black/30"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={reduce ? { duration: 0.12 } : { duration: 0.2 }}
            onClick={onClose}
          />
          <motion.aside
            className="fixed inset-y-0 right-0 z-50 flex w-[420px] max-w-[92vw] flex-col glass border-l border-[hsl(var(--glass-border))] shadow-glass-lg rounded-l-apple-lg"
            initial={reduce ? { opacity: 0 } : { opacity: 0, x: 24, scale: 0.98 }}
            animate={reduce ? { opacity: 1 } : { opacity: 1, x: 0, scale: 1 }}
            exit={reduce ? { opacity: 0 } : { opacity: 0, x: 24, scale: 0.98 }}
            transition={reduce ? { duration: 0.12 } : { type: 'spring', bounce: 0, duration: 0.3 }}
          >
            <div className="shrink-0 h-12 flex items-center gap-2 px-4 border-b border-[hsl(var(--glass-border))]">
              <span className="text-[14px] font-semibold text-foreground">知识库设置</span>
              <button
                onClick={onClose}
                title="关闭"
                className="ml-auto grid h-7 w-7 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="flex-1 min-h-0 overflow-y-auto scroll-edge-top px-4 py-4 space-y-6">
              <KbConfigSection kb={kb} />
              <KbMaintenanceSection kb={kb} open={open} docCount={docCount} onChanged={onChanged} />
            </div>
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  )
}

/** Effective-value + inherit/override editor for the three scalar fields, plus
 *  write-only credentials — all written to the KB's config.yaml / .env. */
function KbConfigSection({ kb }: { kb: string }) {
  const [config, setConfig] = useState<KbConfig | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [apiKeyInput, setApiKeyInput] = useState('')
  const [apiBase, setApiBase] = useState('')
  const [busy, setBusy] = useState(false)

  const apply = useCallback((c: KbConfig) => {
    setConfig(c)
    setApiBase(c.openai_api_base ?? '')
    setApiKeyInput('')
  }, [])

  useEffect(() => {
    let cancelled = false
    getKbConfig(kb)
      .then((c) => !cancelled && apply(c))
      .catch((e) => !cancelled && setLoadError(errMsg(e)))
    return () => {
      cancelled = true
    }
  }, [kb, apply])

  // Persist a single scalar override (value) or revert to inherited (null).
  const setOverride = useCallback(
    async (field: 'model' | 'language' | 'pageindex_threshold', value: string | number | null) => {
      setBusy(true)
      try {
        const next = await patchKbConfig(kb, { config: { [field]: value } })
        apply(next)
      } catch (e) {
        toast.error(`保存失败：${errMsg(e)}`)
      } finally {
        setBusy(false)
      }
    },
    [kb, apply],
  )

  const saveCredentials = useCallback(async () => {
    if (!config) return
    setBusy(true)
    try {
      const patch: Parameters<typeof patchKbConfig>[1] = {}
      const baseTrim = apiBase.trim()
      const currentBase = config.openai_api_base ?? ''
      if (baseTrim !== currentBase) patch.openai_api_base = baseTrim === '' ? null : baseTrim
      if (apiKeyInput !== '') patch.api_key = apiKeyInput
      if (Object.keys(patch).length === 0) {
        toast.info('没有需要保存的更改')
        return
      }
      apply(await patchKbConfig(kb, patch))
      toast.success('凭证已保存')
    } catch (e) {
      toast.error(`保存失败：${errMsg(e)}`)
    } finally {
      setBusy(false)
    }
  }, [kb, config, apiBase, apiKeyInput, apply])

  const clearApiKey = useCallback(async () => {
    setBusy(true)
    try {
      apply(await patchKbConfig(kb, { api_key: null }))
      toast.success('已清除 API Key')
    } catch (e) {
      toast.error(`清除失败：${errMsg(e)}`)
    } finally {
      setBusy(false)
    }
  }, [kb, apply])

  if (loadError) {
    return (
      <div className="rounded-lg bg-red-50 dark:bg-red-500/10 border border-red-200/70 dark:border-red-500/25 px-3 py-2 text-[12.5px] text-red-600 dark:text-red-400">
        加载配置失败：{loadError}
      </div>
    )
  }
  if (!config) {
    return <div className="text-[12.5px] text-muted-foreground">加载中…</div>
  }

  const hasKey = config.has_api_key

  return (
    <div className="space-y-4">
      <h3 className="text-[12px] font-semibold text-muted-foreground tracking-wide">配置</h3>

      <OverrideRow
        label="模型"
        field="model"
        source={config.sources.model}
        effective={config.model}
        globalValue={config.global_values.model}
        busy={busy}
        onSet={(v) => setOverride('model', v.trim() === '' ? null : v.trim())}
        onRevert={() => setOverride('model', null)}
      />
      <OverrideRow
        label="Wiki 输出语言"
        field="language"
        source={config.sources.language}
        effective={config.language}
        globalValue={config.global_values.language}
        busy={busy}
        onSet={(v) => setOverride('language', v.trim() === '' ? null : v.trim())}
        onRevert={() => setOverride('language', null)}
      />
      <OverrideRow
        label="PageIndex 阈值（页数）"
        field="pageindex_threshold"
        source={config.sources.pageindex_threshold}
        effective={String(config.pageindex_threshold)}
        globalValue={
          config.global_values.pageindex_threshold == null
            ? null
            : String(config.global_values.pageindex_threshold)
        }
        busy={busy}
        numeric
        // OverrideRow's onBlur only calls onSet once `draft` has already been
        // validated as a non-negative integer string, so this is safe to
        // convert directly (no silent null-on-invalid — see OverrideRow).
        onSet={(v) => setOverride('pageindex_threshold', Number(v))}
        onRevert={() => setOverride('pageindex_threshold', null)}
      />

      <h3 className="pt-2 text-[12px] font-semibold text-muted-foreground tracking-wide">凭证（本库 .env）</h3>
      <div>
        <label className="text-[12px] font-medium text-muted-foreground flex items-center gap-1">
          <KeyRound className="w-3 h-3" />API Key
        </label>
        <input
          type="password"
          value={apiKeyInput}
          autoComplete="new-password"
          disabled={busy}
          onChange={(e) => setApiKeyInput(e.target.value)}
          placeholder={hasKey ? '已设置密钥 · 留空则保持不变' : '未设置 · 输入以启用'}
          className="mt-1.5 w-full h-9 rounded-md border border-input bg-transparent px-3 text-[13px] font-mono2 outline-none focus-visible:ring-2 focus-visible:ring-ring focus:border-accent-brand"
        />
        <div className="mt-1.5 flex items-center gap-2 text-[11.5px] text-muted-foreground">
          <span className={cn('inline-block w-1.5 h-1.5 rounded-full', hasKey ? 'bg-emerald-500' : 'bg-muted-foreground/40')} />
          {hasKey ? '已设置密钥（永不回显；输入新值即可轮换）' : '未设置密钥'}
          {hasKey && (
            <button
              onClick={clearApiKey}
              disabled={busy}
              className="ml-auto inline-flex items-center gap-1 h-7 px-2.5 rounded-lg border border-[hsl(var(--glass-border))] text-[12px] font-medium text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-500/10 transition-colors disabled:opacity-60"
            >
              {busy ? <Loader2 className="w-3 h-3 animate-spin" /> : <Trash2 className="w-3 h-3" />}清除
            </button>
          )}
        </div>
      </div>
      <div>
        <label className="text-[12px] font-medium text-muted-foreground">API Base URL（可选）</label>
        <input
          value={apiBase}
          disabled={busy}
          onChange={(e) => setApiBase(e.target.value)}
          placeholder="留空使用 provider 默认；本地/兼容端点时填写"
          className="mt-1.5 w-full h-9 rounded-md border border-input bg-transparent px-3 text-[13px] font-mono2 outline-none focus-visible:ring-2 focus-visible:ring-ring focus:border-accent-brand"
        />
      </div>
      <button
        onClick={saveCredentials}
        disabled={busy}
        className="inline-flex items-center gap-1.5 h-9 px-4 rounded-xl bg-accent-brand text-white text-[13px] font-medium hover:bg-accent-brand/90 shadow-sm transition-colors disabled:opacity-50"
      >
        {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : null}保存凭证
      </button>
    </div>
  )
}

/** One scalar field: a 为本库覆盖 switch + inherited badge OR an override input. */
function OverrideRow({
  label, field, source, effective, globalValue, busy, numeric, onSet, onRevert,
}: {
  label: string
  field: string
  source: ConfigSource
  effective: string
  globalValue: string | null
  busy: boolean
  numeric?: boolean
  onSet: (value: string) => void
  onRevert: () => void
}) {
  const overridden = source === 'kb'
  const [draft, setDraft] = useState(effective)
  useEffect(() => setDraft(effective), [effective])

  const inheritedBadge =
    source === 'global'
      ? `继承 · 全局(${globalValue ?? effective})`
      : `继承 · 默认(${effective})`

  return (
    <div>
      <div className="flex items-center justify-between">
        <label className="text-[12px] font-medium text-muted-foreground">{label}</label>
        <span className="flex items-center gap-2 text-[11px] text-muted-foreground">
          为本库覆盖
          <Switch
            checked={overridden}
            disabled={busy}
            onCheckedChange={(v) => (v ? onSet(effective) : onRevert())}
            aria-label={`${label} 为本库覆盖`}
          />
        </span>
      </div>
      {overridden ? (
        <input
          type={numeric ? 'number' : 'text'}
          value={draft}
          disabled={busy}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={() => {
            if (numeric) {
              // A non-integer/empty blur must never silently revert the
              // override to inherited — that's a data change the user
              // didn't ask for. Only a valid non-negative integer patches;
              // anything else just snaps the field back to its real value.
              // Reverting to inherit stays the toggle's job (onRevert).
              const trimmed = draft.trim()
              const n = Number(trimmed)
              const valid = trimmed !== '' && Number.isInteger(n) && n >= 0
              if (!valid) {
                setDraft(effective)
                return
              }
            }
            if (draft !== effective) onSet(draft)
          }}
          className="mt-1.5 w-full h-9 rounded-md border border-input bg-transparent px-3 text-[13px] font-mono2 outline-none focus-visible:ring-2 focus-visible:ring-ring focus:border-accent-brand"
        />
      ) : (
        <div
          data-field={field}
          className="mt-1.5 flex h-9 items-center rounded-md border border-dashed border-[hsl(var(--glass-border))] px-3 text-[12.5px] text-muted-foreground"
        >
          {inheritedBadge}
        </div>
      )}
    </div>
  )
}

/** Per-doc row accumulated from a recompile SSE stream (from the `doc` event).
 *  Moved verbatim from `pages/KbDetail.tsx` (formerly its 任务 tab). */
interface RecompileDoc {
  name: string
  type: string
  status: string
  elapsed: number | null
  message: string | null
}

/** Live recompile progress, folded from `runRecompile`'s SSE events. Moved
 *  verbatim from `pages/KbDetail.tsx`. */
interface RecompileState {
  status: 'idle' | 'running' | 'done' | 'error'
  docs: RecompileDoc[]
  summary: { total: number; recompiled: number; skipped: number } | null
  error: string | null
}

const initialRecompile: RecompileState = { status: 'idle', docs: [], summary: null, error: null }

/**
 * Fold one recompile SSE event into the running job state. Mirrors the
 * per-event accumulation used by the chat/deck streams: `start` resets, each
 * `doc` appends a row, `final` records the summary, `error` is terminal.
 * Moved verbatim from `pages/KbDetail.tsx`.
 */
function foldRecompile(s: RecompileState, ev: SseEvent): RecompileState {
  switch (ev.event) {
    case 'start':
      return { status: 'running', docs: [], summary: null, error: null }
    case 'doc':
      return {
        ...s,
        docs: [
          ...s.docs,
          {
            name: ev.data?.doc_name ?? ev.data?.name ?? '(未命名)',
            type: ev.data?.type ?? '',
            status: ev.data?.status ?? '',
            elapsed: ev.data?.elapsed ?? null,
            message: ev.data?.message ?? null,
          },
        ],
      }
    case 'final':
      return {
        ...s,
        status: s.status === 'error' ? 'error' : 'done',
        summary: {
          total: ev.data?.total ?? 0,
          recompiled: ev.data?.recompiled ?? 0,
          skipped: ev.data?.skipped ?? 0,
        },
      }
    case 'error':
      return { ...s, status: 'error', error: ev.data?.message ?? '重新编译失败' }
    default:
      return s
  }
}

/** 维护: watch/recompile/lint controls, moved verbatim from KbDetail's 任务
 *  tab (behavior unchanged). Two deltas from the original: the watch-status
 *  poll re-gates from `tab === 'jobs'` to the sheet's `open` prop, and
 *  `startRecompile` calls `onChanged?.()` instead of KbDetail's own
 *  `refreshInventory` (KbDetail still owns the inventory fetch; it re-fetches
 *  via `onChanged`). */
function KbMaintenanceSection({
  kb, open, docCount, onChanged,
}: {
  kb: string
  open: boolean
  docCount: number
  onChanged?: () => void
}) {
  const [watch, setWatch] = useState<WatchStatus | null>(null)
  const [watchError, setWatchError] = useState<string | null>(null)
  const [watchBusy, setWatchBusy] = useState(false)
  const [recompile, setRecompile] = useState<RecompileState>(initialRecompile)
  const [recompileRunning, setRecompileRunning] = useState(false)
  const [lintBusy, setLintBusy] = useState(false)
  const [lintResult, setLintResult] = useState<string | null>(null)

  // Poll watcher status while the sheet is open so counters reflect reality
  // (added/skipped/failed tick up as the watcher ingests files).
  useEffect(() => {
    if (!open) return
    let cancelled = false
    const tick = () => {
      watchStatus(kb)
        .then((s) => {
          if (cancelled) return
          setWatch(s)
          setWatchError(null)
        })
        .catch((e) => {
          if (!cancelled) setWatchError(errMsg(e))
        })
    }
    tick()
    const iv = setInterval(tick, 5000)
    return () => {
      cancelled = true
      clearInterval(iv)
    }
  }, [open, kb])

  const toggleWatch = useCallback(async () => {
    if (watchBusy) return
    setWatchBusy(true)
    const wasActive = watch?.active === true
    try {
      const s = wasActive ? await watchStop(kb) : await watchStart(kb)
      setWatch(s)
      setWatchError(null)
      toast.success(wasActive ? '已停止文件监听' : '已启动文件监听')
    } catch (e) {
      toast.error(errMsg(e))
    } finally {
      setWatchBusy(false)
    }
  }, [kb, watch?.active, watchBusy])

  const startRecompile = useCallback(async () => {
    if (recompileRunning) return
    setRecompileRunning(true)
    setRecompile({ status: 'running', docs: [], summary: null, error: null })
    try {
      for await (const ev of runRecompile(kb)) {
        setRecompile((s) => foldRecompile(s, ev))
      }
    } catch (e) {
      const message = errMsg(e)
      setRecompile((s) => ({ ...s, status: 'error', error: s.error ?? message }))
      toast.error(`重新编译失败：${message}`)
    } finally {
      // A stream that ended without a terminal `final`/`error` still settles.
      setRecompile((s) => (s.status === 'running' ? { ...s, status: s.error ? 'error' : 'done' } : s))
      setRecompileRunning(false)
      // Recompiled pages change the wiki tree; keep the browse/来源 lists fresh.
      onChanged?.()
    }
  }, [kb, recompileRunning, onChanged])

  const runStructuralLint = useCallback(async () => {
    if (lintBusy) return
    setLintBusy(true)
    try {
      const res = (await runLint(kb, false)) as { message?: string; skipped?: boolean }
      setLintResult(res.message ?? (res.skipped ? '检查已跳过' : '检查完成'))
      toast.success('结构检查完成')
    } catch (e) {
      toast.error(`检查失败：${errMsg(e)}`)
    } finally {
      setLintBusy(false)
    }
  }, [kb, lintBusy])

  const watchActive = watch?.active === true

  return (
    <div className="space-y-5">
      <h3 className="text-[12px] font-semibold text-muted-foreground tracking-wide">维护</h3>

      {/* 文件监听（真实 /api/v1/watch/status） */}
      <div className="rounded-2xl border border-[hsl(var(--glass-border))] glass-2 px-4 py-3.5">
        <div className="flex items-center gap-3">
          <span
            className={cn(
              'w-8 h-8 rounded-lg grid place-items-center shrink-0',
              watchActive ? 'bg-emerald-50 dark:bg-emerald-500/10 text-emerald-500 dark:text-emerald-400' : 'bg-muted text-muted-foreground',
            )}
          >
            <Radio className={cn('w-4 h-4', watchActive && 'animate-pulse')} />
          </span>
          <div className="min-w-0 flex-1">
            <div className="text-[13.5px] font-medium text-foreground">文件监听</div>
            <div className="text-[12px] text-muted-foreground mt-0.5 truncate">
              {watchActive
                ? `监听中${watch?.raw_dir ? ` · ${watch.raw_dir}` : ''}`
                : '未运行 · 启动后新文件会自动编译'}
            </div>
          </div>
          <button
            onClick={toggleWatch}
            disabled={watchBusy}
            className={cn(
              'inline-flex items-center gap-1.5 h-8 px-3 rounded-lg text-[12.5px] font-medium transition-colors shrink-0 disabled:opacity-60',
              watchActive
                ? 'border border-[hsl(var(--glass-border))] text-muted-foreground hover:bg-accent'
                : 'bg-accent-brand text-white hover:bg-accent-brand/90',
            )}
          >
            {watchBusy ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : watchActive ? (
              <>停止</>
            ) : (
              <>
                <Play className="w-3.5 h-3.5" />启动监听
              </>
            )}
          </button>
        </div>
        {watchError && (
          <div className="mt-2.5 rounded-lg bg-red-50 dark:bg-red-500/10 border border-red-200/70 dark:border-red-500/25 px-3 py-2 text-[12px] text-red-600 dark:text-red-400">
            {watchError}
          </div>
        )}
        {watch?.counters && Object.keys(watch.counters).length > 0 && (
          <div className="mt-3 flex gap-2 flex-wrap">
            {Object.entries(watch.counters).map(([k, v]) => (
              <span
                key={k}
                className="inline-flex items-center gap-1.5 text-[11.5px] text-muted-foreground bg-muted border border-[hsl(var(--glass-border))] rounded-full px-2.5 py-1"
              >
                <span className="text-muted-foreground">{k}</span>
                <span className="font-semibold text-foreground">{v}</span>
              </span>
            ))}
          </div>
        )}
        {watch?.recent_events && watch.recent_events.length > 0 && (
          <div className="mt-3 border-t border-[hsl(var(--glass-border))] pt-2.5 space-y-1">
            {watch.recent_events.slice(-5).reverse().map((e, i) => (
              <div key={i} className="flex items-center gap-2 text-[11.5px] text-muted-foreground">
                <Clock className="w-3 h-3 shrink-0" />
                <span className="font-mono2 text-muted-foreground">{e.event}</span>
                <span className="truncate">
                  {typeof e.data?.original_name === 'string' ? e.data.original_name : ''}
                  {typeof e.data?.status === 'string' ? ` · ${e.data.status}` : ''}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 维护动作 */}
      <div className="flex items-center gap-2">
        <button
          onClick={startRecompile}
          disabled={recompileRunning || docCount === 0}
          title={docCount === 0 ? '暂无已编译文档' : undefined}
          className="inline-flex items-center gap-1.5 h-8 px-3 rounded-lg bg-primary text-primary-foreground text-[12.5px] font-medium hover:bg-primary/90 transition-colors disabled:opacity-50"
        >
          {recompileRunning ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
          重新编译全部
        </button>
        <button
          onClick={runStructuralLint}
          disabled={lintBusy}
          className="inline-flex items-center gap-1.5 h-8 px-3 rounded-lg border border-[hsl(var(--glass-border))] text-[12.5px] font-medium text-muted-foreground hover:bg-accent transition-colors disabled:opacity-60"
        >
          {lintBusy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ShieldCheck className="w-3.5 h-3.5" />}
          结构检查
        </button>
      </div>

      {lintResult && (
        <div className="rounded-2xl border border-[hsl(var(--glass-border))] glass-2 px-4 py-3 text-[12.5px] text-muted-foreground whitespace-pre-wrap">
          {lintResult}
        </div>
      )}

      {/* 重新编译进度（真实 SSE） */}
      {recompile.status !== 'idle' && (
        <div className="space-y-2.5">
          <div className="flex items-center gap-2 text-[13px] font-medium text-foreground">
            {recompile.status === 'running' && <Loader2 className="w-4 h-4 animate-spin text-accent-brand" />}
            {recompile.status === 'done' && <CheckCircle2 className="w-4 h-4 text-emerald-500 dark:text-emerald-400" />}
            {recompile.status === 'error' && <AlertCircle className="w-4 h-4 text-red-500 dark:text-red-400" />}
            重新编译
            {recompile.summary && (
              <span className="text-[12px] font-normal text-muted-foreground">
                · 共 {recompile.summary.total} · 编译 {recompile.summary.recompiled} · 跳过 {recompile.summary.skipped}
              </span>
            )}
          </div>
          {recompile.error && (
            <div className="rounded-lg bg-red-50 dark:bg-red-500/10 border border-red-200/70 dark:border-red-500/25 px-3 py-2 text-[12px] text-red-600 dark:text-red-400">
              {recompile.error}
            </div>
          )}
          {recompile.docs.map((d, i) => (
            <div key={`${d.name}-${i}`} className="rounded-2xl border border-[hsl(var(--glass-border))] glass-2 px-4 py-3 flex items-center gap-3">
              <span
                className={cn(
                  'w-8 h-8 rounded-lg grid place-items-center shrink-0',
                  d.status === 'ok'
                    ? 'bg-emerald-50 dark:bg-emerald-500/10 text-emerald-500 dark:text-emerald-400'
                    : d.status === 'error'
                      ? 'bg-red-50 dark:bg-red-500/10 text-red-500 dark:text-red-400'
                      : 'bg-muted text-muted-foreground',
                )}
              >
                {d.status === 'ok' ? (
                  <CheckCircle2 className="w-4 h-4" />
                ) : d.status === 'error' ? (
                  <AlertCircle className="w-4 h-4" />
                ) : (
                  <Clock className="w-4 h-4" />
                )}
              </span>
              <div className="min-w-0 flex-1">
                <div className="text-[13.5px] font-medium text-foreground truncate">{d.name}</div>
                <div className="text-[12px] text-muted-foreground mt-0.5 truncate">
                  {d.type}
                  {d.message ? ` · ${d.message}` : ''}
                </div>
              </div>
              <span className="text-[11.5px] text-muted-foreground shrink-0">
                {d.status}
                {d.elapsed != null ? ` · ${d.elapsed}s` : ''}
              </span>
            </div>
          ))}
          {recompile.status === 'running' && recompile.docs.length === 0 && (
            <div className="text-[12.5px] text-muted-foreground">正在准备…</div>
          )}
        </div>
      )}
    </div>
  )
}
