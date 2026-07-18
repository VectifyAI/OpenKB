import { useCallback, useEffect, useMemo, useState } from 'react'
import { Cpu, FolderCog, Cloud, HardDrive, KeyRound, Loader2, Save, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import {
  listKbs, getKbConfig, patchKbConfig,
  type KbSummary, type KbConfig, type KbConfigPatch,
} from '@/api/kb'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import { cn } from '@/lib/utils'

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e))

const subtabs = [
  { id: 'model', label: '模型', icon: Cpu },
  { id: 'general', label: '通用', icon: FolderCog },
  { id: 'conn', label: '数据源连接', icon: Cloud },
] as const

/**
 * Remote connectors are NOT implemented — there is no OAuth/S3 backend. These
 * render as disabled "coming soon" cards so the UI never fakes a connected or
 * authorized state (the reference's fake OAuth flow is intentionally dropped).
 */
const connectors = [
  { id: 'gdrive', label: 'Google Drive', icon: Cloud },
  { id: 's3', label: 'Amazon S3', icon: HardDrive },
  { id: 'onedrive', label: 'OneDrive', icon: Cloud },
] as const

const inputCls =
  'mt-1.5 w-full h-9 rounded-md border border-input bg-transparent px-3 text-[13px] font-mono2 outline-none focus:border-blue-400'

