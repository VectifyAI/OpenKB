import { useState } from "react"
import { useNavigate } from "react-router"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import { Sparkles, KeyRound, FolderPlus, Loader2 } from "lucide-react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { UnLanguageDatalist, UN_LANG_LIST_ID } from "@/components/UnLanguageDatalist"
import { patchGlobalConfig, type GlobalConfigPatch } from "@/api/config"
import { createKb } from "@/api/kb"

const inputCls =
  "mt-1.5 w-full h-9 rounded-md border border-input bg-transparent px-3 text-[13px] font-mono2 outline-none focus-visible:ring-2 focus-visible:ring-ring focus:border-accent-brand"

/**
 * First-launch setup: welcome → connect the model (patchGlobalConfig) → create
 * the first knowledge base (createKb). Shown when the global config has no API
 * key configured yet. "Skip for now" dismisses it.
 */
export default function Onboarding({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation("common")
  const navigate = useNavigate()
  const [step, setStep] = useState(1)
  const [model, setModel] = useState("gpt-5.4")
  const [language, setLanguage] = useState("")
  const [apiKey, setApiKey] = useState("")
  const [baseUrl, setBaseUrl] = useState("")
  const [kbName, setKbName] = useState("my-kb")
  const [busy, setBusy] = useState(false)

  const errText = (e: unknown) => (e instanceof Error ? e.message : String(e))

  const saveConfig = async () => {
    setBusy(true)
    try {
      const patch: GlobalConfigPatch = { config: { model: model.trim() } }
      if (language.trim()) patch.config!.language = language.trim()
      if (apiKey.trim()) patch.api_key = apiKey.trim()
      if (baseUrl.trim()) patch.openai_api_base = baseUrl.trim()
      await patchGlobalConfig(patch)
      setStep(3)
    } catch (e) {
      toast.error(errText(e))
    } finally {
      setBusy(false)
    }
  }

  const createFirstKb = async () => {
    const name = kbName.trim()
    if (!name) return
    setBusy(true)
    try {
      await createKb({ kb: name })
      toast.success(t("onboarding.createdToast", { name }))
      onClose()
      navigate(`/kb/${encodeURIComponent(name)}`)
    } catch (e) {
      toast.error(errText(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) onClose()
      }}
    >
      <DialogContent className="sm:max-w-md">
        {step === 1 && (
          <>
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <Sparkles className="w-4 h-4 text-accent-brand" />
                {t("onboarding.welcomeTitle")}
              </DialogTitle>
              <DialogDescription>{t("onboarding.welcomeBody")}</DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="ghost" onClick={onClose}>
                {t("onboarding.skip")}
              </Button>
              <Button onClick={() => setStep(2)}>{t("onboarding.getStarted")}</Button>
            </DialogFooter>
          </>
        )}

        {step === 2 && (
          <>
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <KeyRound className="w-4 h-4 text-accent-brand" />
                {t("onboarding.configTitle")}
              </DialogTitle>
              <DialogDescription>{t("onboarding.configBody")}</DialogDescription>
            </DialogHeader>
            <div className="space-y-3">
              <div>
                <label className="text-[12px] font-medium text-muted-foreground">
                  {t("onboarding.modelLabel")}
                </label>
                <input value={model} onChange={(e) => setModel(e.target.value)} className={inputCls} />
              </div>
              <div>
                <label className="text-[12px] font-medium text-muted-foreground">
                  {t("onboarding.langLabel")}
                </label>
                <input
                  value={language}
                  onChange={(e) => setLanguage(e.target.value)}
                  list={UN_LANG_LIST_ID}
                  placeholder="English"
                  className={inputCls}
                />
                <UnLanguageDatalist />
              </div>
              <div>
                <label className="text-[12px] font-medium text-muted-foreground">
                  {t("onboarding.apiKeyLabel")}
                </label>
                <input
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder={t("onboarding.apiKeyPlaceholder")}
                  className={inputCls}
                />
              </div>
              <div>
                <label className="text-[12px] font-medium text-muted-foreground">
                  {t("onboarding.baseUrlLabel")}
                </label>
                <input
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                  placeholder={t("onboarding.baseUrlPlaceholder")}
                  className={inputCls}
                />
              </div>
            </div>
            <DialogFooter>
              <Button variant="ghost" onClick={() => setStep(1)}>
                {t("onboarding.back")}
              </Button>
              <Button onClick={saveConfig} disabled={busy}>
                {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
                {t("onboarding.continue")}
              </Button>
            </DialogFooter>
          </>
        )}

        {step === 3 && (
          <>
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <FolderPlus className="w-4 h-4 text-accent-brand" />
                {t("onboarding.kbTitle")}
              </DialogTitle>
              <DialogDescription>{t("onboarding.kbBody")}</DialogDescription>
            </DialogHeader>
            <div>
              <label className="text-[12px] font-medium text-muted-foreground">
                {t("onboarding.kbNameLabel")}
              </label>
              <input
                value={kbName}
                onChange={(e) => setKbName(e.target.value)}
                placeholder="my-kb"
                className={inputCls}
              />
            </div>
            <DialogFooter>
              <Button variant="ghost" onClick={() => setStep(2)}>
                {t("onboarding.back")}
              </Button>
              <Button onClick={createFirstKb} disabled={busy || !kbName.trim()}>
                {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
                {t("onboarding.createFinish")}
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}
