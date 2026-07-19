import React from 'react'

/** 极简 Markdown 渲染：标题 / 列表 / 引用 / 粗体 / [[wikilink]] / 行内代码 */
function inline(text: string): React.ReactNode[] {
  const parts: React.ReactNode[] = []
  const re = /(\[\[[^\]]+\]\]|\*\*[^*]+\*\*|`[^`]+`)/g
  let last = 0, m: RegExpExecArray | null, k = 0
  while ((m = re.exec(text))) {
    if (m.index > last) parts.push(text.slice(last, m.index))
    const tok = m[0]
    if (tok.startsWith('[[')) {
      parts.push(
        <span key={k++} className="text-accent-brand bg-accent-brand/10 rounded px-1 py-px cursor-pointer hover:bg-accent-brand/20 transition-colors">
          {tok.slice(2, -2)}
        </span>,
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

export default function MarkdownView({ source }: { source: string }) {
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
            <span>{inline(li)}</span>
          </li>
        ))}
      </ul>,
    )
    list = []
  }

  for (const raw of lines) {
    const line = raw.trimEnd()
    if (line.startsWith('- ')) { list.push(line.slice(2)); continue }
    flushList()
    if (!line.trim()) { out.push(<div key={key++} className="h-2" />); continue }
    if (line.startsWith('### ')) out.push(<h3 key={key++} className="mt-4 mb-1.5 text-[14px] font-semibold text-foreground">{inline(line.slice(4))}</h3>)
    else if (line.startsWith('## ')) out.push(<h2 key={key++} className="mt-5 mb-2 text-[16px] font-bold text-foreground">{inline(line.slice(3))}</h2>)
    else if (line.startsWith('# ')) out.push(<h1 key={key++} className="mb-3 text-[22px] font-extrabold tracking-tight text-foreground">{inline(line.slice(2))}</h1>)
    else if (line.startsWith('> ')) out.push(<div key={key++} className="my-2.5 border-l-2 border-amber-400/70 bg-amber-400/10 rounded-r-lg px-3 py-2 text-[13px] text-muted-foreground">{inline(line.slice(2))}</div>)
    else if (/^\d+\.\s/.test(line)) out.push(<p key={key++} className="my-1 text-[14px] leading-relaxed text-muted-foreground pl-1">{inline(line)}</p>)
    else out.push(<p key={key++} className="my-1.5 text-[14px] leading-relaxed text-muted-foreground">{inline(line)}</p>)
  }
  flushList()
  return <div>{out}</div>
}