export default function Settings() {
  const [tab, setTab] = useState<string>('model')
  const [kbs, setKbs] = useState<KbSummary[]>([])
  const [kb, setKb] = useState<string>('')

  // Last-fetched baseline. The editable form fields below are diffed against
  // this to build a minimal merge-patch on save.
  const [config, setConfig] = useState<KbConfig | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  // Editable form state.
  const [model, setModel] = useState('')
  const [language, setLanguage] = useState('')
  const [threshold, setThreshold] = useState('')
  const [apiBase, setApiBase] = useState('')
  // The api_key input is WRITE-ONLY: it always starts empty (the GET never
  // returns a key value, only has_api_key). A non-empty value on save SETS/
  // rotates the key; leaving it empty leaves the stored key UNCHANGED.
  const [apiKeyInput, setApiKeyInput] = useState('')

  const [saving, setSaving] = useState(false)
  const [clearing, setClearing] = useState(false)

  // Load the KB list once; default the picker to the first KB.
  useEffect(() => {
    listKbs()
      .then((r) => {
        setKbs(r.knowledge_bases)
        setKb((cur) => cur || r.knowledge_bases[0]?.name || '')
      })
      .catch(() => setKbs([]))
  }, [])

  /** Set the baseline and repopulate the form from a fresh config. Always
   *  resets the api_key input to empty so the raw key value is never shown. */
  const applyConfig = useCallback((c: KbConfig) => {
    setConfig(c)
    setModel(c.model)
    setLanguage(c.language)
    setThreshold(String(c.pageindex_threshold))
    setApiBase(c.openai_api_base ?? '')
    setApiKeyInput('')
  }, [])

  // Fetch the selected KB's config on select/mount.
  useEffect(() => {
    if (!kb) return
    let cancelled = false
    setLoading(true)
    setLoadError(null)
    getKbConfig(kb)
      .then((c) => {
        if (cancelled) return
        applyConfig(c)
      })
      .catch((e) => {
        if (cancelled) return
        setConfig(null)
        setLoadError(errMsg(e))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [kb, applyConfig])

  /**
   * Diff the form against the baseline into a minimal merge-patch. The three
   * credential cases are produced EXACTLY: unchanged → key omitted (undefined,
   * dropped by JSON.stringify); set/rotate → the typed string; clearing is a
   * separate explicit action (see `clearApiKey`), never an empty string here.
   */
  const buildPatch = useCallback((): KbConfigPatch => {
    const patch: KbConfigPatch = {}
    if (!config) return patch

    const cfg: NonNullable<KbConfigPatch['config']> = {}
    const modelTrim = model.trim()
    if (modelTrim && modelTrim !== config.model) cfg.model = modelTrim
    const langTrim = language.trim()
    if (langTrim && langTrim !== config.language) cfg.language = langTrim
    const thrNum = Number(threshold)
    if (
      threshold.trim() !== '' &&
      Number.isInteger(thrNum) &&
      thrNum !== config.pageindex_threshold
    ) {
      cfg.pageindex_threshold = thrNum
    }
    if (Object.keys(cfg).length > 0) patch.config = cfg

    // openai_api_base is a plaintext config value: '' clears (null), a value
    // sets, an unchanged value is omitted.
    const baseTrim = apiBase.trim()
    const currentBase = config.openai_api_base ?? ''
    if (baseTrim !== currentBase) patch.openai_api_base = baseTrim === '' ? null : baseTrim

    // api_key: only SET when the user typed a value. Empty = unchanged (omit).
    if (apiKeyInput !== '') patch.api_key = apiKeyInput

    return patch
  }, [config, model, language, threshold, apiBase, apiKeyInput])

  const dirty = useMemo(() => Object.keys(buildPatch()).length > 0, [buildPatch])

  const save = useCallback(async () => {
    if (!kb || saving) return
    const patch = buildPatch()
    if (Object.keys(patch).length === 0) {
      toast.info('没有需要保存的更改')
      return
    }
    setSaving(true)
    try {
      const next = await patchKbConfig(kb, patch)
      applyConfig(next)
      toast.success('设置已保存')
    } catch (e) {
      toast.error(`保存失败：${errMsg(e)}`)
    } finally {
      setSaving(false)
    }
  }, [kb, saving, buildPatch, applyConfig])

  // Explicit clear: sends api_key: null (merge-patch clear), distinct from the
  // "leave unchanged" (omit) case that a save of an empty input produces.
  const clearApiKey = useCallback(async () => {
    if (!kb || clearing) return
    setClearing(true)
    try {
      const next = await patchKbConfig(kb, { api_key: null })
      applyConfig(next)
      toast.success('已清除 API Key')
    } catch (e) {
      toast.error(`清除失败：${errMsg(e)}`)
    } finally {
      setClearing(false)
    }
  }, [kb, clearing, applyConfig])

  const hasKey = config?.has_api_key === true

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-[780px] mx-auto px-6 py-8">
        <h1 className="text-[22px] font-extrabold tracking-tight text-neutral-900 anim-fade-up">设置</h1>

        {/* 知识库选择 — 配置是按知识库存储的（.openkb/config.yaml + .env） */}
        <div className="mt-4 flex items-center gap-3 anim-fade-up">
          <span className="text-[13px] text-neutral-500">知识库</span>
          {kbs.length > 0 ? (
            <Select value={kb} onValueChange={setKb}>
              <SelectTrigger className="h-9 w-64 text-[13px]">
                <SelectValue placeholder="选择知识库" />
              </SelectTrigger>
              <SelectContent>
                {kbs.map((k) => (
                  <SelectItem key={k.name} value={k.name}>
                    {k.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <span className="text-[13px] text-neutral-400">尚无知识库</span>
          )}
        </div>

        {/* 子页签 */}
        <div className="mt-5 flex gap-1.5 anim-fade-up anim-d1">
          {subtabs.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={cn(
                'inline-flex items-center gap-1.5 h-9 px-3.5 rounded-xl text-[13px] font-medium transition-colors',
                tab === t.id ? 'bg-neutral-900 text-white shadow-sm' : 'text-neutral-500 hover:bg-white hover:text-neutral-800',
              )}
            >
              <t.icon className="w-3.5 h-3.5" />
              {t.label}
            </button>
          ))}
        </div>

        {loadError && (
          <div className="mt-4 rounded-lg bg-red-50 border border-red-200/70 px-3 py-2 text-[12.5px] text-red-600">
            加载配置失败：{loadError}
          </div>
        )}

        {/* ---------- 模型 ---------- */}
        {tab === 'model' && (
          <div className="mt-5 space-y-4">
            <div className="anim-fade-up rounded-2xl border border-black/8 bg-white p-5">
              <div className="flex items-center gap-2 text-[14px] font-semibold text-neutral-800">
                <Cpu className="w-4 h-4 text-blue-600" />模型与凭证
              </div>
              <p className="mt-0.5 text-[12.5px] text-neutral-400">
                写入该知识库的 .openkb/config.yaml（model / 阈值）与 .env（API Key / base URL）
              </p>

              <div className="mt-4 grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label className="text-[12px] font-medium text-neutral-500">模型</label>
                  <input
                    value={model}
                    disabled={loading || !config}
                    onChange={(e) => setModel(e.target.value)}
                    placeholder="gpt-5.4"
                    className={inputCls}
                  />
                </div>
                <div>
                  <label className="text-[12px] font-medium text-neutral-500">PageIndex 阈值（页数）</label>
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
                <div className="sm:col-span-2">
                  <label className="text-[12px] font-medium text-neutral-500 flex items-center gap-1">
                    <KeyRound className="w-3 h-3" />API Key
                  </label>
                  <input
                    type="password"
                    value={apiKeyInput}
                    disabled={loading || !config}
                    autoComplete="new-password"
                    onChange={(e) => setApiKeyInput(e.target.value)}
                    placeholder={hasKey ? '已设置密钥 · 留空则保持不变' : '未设置 · 输入以启用'}
                    className={inputCls}
                  />
                  <div className="mt-1.5 flex items-center gap-2 text-[11.5px] text-neutral-400">
                    <span className={cn('inline-block w-1.5 h-1.5 rounded-full', hasKey ? 'bg-emerald-500' : 'bg-neutral-300')} />
                    {hasKey ? '已设置密钥（永不回显；输入新值即可轮换）' : '未设置密钥'}
                    {hasKey && (
                      <button
                        onClick={clearApiKey}
                        disabled={clearing}
                        className="ml-auto inline-flex items-center gap-1 h-7 px-2.5 rounded-lg border border-black/10 text-[12px] font-medium text-red-600 hover:bg-red-50 transition-colors disabled:opacity-60"
                      >
                        {clearing ? <Loader2 className="w-3 h-3 animate-spin" /> : <Trash2 className="w-3 h-3" />}
                        清除
                      </button>
                    )}
                  </div>
                </div>
                <div className="sm:col-span-2">
                  <label className="text-[12px] font-medium text-neutral-500">API Base URL（可选）</label>
                  <input
                    value={apiBase}
                    disabled={loading || !config}
                    onChange={(e) => setApiBase(e.target.value)}
                    placeholder="留空使用 provider 默认；本地/兼容端点时填写"
                    className={inputCls}
                  />
                </div>
              </div>
              <p className="mt-3 text-[12px] text-neutral-400">
                经 LiteLLM 路由，支持任意 provider/model 格式；本地运行时需填写 base URL。
              </p>
            </div>

            <SaveBar dirty={dirty} saving={saving} onSave={save} disabled={loading || !config} />
          </div>
        )}

        {/* ---------- 通用 ---------- */}
        {tab === 'general' && (
          <div className="mt-5 space-y-4">
            <div className="anim-fade-up rounded-2xl border border-black/8 bg-white p-5 space-y-5">
              <div>
                <label className="text-[13px] font-semibold text-neutral-800">Wiki 输出语言</label>
                <p className="mt-0.5 text-[12px] text-neutral-400">写入编译 prompt 的输出语言，例如 en / 中文 / 日本語</p>
                <input
                  value={language}
                  disabled={loading || !config}
                  onChange={(e) => setLanguage(e.target.value)}
                  placeholder="en"
                  className={cn(inputCls, 'max-w-[240px]')}
                />
              </div>
            </div>

            <SaveBar dirty={dirty} saving={saving} onSave={save} disabled={loading || !config} />

            <div className="anim-fade-up anim-d2 rounded-2xl border border-black/8 bg-white px-5 py-4 flex items-center gap-3 text-[12.5px] text-neutral-400">
              <span className="w-6 h-6 rounded-md bg-blue-600 text-white grid place-items-center text-[12px] font-extrabold">K</span>
              OpenKB Studio · 由 PageIndex 提供无向量化检索 · wiki 与你的数据始终留在本地
            </div>
          </div>
        )}

        {/* ---------- 数据源连接（无后端，明确标注即将推出，绝不伪造已连接） ---------- */}
        {tab === 'conn' && (
          <div className="mt-5 space-y-3">
            <p className="text-[13px] text-neutral-400 anim-fade-up">
              云端数据源连接器（OAuth / S3）尚未实现，敬请期待；当前仅支持在知识库详情页手动上传本地文件。
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5">
              {connectors.map((c) => (
                <div
                  key={c.id}
                  aria-disabled="true"
                  title="即将推出"
                  className="anim-fade-up rounded-2xl border border-dashed border-neutral-200 bg-neutral-50/60 px-4 py-3.5 flex items-center gap-3 opacity-70 cursor-not-allowed select-none"
                >
                  <span className="w-9 h-9 rounded-xl bg-white border border-black/5 grid place-items-center shrink-0">
                    <c.icon className="w-4 h-4 text-neutral-400" />
                  </span>
                  <div className="min-w-0">
                    <div className="text-[13px] font-medium text-neutral-500 truncate">{c.label}</div>
                    <div className="text-[11.5px] text-neutral-400 mt-0.5">即将推出</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
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
  return (
    <div className="flex items-center gap-3">
      <button
        onClick={onSave}
        disabled={disabled || saving || !dirty}
        className="inline-flex items-center gap-1.5 h-9 px-4 rounded-xl bg-blue-600 text-white text-[13px] font-medium hover:bg-blue-700 shadow-sm transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
        保存更改
      </button>
      {dirty && !saving && <span className="text-[12px] text-neutral-400">有未保存的更改</span>}
    </div>
  )
}
