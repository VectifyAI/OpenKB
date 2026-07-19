import React, { useEffect, useId, useRef, useState } from 'react'
import { useTheme } from '@/lib/theme'

/** 极简 Markdown 渲染：标题 / 列表 / 引用 / 粗体 / [[wikilink]] / 行内代码 / 代码块 / mermaid */
function inline(text: string, onWikiLink?: (target: string) => void): React.ReactNode[] {
  const parts: React.ReactNode[] = []
  const re = /(\[\[[^\]]+\]\]|\*\*[^*]+\*\*|`[^`]+`)/g
  let last = 0, m: RegExpExecArray | null, k = 0
  while ((m = re.exec(text))) {
    if (m.index > last) parts.push(text.slice(last, m.index))
    const tok = m[0]
    if (tok.startsWith('[[')) {
      const target = tok.slice(2, -2)
      // Only clickable when a handler is wired — never imply navigation that
      // does nothing (the previous bug: cursor-pointer with no onClick).
      parts.push(
        onWikiLink ? (
          <button
            key={k++}
            type="button"
            onClick={() => onWikiLink(target)}
            className="text-accent-brand bg-accent-brand/10 rounded px-1 py-px cursor-pointer hover:bg-accent-brand/20 transition-colors"
          >
            {target}
          </button>
        ) : (
          <span key={k++} className="text-accent-brand bg-accent-brand/10 rounded px-1 py-px transition-colors">
            {target}
          </span>
        ),
      )
    } else if (tok.startsWith('**')) {
      parts.push(<strong key={k++} className="font-semibold text-foreground">{tok.slice(2, -2)}</strong>)
    } else {
      parts.push(<code key={k++} className="font-mono2 text-[12px] bg-muted rounded px-1 py-px">{tok.slice(1, -1)}</code>)
    }
    last = m.index + tok.length
  }
  if (last < text.length) parts.push(text.slice(last))
  return parts
}

/** A fenced code block (non-mermaid): tokenized, horizontally scrollable. */
function CodeBox({ code, lang }: { code: string; lang?: string }) {
  return (
    <div className="my-3 overflow-hidden rounded-apple-md border border-[hsl(var(--glass-border))] bg-muted/50">
      {lang && (
        <div className="px-3.5 pt-2 text-[10.5px] uppercase tracking-wide text-muted-foreground">{lang}</div>
      )}
      <pre className="overflow-x-auto px-3.5 py-3 text-[12.5px] leading-relaxed">
        <code className="font-mono2 whitespace-pre text-foreground">{code}</code>
      </pre>
    </div>
  )
}

/**
 * Render a ```mermaid block as an SVG diagram. Mermaid is lazily imported (its
 * own bundle chunk, loaded only when a diagram appears). Theme-aware; on any
 * import/parse/render error, falls back to the raw source in a code box so a
 * malformed diagram never crashes the message.
 */
function MermaidBlock({ code }: { code: string }) {
  const { resolved } = useTheme()
  const ref = useRef<HTMLDivElement>(null)
  const [error, setError] = useState(false)
  const id = 'mmd-' + useId().replace(/[^a-zA-Z0-9]/g, '')

  useEffect(() => {
    let cancelled = false
    setError(false)
    import('mermaid')
      .then(async ({ default: mermaid }) => {
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: 'strict',
          theme: resolved === 'dark' ? 'dark' : 'default',
        })
        const { svg } = await mermaid.render(id, code)
        if (!cancelled && ref.current) ref.current.innerHTML = svg
      })
      .catch(() => {
        if (!cancelled) setError(true)
      })
    return () => {
      cancelled = true
    }
  }, [code, resolved, id])

  if (error) return <CodeBox code={code} lang="mermaid" />
  return (
    <div
      ref={ref}
      className="my-3 flex justify-center overflow-x-auto [&_svg]:h-auto [&_svg]:max-w-full"
    />
  )
}

export default function MarkdownView({
  source,
  onWikiLink,
}: {
  source: string
  /** Navigate to a `[[target]]` wikilink's page. Omit to render plain,
   *  non-interactive tokens (no `cursor-pointer` implying a dead click). */
  onWikiLink?: (target: string) => void
}) {
  const lines = source.split('\n')
  const out: React.ReactNode[] = []
  let list: string[] = []
  let key = 0

  const flushList = () => {
    if (!list.length) return
    out.push(
      <ul key={key++} className="my-2.5 space-y-1.5 pl-1">
        {list.map((li, i) => (
          <li key={i} className="flex gap-2 text-[14px] leading-relaxed text-muted-foreground">
            <span className="mt-[9px] w-1 h-1 rounded-full bg-muted-foreground shrink-0" />
            <span>{inline(li, onWikiLink)}</span>
          </li>
        ))}
      </ul>,
    )
    list = []
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trimEnd()

    // Fenced code block: ``` or ```lang … ``` (accumulate until the closing fence)
    const fence = /^```(\w*)\s*$/.exec(line.trim())
    if (fence) {
      flushList()
      const lang = fence[1]
      const body: string[] = []
      i++
      while (i < lines.length && !/^```\s*$/.test(lines[i].trim())) {
        body.push(lines[i])
        i++
      }
      // i now sits on the closing fence (or past the end if unterminated).
      const code = body.join('\n')
      if (lang === 'mermaid') out.push(<MermaidBlock key={key++} code={code} />)
      else out.push(<CodeBox key={key++} code={code} lang={lang || undefined} />)
      continue
    }

    if (line.startsWith('- ')) { list.push(line.slice(2)); continue }
    flushList()
    if (!line.trim()) { out.push(<div key={key++} className="h-2" />); continue }
    if (line.startsWith('### ')) out.push(<h3 key={key++} className="mt-4 mb-1.5 text-[14px] font-semibold text-foreground">{inline(line.slice(4), onWikiLink)}</h3>)
    else if (line.startsWith('## ')) out.push(<h2 key={key++} className="mt-5 mb-2 text-[16px] font-bold text-foreground">{inline(line.slice(3), onWikiLink)}</h2>)
    else if (line.startsWith('# ')) out.push(<h1 key={key++} className="mb-3 text-[22px] font-extrabold tracking-tight text-foreground">{inline(line.slice(2), onWikiLink)}</h1>)
    else if (line.startsWith('> ')) out.push(<div key={key++} className="my-2.5 border-l-2 border-amber-400/70 bg-amber-400/10 rounded-r-lg px-3 py-2 text-[13px] text-muted-foreground">{inline(line.slice(2), onWikiLink)}</div>)
    else if (/^\d+\.\s/.test(line)) out.push(<p key={key++} className="my-1 text-[14px] leading-relaxed text-muted-foreground pl-1">{inline(line, onWikiLink)}</p>)
    else out.push(<p key={key++} className="my-1.5 text-[14px] leading-relaxed text-muted-foreground">{inline(line, onWikiLink)}</p>)
  }
  flushList()
  return <div>{out}</div>
}
