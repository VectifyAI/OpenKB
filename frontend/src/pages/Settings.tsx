import { useCallback, useEffect, useMemo, useState } from 'react'
import { Cpu, FolderCog, Cloud, Info, Loader2, Save } from 'lucide-react'
import { toast } from 'sonner'
import { getGlobalConfig, patchGlobalConfig, type GlobalConfig } from '@/api/config'
import ConnectorCards from '@/components/ConnectorCards'
import AboutTab from '@/components/AboutTab'
import { cn } from '@/lib/utils'

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e))

const subtabs = [
  { id: 'model', label: '模型', icon: Cpu },
  { id: 'general', label: '通用', icon: FolderCog },
  { id: 'conn', label: '数据源连接', icon: Cloud },
  { id: 'about', label: '关于', icon: Info },
] as const

const inputCls =
  'mt-1.5 w-full h-9 rounded-md border border-input bg-transparent px-3 text-[13px] font-mono2 outline-none focus-visible:ring-2 focus-visible:ring-ring focus:border-accent-brand'

export default function Settings() {
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

  const [saving, setSaving] = useState(false)

  /** Set the baseline and repopulate the form from a fresh global config. */
  const applyConfig = useCallback((c: GlobalConfig) => {
    setConfig(c)
    setModel(c.model)
    setLanguage(c.language)
    setThreshold(String(c.pageindex_threshold))
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
   * Diff the form against the baseline into a minimal merge-patch. Only the
   * three global scalars are editable: an unchanged field is omitted
   * (undefined → dropped by JSON.stringify), a changed field carries its new
   * value. These required scalars are never cleared from this page, so `null`
   * (the RFC 7386 "revert to built-in default" signal) is intentionally never
   * emitted here — an empty input is treated as "no change", not a clear.
   */
  const buildPatch = useCallback(() => {
    const cfg: NonNullable<Parameters<typeof patchGlobalConfig>[0]['config']> = {}
    if (!config) return { cfg, dirty: false }
    const m = model.trim()
    if (m && m !== config.model) cfg.model = m
    const l = language.trim()
    if (l && l !== config.language) cfg.language = l
    const n = Number(threshold)
    if (threshold.trim() !== '' && Number.isInteger(n) && n !== config.pageindex_threshold) {
      cfg.pageindex_threshold = n
    }
    return { cfg, dirty: Object.keys(cfg).length > 0 }
  }, [config, model, language, threshold])

  const dirty = useMemo(() => buildPatch().dirty, [buildPatch])

  const save = useCallback(async () => {
    const { cfg, dirty } = buildPatch()
    if (!dirty) {
      toast.info('没有需要保存的更改')
      return
    }
    setSaving(true)
    try {
      applyConfig(await patchGlobalConfig({ config: cfg }))
      toast.success('全局默认已保存')
    } catch (e) {
      toast.error(`保存失败：${errMsg(e)}`)
    } finally {
      setSaving(false)
    }
  }, [buildPatch, applyConfig])

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-[1040px] mx-auto px-6 lg:px-8 py-8">
        <h1 className="text-[22px] font-extrabold tracking-tight text-foreground anim-fade-up">设置</h1>

        {/* 子页签 */}
        <div className="mt-5 flex gap-1.5 anim-fade-up anim-d1">
          {subtabs.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={cn(
                'inline-flex items-center gap-1.5 h-9 px-3.5 rounded-xl text-[13px] font-medium transition-colors',
                tab === t.id ? 'bg-primary text-primary-foreground shadow-sm' : 'text-muted-foreground hover:bg-accent hover:text-foreground',
              )}
            >
              <t.icon className="w-3.5 h-3.5" />
              {t.label}
            </button>
          ))}
        </div>

        {loadError && (
          <div className="mt-4 rounded-lg bg-red-50 dark:bg-red-500/10 border border-red-200/70 dark:border-red-500/25 px-3 py-2 text-[12.5px] text-red-600 dark:text-red-400">
            加载配置失败：{loadError}
          </div>
        )}

        {/* ---------- 模型 ---------- */}
        {tab === 'model' && (
          <div className="mt-5 space-y-4">
            <div className="anim-fade-up rounded-2xl border border-[hsl(var(--glass-border))] glass-2 p-5">
              <div className="flex items-center gap-2 text-[14px] font-semibold text-foreground">
                <Cpu className="w-4 h-4 text-accent-brand" />模型与阈值
              </div>
              <p className="mt-0.5 text-[12.5px] text-muted-foreground">
                全局默认模型与阈值（写入 ~/.config/openkb/global.yaml；各知识库可在其设置中继承或覆盖）
              </p>

              <div className="mt-4 grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label className="text-[12px] font-medium text-muted-foreground">模型</label>
                  <input
                    value={model}
                    disabled={loading || !config}
                    onChange={(e) => setModel(e.target.value)}
                    placeholder="gpt-5.4"
                    className={inputCls}
                  />
                </div>
                <div>
                  <label className="text-[12px] font-medium text-muted-foreground">PageIndex 阈值（页数）</label>
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

            <SaveBar dirty={dirty} saving={saving} onSave={save} disabled={loading || !config} />
          </div>
        )}

        {/* ---------- 通用 ---------- */}
        {tab === 'general' && (
          <div className="mt-5 space-y-4">
            <div className="anim-fade-up rounded-2xl border border-[hsl(var(--glass-border))] glass-2 p-5 space-y-5">
              <div>
                <label className="text-[13px] font-semibold text-foreground">Wiki 输出语言</label>
                <p className="mt-0.5 text-[12px] text-muted-foreground">写入编译 prompt 的输出语言，例如 en / 中文 / 日本語</p>
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

            <div className="anim-fade-up anim-d2 rounded-2xl border border-[hsl(var(--glass-border))] glass-2 px-5 py-4 flex items-center gap-3 text-[12.5px] text-muted-foreground">
              <span className="w-6 h-6 rounded-md bg-accent-brand text-white grid place-items-center text-[12px] font-extrabold">K</span>
              OpenKB Studio · 由 PageIndex 提供无向量化检索 · wiki 与你的数据始终留在本地
            </div>
          </div>
        )}

        {/* ---------- 数据源连接（无后端；改为 GitHub 需求投票，绝不伪造已连接） ---------- */}
        {tab === 'conn' && (
          <div className="mt-5 space-y-3">
            <p className="text-[13px] text-muted-foreground anim-fade-up">
              云端数据源连接器（OAuth / S3）开发中，尚不可用；当前请在知识库详情页手动上传本地文件。想要某个连接器？点下方卡片去 GitHub 投票，帮我们排优先级。
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
  return (
    <div className="flex items-center gap-3">
      <button
        onClick={onSave}
        disabled={disabled || saving || !dirty}
        className="inline-flex items-center gap-1.5 h-9 px-4 rounded-xl bg-accent-brand text-white text-[13px] font-medium hover:bg-accent-brand/90 shadow-sm transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
        保存更改
      </button>
      {dirty && !saving && <span className="text-[12px] text-muted-foreground">有未保存的更改</span>}
    </div>
  )
}
