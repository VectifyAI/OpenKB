import { Cloud, HardDrive, ThumbsUp, type LucideIcon } from "lucide-react"

/**
 * Remote document-source connectors are NOT implemented (no OAuth/S3/sync
 * backend). Rather than dead "coming soon" tiles, each card links to a GitHub
 * issue search where users 👍 to signal demand — a telemetry-free, OSS-native
 * way to prioritize. The copy MUST never imply a connector is connected.
 *
 * `voteUrl` is an interim per-connector label search on the canonical repo; swap
 * to a specific `.../issues/<N>` once a tracking issue exists (one-line change).
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

export const CONNECTORS: Connector[] = [
  { id: "gdrive", label: "Google Drive", icon: Cloud, voteUrl: voteSearch("gdrive") },
  { id: "s3", label: "Amazon S3", icon: HardDrive, voteUrl: voteSearch("s3") },
  { id: "onedrive", label: "OneDrive", icon: Cloud, voteUrl: voteSearch("onedrive") },
]

/** Grid of connector "want this?" cards linking to GitHub demand-vote searches. */
export default function ConnectorCards() {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5">
      {CONNECTORS.map((c) => (
        <a
          key={c.id}
          href={c.voteUrl}
          target="_blank"
          rel="noopener noreferrer"
          title="云端同步开发中 · 去 GitHub 投票表达需求"
          className="group rounded-2xl border border-[hsl(var(--glass-border))] glass px-4 py-3.5 flex items-center gap-3 hover:shadow-glass hover:-translate-y-0.5 transition duration-fast ease-out-apple active:scale-[0.99]"
        >
          <span className="w-9 h-9 rounded-xl glass-2 border border-[hsl(var(--glass-border))] grid place-items-center shrink-0">
            <c.icon className="w-4 h-4 text-muted-foreground group-hover:text-accent-brand transition-colors" />
          </span>
          <div className="min-w-0 flex-1">
            <div className="text-[13px] font-medium text-foreground truncate">{c.label}</div>
            <div className="text-[11.5px] text-muted-foreground mt-0.5 inline-flex items-center gap-1">
              <ThumbsUp className="w-3 h-3" />
              想要它？去投票
            </div>
          </div>
        </a>
      ))}
    </div>
  )
}
