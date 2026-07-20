import React, { useEffect, useId, useRef, useState } from 'react'
import katex from 'katex'
import 'katex/dist/katex.min.css'
import { useTheme } from '@/lib/theme'

/** Guard for [text](url) links: only render a real anchor for http(s) or
 *  scheme-less (relative) URLs. Anything with another scheme (javascript:,
 *  data:, vbscript:, …) is treated as literal text, never an anchor. */
const isSafeUrl = (u: string) => /^https?:\/\//i.test(u) || !/^[a-z][\w+.-]*:/i.test(u)

/**
 * Minimal inline Markdown renderer. Tokens (in match-priority order):
 *   [[wikilink|alias]] · \( math \) · **bold** · ~~strike~~ · `code` ·
 *   [text](url) · *italic* / _italic_.
 * ORDER IS LOAD-BEARING inside the alternation: wikilink must precede the link
 * pattern (so `[[x]]` is not parsed as a link), and bold must precede italic
 * (so `**x**` is not parsed as `*`-italic-`*`). Every alternative uses a bounded
 * `[^…]+` class (no nested quantifiers) to avoid catastrophic backtracking.
 */
function inline(text: string, onWikiLink?: (target: string) => void): React.ReactNode[] {
  const parts: React.ReactNode[] = []
  const re = /(\[\[[^\]]+\]\]|\\\([\s\S]+?\\\)|\*\*[^*]+\*\*|~~[^~]+~~|`[^`]+`|\[[^\]]+\]\([^)]+\)|\*[^*]+\*|_[^_]+_)/g
  let last = 0, m: RegExpExecArray | null, k = 0
  while ((m = re.exec(text))) {
    if (m.index > last) parts.push(text.slice(last, m.index))
    const tok = m[0]
    if (tok.startsWith('[[')) {
      // Obsidian/wiki alias form: [[target|alias]]. Split on the FIRST `|` —
      // the part before is the nav TARGET, the part after (if any) is the
      // DISPLAY label. Multiple `|` keep the rest in the label; an empty/absent
      // alias falls back to the target so we never render a blank link.
      const inner = tok.slice(2, -2)
      const pipe = inner.indexOf('|')
      const target = (pipe === -1 ? inner : inner.slice(0, pipe)).trim()
      const display = (pipe === -1 ? '' : inner.slice(pipe + 1)).trim() || target
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
            {display}
          </button>
        ) : (
          <span key={k++} className="text-accent-brand bg-accent-brand/10 rounded px-1 py-px transition-colors">
            {display}
          </span>
        ),
      )
    } else if (tok.startsWith('\\(')) {
      // Inline math \( … \) via KaTeX; on a KaTeX error fall back to a code chip.
      const tex = tok.slice(2, -2)
      let html = ''
      try {
        html = katex.renderToString(tex, { displayMode: false, throwOnError: false })
      } catch {
        html = ''
      }
      parts.push(
        html ? (
          <span key={k++} className="text-foreground" dangerouslySetInnerHTML={{ __html: html }} />
        ) : (
          <code key={k++} className="font-mono2 text-[12px] bg-muted rounded px-1 py-px">{tex}</code>
        ),
      )
    } else if (tok.startsWith('**')) {
      parts.push(<strong key={k++} className="font-semibold text-foreground">{tok.slice(2, -2)}</strong>)
    } else if (tok.startsWith('~~')) {
      parts.push(<del key={k++} className="line-through">{tok.slice(2, -2)}</del>)
    } else if (tok.startsWith('`')) {
      parts.push(<code key={k++} className="font-mono2 text-[12px] bg-muted rounded px-1 py-px">{tok.slice(1, -1)}</code>)
    } else if (tok.startsWith('[')) {
      // Inline link [text](url). `[[…]]` was already consumed above, so any
      // `[` reaching here is a genuine link. Only emit an anchor when the URL
      // passes the scheme guard; otherwise render the raw token as literal text.
      const lm = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(tok)
      const label = lm ? lm[1] : tok
      const url = lm ? lm[2] : ''
      parts.push(
        lm && isSafeUrl(url) ? (
          <a
            key={k++}
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-accent-brand hover:underline"
          >
            {label}
          </a>
        ) : (
          <span key={k++}>{tok}</span>
        ),
      )
    } else {
      // *italic* or _italic_ (bold `**…**` was consumed above).
      parts.push(<em key={k++} className="italic">{tok.slice(1, -1)}</em>)
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

/** Display (block) math via KaTeX. On a KaTeX error, falls back to the raw TeX
 *  in a code box so a malformed formula never crashes the message. */
function MathBlock({ tex }: { tex: string }) {
  let html = ''
  try {
    html = katex.renderToString(tex, { displayMode: true, throwOnError: false })
  } catch {
    return <CodeBox code={tex} />
  }
  return (
    <div
      className="my-3 overflow-x-auto text-center text-foreground"
      dangerouslySetInnerHTML={{ __html: html }}
    />
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
  let olist: string[] = []
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

  const flushOList = () => {
    if (!olist.length) return
    out.push(
      <ol key={key++} className="my-2.5 space-y-1.5 pl-6 list-decimal">
        {olist.map((li, i) => (
          <li key={i} className="pl-1 text-[14px] leading-relaxed text-muted-foreground marker:text-muted-foreground">
            <span>{inline(li, onWikiLink)}</span>
          </li>
        ))}
      </ol>,
    )
    olist = []
  }

  // Block boundary: any non-list line closes BOTH pending lists.
  const flushBlocks = () => { flushList(); flushOList() }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trimEnd()

    // Fenced code block: ``` or ```lang … ``` (accumulate until the closing fence)
    const fence = /^```(\w*)\s*$/.exec(line.trim())
    if (fence) {
      flushBlocks()
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

    // Block math: LaTeX \[ … \] display delimiters. Handle a single-line form
    // and the model's usual three-line form (\[ alone, body lines, \] alone).
    const oneLineMath = /^\\\[([\s\S]*?)\\\]$/.exec(line.trim())
    if (oneLineMath) {
      flushBlocks()
      out.push(<MathBlock key={key++} tex={oneLineMath[1].trim()} />)
      continue
    }
    if (line.trim() === '\\[') {
      flushBlocks()
      const body: string[] = []
      i++
      while (i < lines.length && lines[i].trim() !== '\\]') { body.push(lines[i]); i++ }
      out.push(<MathBlock key={key++} tex={body.join('\n').trim()} />)
      continue
    }

    // Horizontal rule / thematic break: ---, ***, or ___
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(line.trim())) {
      flushBlocks()
      out.push(<hr key={key++} className="my-4 border-0 border-t border-[hsl(var(--glass-border))]" />)
      continue
    }

    // Unordered item: close any open ordered list, keep accumulating the ul.
    if (line.startsWith('- ')) { flushOList(); list.push(line.slice(2)); continue }
    // Ordered item `N. …`: close any open unordered list, strip the marker, and
    // accumulate into a real <ol> (mirrors the ul buffer/flush pattern).
    const om = /^\d+\.\s+/.exec(line)
    if (om) { flushList(); olist.push(line.slice(om[0].length)); continue }
    flushBlocks()
    if (!line.trim()) { out.push(<div key={key++} className="h-2" />); continue }
    if (line.startsWith('### ')) out.push(<h3 key={key++} className="mt-4 mb-1.5 text-[14px] font-semibold text-foreground">{inline(line.slice(4), onWikiLink)}</h3>)
    else if (line.startsWith('## ')) out.push(<h2 key={key++} className="mt-5 mb-2 text-[16px] font-bold text-foreground">{inline(line.slice(3), onWikiLink)}</h2>)
    else if (line.startsWith('# ')) out.push(<h1 key={key++} className="mb-3 text-[22px] font-extrabold tracking-tight text-foreground">{inline(line.slice(2), onWikiLink)}</h1>)
    else if (line.startsWith('> ')) out.push(<div key={key++} className="my-2.5 border-l-2 border-amber-400/70 bg-amber-400/10 rounded-r-lg px-3 py-2 text-[13px] text-muted-foreground">{inline(line.slice(2), onWikiLink)}</div>)
    else out.push(<p key={key++} className="my-1.5 text-[14px] leading-relaxed text-muted-foreground">{inline(line, onWikiLink)}</p>)
  }
  flushBlocks()
  return <div>{out}</div>
}
