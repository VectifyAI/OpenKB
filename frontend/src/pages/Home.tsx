import { useEffect, useState } from 'react'
import { listKbs, type KbSummary } from '@/api/kb'

export default function Home() {
  const [kbs, setKbs] = useState<KbSummary[]>([])

  useEffect(() => {
    listKbs()
      .then(r => setKbs(r.knowledge_bases))
      .catch(() => setKbs([]))
  }, [])

  const totalDocs = kbs.reduce((a, k) => a + k.document_count, 0)

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-[760px] mx-auto px-6 pt-[9vh] pb-16">
        {/* 问候 */}
        <div className="anim-fade-up">
          <h1 className="text-[30px] font-extrabold tracking-tight text-neutral-900">
            有什么想问的？
          </h1>
          <p className="mt-1.5 text-[14px] text-neutral-400">
            知识已编译就绪 · {totalDocs} 篇文档 · {kbs.length} 个知识库
          </p>
        </div>

        {/* 输入框：对话在 Task 5 接入，这里先用禁用占位 */}
        <div className="mt-7 anim-fade-up anim-d1">
          <div className="rounded-2xl border border-black/10 bg-white shadow-sm opacity-70">
            <textarea
              disabled
              rows={3}
              placeholder="对话功能即将上线…"
              className="w-full resize-none bg-transparent px-4 pt-3 pb-3 text-[15px] leading-relaxed outline-none placeholder:text-neutral-400 cursor-not-allowed"
            />
          </div>
        </div>

        {/* recent sessions: Task 5 */}

        <p className="mt-14 text-center text-[12px] text-neutral-300 anim-fade-up anim-d4">
          由 OpenKB 驱动 · wiki 是纯 Markdown，随时可用 Obsidian 打开
        </p>
      </div>
    </div>
  )
}
