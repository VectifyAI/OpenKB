// Lightweight i18n: two languages (en default, zh) with a localStorage toggle.
// Every user-visible string lives in the STRINGS map below. Components call
// useI18n() to get a `t(key)` function bound to the current language.

import { createContext, useContext, useCallback, useState } from "react";

const LS_LANG = "openkb_lang";

const STRINGS = {
  // Sidebar / nav
  overview: { en: "Overview", zh: "概述" },
  documents: { en: "Documents", zh: "文档" },
  query: { en: "Query", zh: "查询" },
  chat: { en: "Chat", zh: "对话" },
  maintenance: { en: "Maintenance", zh: "维护" },
  noKbSelected: { en: "No KB selected", zh: "未选择知识库" },
  selectOrCreate: { en: "Select or create a knowledge base from the sidebar.", zh: "请在左侧选择或新建一个知识库。" },
  noKbs: { en: "No knowledge bases yet", zh: "暂无知识库" },
  docs: { en: "docs", zh: "篇" },
  newKb: { en: "New KB", zh: "新建知识库" },
  newKbPrompt: { en: "New KB name (letters/numbers/-/_):", zh: "新知识库名称（字母/数字/下划线/连字符）：" },
  created: { en: "Created", zh: "已创建" },
  connectionSettings: { en: "Connection settings", zh: "连接设置" },
  menu: { en: "Menu", zh: "菜单" },
  appTitle: { en: "OpenKB · Knowledge Workbench", zh: "OpenKB · 知识工作台" },
  // Documents
  dragDrop: { en: "Drag files here, or click to browse", zh: "拖拽文件到此处，或点击选择" },
  supportedTypes: { en: "Supported: PDF, DOCX, MD, TXT, and more", zh: "支持：PDF、DOCX、MD、TXT 等" },
  docName: { en: "Name", zh: "名称" },
 docStatus: { en: "Status", zh: "状态" },
  docType: { en: "Type", zh: "类型" },
 docActions: { en: "Actions", zh: "操作" },
  delete: { en: "Delete", zh: "删除" },
  confirmDelete: { en: "Delete this document?", zh: "删除此文档？" },
  uploadSuccess: { en: "Upload complete", zh: "上传完成" },
  // Query / Chat
  askKb: { en: "Ask the knowledge base", zh: "向知识库提问" },
  vectorlessRetrieval: { en: "Reasoning-based retrieval", zh: "基于无向量推理检索" },
  answerWithReasoning: { en: "Answer with retrieval timeline", zh: "答案附推理过程" },
  typeQuestion: { en: "Ask anything...", zh: "例如：这篇文章的主要结论是什么？" },
 typeToContinue: { en: "Continue the conversation...", zh: "继续对话…" },
 you: { en: "You", zh: "你" },
 multiTurn: { en: "Multi-turn chat", zh: "多轮对话" },
 chatPersist: { en: "Sessions persist and can be resumed.", zh: "会话自动持久化，可跨次恢复。" },
 newSession: { en: "New session", zh: "本次新会话" },
 retrieve: { en: "Retrieval", zh: "检索" },
 turnCount: { en: "Turn {n}", zh: "第 {n} 轮" },
 stopGeneration: { en: "Stop", zh: "停止生成" },
  send: { en: "Send", zh: "发送" },
  ask: { en: "Ask", zh: "提问" },
  stopped: { en: "Stopped", zh: "已停止" },
  userInterrupted: { en: "User interrupted", zh: "用户中断生成" },
  deleteSession: { en: "Delete this session?", zh: "删除此会话？" },
  deleted: { en: "Deleted", zh: "已删除" },
  completed: { en: "Done", zh: "完成" },
  turn: { en: "turn", zh: "轮" },
  error: { en: "Error", zh: "错误" },
  reasoningDone: { en: "Retrieval complete", zh: "推理检索结束" },
  // Maintenance
  runLint: { en: "Run Lint", zh: "运行检查" },
  lintFix: { en: "Auto-fix", zh: "自动修复" },
  recompileAll: { en: "Recompile All", zh: "全部重编译" },
  recompile: { en: "Recompile", zh: "重编译" },
  watcher: { en: "File Watcher", zh: "文件监听" },
  start: { en: "Start", zh: "启动" },
  stop: { en: "Stop", zh: "停止" },
  lintResults: { en: "Lint Results", zh: "检查结果" },
  structuralReport: { en: "Structural Report", zh: "结构检查报告" },
  knowledgeReport: { en: "Knowledge Report", zh: "知识检查报告" },
  noReports: { en: "No lint reports yet.", zh: "暂无检查报告。" },
  // Overview
  documentsCol: { en: "Documents", zh: "文档" },
  concepts: { en: "Concepts", zh: "概念" },
  summaries: { en: "Summaries", zh: "摘要" },
  recentDocs: { en: "Recent Documents", zh: "最近文档" },
  activity: { en: "Activity", zh: "活动" },
  lastCompile: { en: "Last compile", zh: "最近编译" },
  clickToQuery: { en: "Click to query", zh: "点击提问" },
  // Inspector
 inspectorTitle: { en: "Reasoning Timeline", zh: "推理时间线" },
  inspBusy: { en: "Reasoning…", zh: "推理中…" },
  inspIdle: { en: "Idle", zh: "空闲" },
  inspEmptyHint: { en: "After a query or chat, the vectorless retrieval and reasoning steps appear here in real time.", zh: "发起查询或对话后，无向量检索与推理过程将在此实时呈现。" },
 startRetrieval: { en: "Started retrieval", zh: "启动推理检索" },
  toolCall: { en: "Tool", zh: "工具" },
  inspect: { en: "Inspect", zh: "检视" },
  // Settings
  settings: { en: "Connection", zh: "连接" },
  apiBase: { en: "API Base URL", zh: "API 地址" },
  apiToken: { en: "API Token", zh: "API 令牌" },
  connect: { en: "Connect", zh: "连接" },
  cancel: { en: "Cancel", zh: "取消" },
  tokenRequired: { en: "Token is required.", zh: "请填写令牌。" },
  insecureWarn: { en: "Warning: non-HTTPS cross-origin URL will send your token unencrypted.", zh: "警告：非 HTTPS 跨域地址将以明文发送令牌。" },

  // Documents extended
  uploading: { en: "Uploading", zh: "上传中" },
  compiling: { en: "Compiling", zh: "编译中" },
  added: { en: "Added", zh: "已添加" },
  uploadFailed: { en: "Upload failed", zh: "上传失败" },
  failed: { en: "Failed", zh: "失败" },
  processing: { en: "Processing", zh: "处理" },
 deletedDoc: { en: "Deleted", zh: "已删除" },
  deleteFailed: { en: "Delete failed", zh: "删除失败" },
  confirmDeleteDoc: { en: "Delete document and clean its wiki pages?", zh: "确定删除文档并清理其 wiki 页面？" },
  plan: { en: "Plan", zh: "计划" },
  refresh: { en: "Refresh", zh: "刷新" },
  noDocsYet: { en: "No documents yet", zh: "暂无文档" },
  uploadHint: { en: "Files will be compiled into wiki automatically.", zh: "上传文件后将自动编译为 wiki。" },
  dragOrClick: { en: "Drag files or click to upload", zh: "拖入文件或点击上传" },
  uploadProgress: { en: "Upload Progress", zh: "上传进度" },
  indexedDocs: { en: "Indexed Documents", zh: "已索引文档" },
  pages: { en: "Pages", zh: "页数" },
  hash: { en: "Hash", zh: "哈希" },
  flowDone: { en: "Compile flow finished", zh: "编译流程结束" },
  // Maintenance extended
  running: { en: "Running", zh: "运行中" },
  autoFixSuffix: { en: "(auto-fix)", zh: "（自动修复）" },
  skipped: { en: "Skipped", zh: "已跳过" },
  filesChanged: { en: "Files changed", zh: "修改文件" },
  ghostsRemoved: { en: "Ghost links cleaned", zh: "清理幽灵链接" },
  lintComplete: { en: "Lint complete", zh: "检查完成" },
  recompiling: { en: "Recompiling...", zh: "重编译中…" },
  targets: { en: "Targets", zh: "目标" },
  recompileDone: { en: "Recompile done", zh: "重编译完成" },
  recompiled: { en: "Recompiled", zh: "重编译" },
  watcherOn: { en: "Watcher started", zh: "已开启监听" },
  watcherOff: { en: "Watcher stopped", zh: "已停止监听" },
  healthLint: { en: "Health Check - Lint", zh: "健康检查 · Lint" },
  lintDesc: { en: "Checks structural integrity and knowledge consistency. Can auto-fix broken wikilinks.", zh: "检测结构完整性与知识一致性，可自动修复失效的 wikilink。" },
  recompileSection: { en: "Recompile", zh: "重新编译 · Recompile" },
  recompileDesc: { en: "Re-run compilation on indexed documents. Regenerates summaries and rewrites concept pages (manual edits will be overwritten).", zh: "对已索引文档重跑编译，重生成摘要并改写概念页（手动编辑会被覆盖）。" },
  scope: { en: "Scope", zh: "范围" },
  allDocs: { en: "All documents", zh: "全部文档" },
  oneDoc: { en: "Specific document", zh: "指定文档" },
  noDocOption: { en: "(no documents)", zh: "（无文档）" },
  startRecompile: { en: "Start Recompile", zh: "开始重编译" },
  watchSection: { en: "File Watch", zh: "文件监听 · Watch" },
  watchDesc: { en: "Watches raw/ directory. New files are compiled into wiki automatically.", zh: "监听 raw/ 目录，新增文件自动编译为 wiki。" },
  watchStatus: { en: "Watcher status", zh: "监听状态" },
  kbStatus: { en: "Knowledge Base Status", zh: "知识库状态" },
  kbStatusDesc: { en: "Directory structure and index overview.", zh: "目录结构与索引概况。" },
  rawFiles: { en: "Raw files", zh: "原始文件" },
  indexed: { en: "Indexed", zh: "已索引" },
  lastLint: { en: "Last lint", zh: "上次检查" },
  autoFixLabel: { en: "Auto-fix", zh: "自动修复" },
  // Additional keys for full i18n coverage
  askKbDesc: { en: "Vectorless reasoning-based retrieval; answers include the reasoning timeline.", zh: "基于无向量推理检索，答案附推理过程。" },
  prefillTemplate: { en: 'What is "{concept}"? Please explain based on the knowledge base.', zh: '什么是「{concept}」？请基于知识库解释。' },
  noDocsYetDesc: { en: "Go to the Documents page to add files and start compiling.", zh: "去「文档」页添加文件开始编译。" },
  apiBasePlaceholder: { en: "Leave blank for same-origin (e.g. http://127.0.0.1:8000)", zh: "留空则同源访问（如 http://127.0.0.1:8000）" },
  save: { en: "Save", zh: "保存" },
  loading: { en: "Loading", zh: "加载中" },
  requestFailed: { en: "Request failed", zh: "请求失败" },
};

export function getStoredLang() {
  return localStorage.getItem(LS_LANG) || "en";
}

export function storeLang(lang) {
  localStorage.setItem(LS_LANG, lang);
}

const I18nContext = createContext(null);

export function I18nProvider({ children }) {
  const [lang, setLang] = useState(getStoredLang);
  const t = useCallback((key) => {
    const entry = STRINGS[key];
    if (!entry) return key;
    return entry[lang] || entry.en || key;
  }, [lang]);
  const toggleLang = useCallback(() => {
    setLang((prev) => {
      const next = prev === "en" ? "zh" : "en";
      storeLang(next);
      return next;
    });
  }, []);
  return (
    <I18nContext.Provider value={{ lang, t, toggleLang }}>
      {children}
    </I18nContext.Provider>
  );
}

export function useI18n() {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error("useI18n must be used within I18nProvider");
  return ctx;
}
