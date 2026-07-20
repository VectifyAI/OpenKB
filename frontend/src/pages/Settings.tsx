import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Cpu, FolderCog, Cloud, Info, Loader2, Save, KeyRound, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { getGlobalConfig, patchGlobalConfig, type GlobalConfig, type GlobalConfigPatch } from '@/api/config'
import ConnectorCards from '@/components/ConnectorCards'
import AboutTab from '@/components/AboutTab'
import { cn } from '@/lib/utils'
import { UnLanguageDatalist, UN_LANG_LIST_ID } from '@/components/UnLanguageDatalist'

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e))

// Labels resolve at render time via `t(\`settings:tabs.${id}\`)`; `id` is code.
const subtabs = [
  { id: 'model', icon: Cpu },
  { id: 'general', icon: FolderCog },
  { id: 'conn', icon: Cloud },
  { id: 'about', icon: Info },
] as const

const inputCls =
  'mt-1.5 w-full h-9 rounded-md border border-input bg-transparent px-3 text-[13px] font-mono2 outline-none focus-visible:ring-2 focus-visible:ring-ring focus:border-accent-brand'

export default function Settings() {
  // 'kbSettings' is pulled in to REUSE the per-KB gear's credential copy
  // (keyPlaceholder*/keyHint*/clear/baseLabel/basePlaceholder) verbatim, so the
  // global and per-KB credential UIs read identically. This only references
  // those keys; it does not own or modify the kbSettings namespace.
  const { t } = useTranslation(['settings', 'common', 'kbSettings'])
  const [tab, setTab] = useState<string>('model')

  // Last-fetched baseline. The editable form fields below are diffed against
  // this to build a minimal merge-patch on save.
  const [config, setConfig] = useState<GlobalConfig | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  // Editable form state.
  const [model, setModel] = useState('')
  const [language, setLanguage] = useState('')
  const [threshold, setThreshold] = useState('')
  // Global-default credentials (written to ~/.config/openkb/.env). The API key
  // is write-only: its value is never fetched, so the input starts empty and a
  // non-empty value means "rotate". `clearKey` defers an explicit-null removal
  // of an existing key into the same save (never echoing the value).
  const [apiKey, setApiKey] = useState('')
  const [apiBase, setApiBase] = useState('')
  const [clearKey, setClearKey] = useState(false)

  const [saving, setSaving] = useState(false)

  /** Set the baseline and repopulate the form from a fresh global config. */
  const applyConfig = useCallback((c: GlobalConfig) => {
    setConfig(c)
    setModel(c.model)
    setLanguage(c.language)
    setThreshold(String(c.pageindex_threshold))
    setApiBase(c.openai_api_base ?? '')
    setApiKey('')
    setClearKey(false)
  }, [])

  // Fetch the global defaults once on mount.
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setLoadError(null)
    getGlobalConfig()
      .then((c) => !cancelled && applyConfig(c))
      .catch((e) => !cancelled && setLoadError(errMsg(e)))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [applyConfig])

  /**
   * Diff the form against the baseline into a minimal merge-patch. The three
   * scalars are required, so an empty input is "no change", never a clear (they
   * never emit `null`). Credentials follow the per-KB gear's discipline: an
   * unchanged base is omitted, emptying a previously-set base clears it (null),
   * the api_key is sent only when the user typed one (rotate) or as an explicit
   * `null` when the user asked to clear an existing key. `api_key: ""` is never
   * sent (that would set an empty key). An omitted field is dropped by
   * JSON.stringify and left unchanged server-side (RFC 7386).
   */
  const buildPatch = useCallback(() => {
    const patch: GlobalConfigPatch = {}
    if (!config) return { patch, dirty: false }
    const cfg: NonNullable<GlobalConfigPatch['config']> = {}
    const m = model.trim()
    if (m && m !== config.model) cfg.model = m
    const l = language.trim()
    if (l && l !== config.language) cfg.language = l
    const n = Number(threshold)
    if (threshold.trim() !== '' && Number.isInteger(n) && n !== config.pageindex_threshold) {
      cfg.pageindex_threshold = n
    }
    if (Object.keys(cfg).length > 0) patch.config = cfg
    const baseTrim = apiBase.trim()
    const currentBase = config.openai_api_base ?? ''
    if (baseTrim !== currentBase) patch.openai_api_base = baseTrim === '' ? null : baseTrim
    if (clearKey) patch.api_key = null
    else if (apiKey !== '') patch.api_key = apiKey
    return { patch, dirty: Object.keys(patch).length > 0 }
  }, [config, model, language, threshold, apiBase, apiKey, clearKey])

  const dirty = useMemo(() => buildPatch().dirty, [buildPatch])

  const save = useCallback(async () => {
    const { patch, dirty } = buildPatch()
    if (!dirty) {
      toast.info(t('common:noChanges'))
      return
    }
    setSaving(true)
    try {
      applyConfig(await patchGlobalConfig(patch))
      toast.success(t('settings:savedToast'))
    } catch (e) {
      toast.error(t('common:saveError', { error: errMsg(e) }))
    } finally {
      setSaving(false)
    }
  }, [buildPatch, applyConfig, t])

  const hasKey = !!config?.has_api_key

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-[1040px] mx-auto px-6 lg:px-8 py-8">
        <h1 className="text-[22px] font-extrabold tracking-tight text-foreground anim-fade-up">{t('common:nav.settings')}</h1>

        {/* 子页签 */}
        <div className="mt-5 flex gap-1.5 anim-fade-up anim-d1">
          {subtabs.map((st) => (
            <button
              key={st.id}
              onClick={() => setTab(st.id)}
              className={cn(
                'inline-flex items-center gap-1.5 h-9 px-3.5 rounded-xl text-[13px] font-medium transition-colors',
                tab === st.id ? 'bg-primary text-primary-foreground shadow-sm' : 'text-muted-foreground hover:bg-accent hover:text-foreground',
              )}
            >
              <st.icon className="w-3.5 h-3.5" />
              {t(`settings:tabs.${st.id}`)}
            </button>
          ))}
        </div>

        {loadError && (
          <div className="mt-4 rounded-lg bg-red-50 dark:bg-red-500/10 border border-red-200/70 dark:border-red-500/25 px-3 py-2 text-[12.5px] text-red-600 dark:text-red-400">
            {t('common:configLoadError', { error: loadError })}
          </div>
        )}

        {/* ---------- 模型 ---------- */}
        {tab === 'model' && (
          <div className="mt-5 space-y-4">
            <div className="anim-fade-up rounded-2xl border border-[hsl(var(--glass-border))] glass-2 p-5">
              <div className="flex items-center gap-2 text-[14px] font-semibold text-foreground">
                <Cpu className="w-4 h-4 text-accent-brand" />{t('settings:modelSection.title')}
              </div>
              <p className="mt-0.5 text-[12.5px] text-muted-foreground">
                {t('settings:modelSection.desc')}
              </p>

              <div className="mt-4 grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label className="text-[12px] font-medium text-muted-foreground">{t('common:fields.model')}</label>
                  <input
                    value={model}
                    disabled={loading || !config}
                    onChange={(e) => setModel(e.target.value)}
                    placeholder="gpt-5.4"
                    className={inputCls}
                  />
                </div>
                <div>
                  <label className="text-[12px] font-medium text-muted-foreground">{t('common:fields.threshold')}</label>
                  <input
                    type="number"
                    min={1}
                    value={threshold}
                    disabled={loading || !config}
                    onChange={(e) => setThreshold(e.target.value)}
                    placeholder="20"
                    className={inputCls}
                  />
                </div>
              </div>
            </div>

            {/* 全局默认凭证（写入 ~/.config/openkb/.env） */}
            <div className="anim-fade-up anim-d1 rounded-2xl border border-[hsl(var(--glass-border))] glass-2 p-5">
              <div className="flex items-center gap-2 text-[14px] font-semibold text-foreground">
                <KeyRound className="w-4 h-4 text-accent-brand" />{t('settings:credSection.title')}
              </div>
              <p className="mt-0.5 text-[12.5px] text-muted-foreground">
                {t('settings:credSection.desc')}
              </p>

              <div className="mt-4 space-y-4">
                <div>
                  <label className="text-[12px] font-medium text-muted-foreground flex items-center gap-1">
                    <KeyRound className="w-3 h-3" />API Key
                  </label>
                  <input
                    type="password"
                    value={apiKey}
                    autoComplete="new-password"
                    disabled={loading || !config}
                    onChange={(e) => {
                      setApiKey(e.target.value)
                      if (e.target.value !== '') setClearKey(false)
                    }}
                    placeholder={hasKey ? t('kbSettings:keyPlaceholderSet') : t('kbSettings:keyPlaceholderUnset')}
                    className={inputCls}
                  />
                  <div className="mt-1.5 flex items-center gap-2 text-[11.5px] text-muted-foreground">
                    <span className={cn('inline-block w-1.5 h-1.5 rounded-full', hasKey && !clearKey ? 'bg-emerald-500' : 'bg-muted-foreground/40')} />
                    {clearKey
                      ? t('settings:credSection.clearPending')
                      : hasKey
                        ? t('kbSettings:keyHintSet')
                        : t('kbSettings:keyHintUnset')}
                    {hasKey && !clearKey && apiKey === '' && (
                      <button
                        type="button"
                        onClick={() => setClearKey(true)}
                        disabled={loading || !config}
                        className="ml-auto inline-flex items-center gap-1 h-7 px-2.5 rounded-lg border border-[hsl(var(--glass-border))] text-[12px] font-medium text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-500/10 transition-colors disabled:opacity-60"
                      >
                        <Trash2 className="w-3 h-3" />{t('kbSettings:clear')}
                      </button>
                    )}
                    {clearKey && (
                      <button
                        type="button"
                        onClick={() => setClearKey(false)}
                        className="ml-auto inline-flex items-center h-7 px-2.5 rounded-lg border border-[hsl(var(--glass-border))] text-[12px] font-medium text-muted-foreground hover:bg-accent transition-colors"
                      >
                        {t('settings:credSection.undoClear')}
                      </button>
                    )}
                  </div>
                </div>
                <div>
                  <label className="text-[12px] font-medium text-muted-foreground">{t('kbSettings:baseLabel')}</label>
                  <input
                    value={apiBase}
                    disabled={loading || !config}
                    onChange={(e) => setApiBase(e.target.value)}
                    placeholder={t('kbSettings:basePlaceholder')}
                    className={inputCls}
                  />
                </div>
              </div>
            </div>

            <SaveBar dirty={dirty} saving={saving} onSave={save} disabled={loading || !config} />
          </div>
        )}

        {/* ---------- 通用 ---------- */}
        {tab === 'general' && (
          <div className="mt-5 space-y-4">
            <div className="anim-fade-up rounded-2xl border border-[hsl(var(--glass-border))] glass-2 p-5 space-y-5">
              <div>
                <label className="text-[13px] font-semibold text-foreground">{t('common:fields.wikiLanguage')}</label>
                <p className="mt-0.5 text-[12px] text-muted-foreground">{t('settings:general.langDesc')}</p>
                <input
                  list={UN_LANG_LIST_ID}
                  value={language}
                  disabled={loading || !config}
                  onChange={(e) => setLanguage(e.target.value)}
                  placeholder="en"
                  className={cn(inputCls, 'max-w-[240px]')}
                />
                <UnLanguageDatalist />
              </div>
            </div>

            <SaveBar dirty={dirty} saving={saving} onSave={save} disabled={loading || !config} />
          </div>
        )}

        {/* ---------- 数据源连接（无后端；改为 GitHub 需求投票，绝不伪造已连接） ---------- */}
        {tab === 'conn' && (
          <div className="mt-5 space-y-3">
            <p className="text-[13px] text-muted-foreground anim-fade-up">
              {t('settings:conn.note')}
            </p>
            <ConnectorCards />
          </div>
        )}

        {/* ---------- 关于 ---------- */}
        {tab === 'about' && <AboutTab />}
      </div>
    </div>
  )
}

function SaveBar({
  dirty, saving, disabled, onSave,
}: {
  dirty: boolean
  saving: boolean
  disabled: boolean
  onSave: () => void
}) {
  const { t } = useTranslation('settings')
  return (
    <div className="flex items-center gap-3">
      <button
        onClick={onSave}
        disabled={disabled || saving || !dirty}
        className="inline-flex items-center gap-1.5 h-9 px-4 rounded-xl bg-accent-brand text-white text-[13px] font-medium hover:bg-accent-brand/90 shadow-sm transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
        {t('saveChanges')}
      </button>
      {dirty && !saving && <span className="text-[12px] text-muted-foreground">{t('unsaved')}</span>}
    </div>
  )
}
