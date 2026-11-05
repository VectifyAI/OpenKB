import { useCallback, useEffect, useState } from "react"
import { Cloud, HardDrive, ThumbsUp, type LucideIcon } from "lucide-react"
import { useTranslation } from "react-i18next"
import { ApiError, apiFetch } from "@/api/client"

/**
 * Remote document-source connectors.
 *
 * A provider with no backend keeps the GitHub vote link (`label:connector:<id>`).
 * Amazon S3 is shipped: with a knowledge base selected, zero bindings show a
 * connect form and one or more bindings show Sync / Remove / Add another.
 */
export interface Connector {
  id: string
  label: string
  icon: LucideIcon
  voteUrl: string
}

const REPO = "https://github.com/VectifyAI/OpenKB"
const voteSearch = (label: string) =>
  `${REPO}/issues?q=${encodeURIComponent(`is:issue label:connector:${label}`)}`

const SHIPPED = new Set(["s3"])

export const CONNECTORS: Connector[] = [
  { id: "gdrive", label: "Google Drive", icon: Cloud, voteUrl: voteSearch("gdrive") },
  { id: "s3", label: "Amazon S3", icon: HardDrive, voteUrl: voteSearch("s3") },
  { id: "onedrive", label: "OneDrive", icon: Cloud, voteUrl: voteSearch("onedrive") },
]

interface BindingView {
  name: string
  provider: string
  description: string
  stale: boolean
  error: string | null
}

const inputCls =
  "mt-1 w-full h-8 rounded-md border border-input bg-transparent px-2 text-[12px] font-mono2 outline-none focus-visible:ring-2 focus-visible:ring-ring"

function VoteCard({ connector }: { connector: Connector }) {
  const { t } = useTranslation("common")
  const Icon = connector.icon
  return (
    <a
      href={connector.voteUrl}
      target="_blank"
      rel="noopener noreferrer"
      title={t("connector.tooltip")}
      className="group rounded-2xl border border-[hsl(var(--glass-border))] glass px-4 py-3.5 flex items-center gap-3 hover:shadow-glass hover:-translate-y-0.5 transition duration-fast ease-out-apple active:scale-[0.99]"
    >
      <span className="w-9 h-9 rounded-xl glass-2 border border-[hsl(var(--glass-border))] grid place-items-center shrink-0">
        <Icon className="w-4 h-4 text-muted-foreground group-hover:text-accent-brand transition-colors" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="text-[13px] font-medium text-foreground truncate">{connector.label}</div>
        <div className="text-[11.5px] text-muted-foreground mt-0.5 inline-flex items-center gap-1">
          <ThumbsUp className="w-3 h-3" />
          {t("connector.vote")}
        </div>
      </div>
    </a>
  )
}

