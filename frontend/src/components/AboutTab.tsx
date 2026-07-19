import { useEffect, useState } from 'react'
import { BookOpen, ExternalLink, Github, Globe, Rocket } from 'lucide-react'
import { getMeta } from '@/api/meta'

const LINKS: { label: string; href: string; icon: typeof Globe }[] = [
  { label: '官网 openkb.ai', href: 'https://openkb.ai', icon: Globe },
  { label: 'GitHub 仓库', href: 'https://github.com/VectifyAI/OpenKB', icon: Github },
  { label: 'PageIndex（底层检索引擎）', href: 'https://github.com/VectifyAI/PageIndex', icon: Github },
  { label: '文档 docs.pageindex.ai', href: 'https://docs.pageindex.ai', icon: BookOpen },
  { label: 'Vectify AI（公司）', href: 'https://vectify.ai', icon: Globe },
]

export default function AboutTab() {
  const [version, setVersion] = useState<string | null>(null)
  useEffect(() => {
    let cancelled = false
    getMeta()
      .then((m) => { if (!cancelled) setVersion(m.version) })
      .catch(() => { /* leave as null → shows — */ })
    return () => { cancelled = true }
  }, [])

  return (
    <div className="mt-5 max-w-[560px] space-y-6 anim-fade-up">
      <div className="flex items-center gap-3">
        <span className="grid h-11 w-11 place-items-center rounded-2xl bg-accent-brand text-[18px] font-black text-white shrink-0">K</span>
        <div className="min-w-0">
          <div className="text-[16px] font-bold text-foreground">OpenKB</div>
          <div className="text-[12.5px] text-muted-foreground">
            Open LLM Knowledge Base · <span className="tabular-nums">v{version ?? '—'}</span> · Apache-2.0
          </div>
        </div>
      </div>

      <p className="text-[13.5px] leading-relaxed text-muted-foreground">
        把原始文档编译成结构化、互相链接的 wiki 式知识库，由 PageIndex 的无向量、基于推理的长文档检索驱动。
      </p>
      <p className="text-[12.5px] text-muted-foreground">由 Vectify AI 打造 · 产品线 PageIndex</p>

      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {LINKS.map((l) => (
          <a
            key={l.href}
            href={l.href}
            target="_blank"
            rel="noopener noreferrer"
            className="group inline-flex items-center gap-2 rounded-apple-md border border-[hsl(var(--glass-border))] glass-2 px-3 py-2 text-[13px] text-foreground transition hover:shadow-glass"
          >
            <l.icon className="w-4 h-4 shrink-0 text-muted-foreground transition-colors group-hover:text-accent-brand" />
            <span className="truncate">{l.label}</span>
            <ExternalLink className="ml-auto w-3.5 h-3.5 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
          </a>
        ))}
      </div>

      <a
        href="https://github.com/VectifyAI/OpenKB/releases"
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex items-center gap-1.5 text-[13px] font-medium text-accent-brand hover:underline"
      >
        <Rocket className="w-4 h-4" /> 查看最新版本 →
      </a>
    </div>
  )
}