function S3Card({ kb }: { kb?: string }) {
  const { t } = useTranslation("common")
  const [bindings, setBindings] = useState<BindingView[]>([])
  const [adding, setAdding] = useState(false)
  const [pending, setPending] = useState<"save" | "sync" | "remove" | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [name, setName] = useState("")
  const [region, setRegion] = useState("")
  const [bucket, setBucket] = useState("")
  const [prefix, setPrefix] = useState("")

  const reload = useCallback(async () => {
    if (!kb) return
    const body = await apiFetch<{ connectors: BindingView[] }>(
      `/api/v1/connectors?kb=${encodeURIComponent(kb)}`,
    )
    setBindings(body.connectors.filter((item) => item.provider === "s3"))
  }, [kb])

  useEffect(() => {
    if (!kb) return
    let cancelled = false
    reload().catch((exc: unknown) => {
      if (!cancelled) setError(exc instanceof Error ? exc.message : String(exc))
    })
    return () => {
      cancelled = true
    }
  }, [kb, reload])

  const save = async () => {
    if (!kb) return
    setPending("save")
    setError(null)
    try {
      await apiFetch("/api/v1/connectors", {
        method: "POST",
        body: { kb, name, provider: "s3", region, bucket, prefix },
      })
      setName("")
      setRegion("")
      setBucket("")
      setPrefix("")
      setAdding(false)
      await reload()
    } catch (exc) {
      const message = exc instanceof ApiError ? exc.message : String(exc)
      setError(t("connector.formError", { error: message }))
    } finally {
      setPending(null)
    }
  }

  const syncOne = async (bindingName: string) => {
    if (!kb) return
    setPending("sync")
    setError(null)
    try {
      await apiFetch("/api/v1/connectors/sync", {
        method: "POST",
        body: { kb, name: bindingName, non_interactive: true },
      })
      await reload()
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc))
    } finally {
      setPending(null)
    }
  }

  const removeOne = async (bindingName: string) => {
    if (!kb) return
    setPending("remove")
    setError(null)
    try {
      await apiFetch("/api/v1/connectors/remove", {
        method: "POST",
        body: { kb, name: bindingName },
      })
      await reload()
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc))
    } finally {
      setPending(null)
    }
  }

  const showForm = Boolean(kb) && (bindings.length === 0 || adding)

  return (
    <div className="rounded-2xl border border-[hsl(var(--glass-border))] glass px-4 py-3.5">
      <div className="flex items-center gap-3">
        <span className="w-9 h-9 rounded-xl glass-2 border border-[hsl(var(--glass-border))] grid place-items-center shrink-0">
          <HardDrive className="w-4 h-4 text-muted-foreground" />
        </span>
        <div className="text-[13px] font-medium text-foreground">Amazon S3</div>
      </div>
      {!kb && <p className="mt-2 text-[12px] text-muted-foreground">{t("connector.needKb")}</p>}
      {kb && bindings.length > 0 && (
        <ul className="mt-3 space-y-2">
          {bindings.map((binding) => (
            <li key={binding.name} className="text-[12px]">
              <div className="font-medium text-foreground">{binding.name}</div>
              <div className="text-muted-foreground truncate">{binding.description}</div>
              <div className="text-muted-foreground">
                {binding.stale ? t("connector.notSynced") : t("connector.synced")}
              </div>
              {binding.error && <div className="text-red-600">{binding.error}</div>}
              <div className="mt-1 flex gap-2">
                <button
                  type="button"
                  disabled={pending !== null}
                  onClick={() => syncOne(binding.name)}
                  className="h-7 px-2 rounded-lg text-[12px] font-medium hover:bg-accent"
                >
                  {t("connector.sync")}
                </button>
                <button
                  type="button"
                  disabled={pending !== null}
                  onClick={() => removeOne(binding.name)}
                  className="h-7 px-2 rounded-lg text-[12px] font-medium text-red-600 hover:bg-red-50 dark:hover:bg-red-500/10"
                >
                  {t("connector.remove")}
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {kb && bindings.length > 0 && !adding && (
        <button
          type="button"
          className="mt-3 text-[12px] font-medium text-accent-brand"
          onClick={() => setAdding(true)}
        >
          {t("connector.addAnother")}
        </button>
      )}
      {showForm && (
        <form
          className="mt-3 space-y-2"
          onSubmit={(event) => {
            event.preventDefault()
            void save()
          }}
        >
          <label className="block text-[11.5px] text-muted-foreground">
            {t("connector.name")}
            <input className={inputCls} value={name} onChange={(event) => setName(event.target.value)} />
          </label>
          <label className="block text-[11.5px] text-muted-foreground">
            {t("connector.region")}
            <input className={inputCls} value={region} onChange={(event) => setRegion(event.target.value)} />
          </label>
          <label className="block text-[11.5px] text-muted-foreground">
            {t("connector.bucket")}
            <input className={inputCls} value={bucket} onChange={(event) => setBucket(event.target.value)} />
          </label>
          <label className="block text-[11.5px] text-muted-foreground">
            {t("connector.prefix")}
            <input className={inputCls} value={prefix} onChange={(event) => setPrefix(event.target.value)} />
            <span className="mt-0.5 block">{t("connector.prefixHint")}</span>
          </label>
          <button
            type="submit"
            disabled={pending !== null}
            className="h-8 px-3 rounded-lg text-[12px] font-medium bg-accent-brand text-white disabled:opacity-60"
          >
            {pending === "save" ? t("connector.saving") : t("connector.save")}
          </button>
        </form>
      )}
      {error && <p className="mt-2 text-[12px] text-red-600">{error}</p>}
    </div>
  )
}

/** Grid of connector cards. Pass `kb` on a knowledge-base page so S3 can bind. */
export default function ConnectorCards({ kb }: { kb?: string }) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5">
      {CONNECTORS.map((connector) =>
        SHIPPED.has(connector.id) ? (
          <S3Card key={connector.id} kb={kb} />
        ) : (
          <VoteCard key={connector.id} connector={connector} />
        ),
      )}
    </div>
  )
}
