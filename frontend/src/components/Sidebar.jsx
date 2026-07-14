import { useState, useRef, useEffect } from "react";
import { LayoutGrid, FileText, Search, MessageSquare, Wrench, Settings, Plus, ChevronDown } from "lucide-react";
import { useApp } from "../state/AppContext.jsx";
import { api } from "../api/client.js";

const NAV = [
  { view: "overview", label: "概览", icon: LayoutGrid },
  { view: "documents", label: "文档", icon: FileText },
  { view: "query", label: "查询", icon: Search },
  { view: "chat", label: "对话", icon: MessageSquare },
  { view: "maintenance", label: "维护", icon: Wrench },
];

export default function Sidebar() {
  const { kbs, kb, setKb, view, setView, setSettingsOpen, sidebarOpen, setSidebarOpen, toastMsg } = useApp();
  const [menuOpen, setMenuOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const menuRef = useRef(null);

  useEffect(() => {
    function onClick(e) {
      if (menuRef.current && !menuRef.current.contains(e.target)) setMenuOpen(false);
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  async function handleCreate() {
    const name = window.prompt("新知识库名称（字母/数字/下划线/连字符）：");
    if (!name) return;
    setCreating(true);
    try {
      await api.initKb(name.trim());
      setKb(name.trim());
      setMenuOpen(false);
      window.dispatchEvent(new CustomEvent("openkb:reload-kbs"));
      toastMsg("已创建：" + name.trim(), "ok");
    } catch (e) {
      toastMsg(e.message, "err");
    } finally {
      setCreating(false);
    }
  }

  return (
    <>
      {sidebarOpen && <div className="sidebar-overlay" onClick={() => setSidebarOpen(false)} />}
      <aside className={`sidebar ${sidebarOpen ? "open" : ""}`}>
        <div className="brand">
          <span className="brand-mark">OK</span>
          <span className="brand-name">OpenKB</span>
        </div>
        <div className="kb-switcher" ref={menuRef}>
          <button className="kb-current" type="button" onClick={() => setMenuOpen((o) => !o)}>
            <span className="kb-current-dot" />
            <span className="kb-current-name">{kb || "未选择知识库"}</span>
            <ChevronDown className="kb-chev" size={14} />
          </button>
          {menuOpen && (
            <div className="kb-menu">
              <div className="kb-menu-list">
                {kbs.length === 0 && (
                  <div className="kb-menu-item" style={{ color: "var(--text-3)" }}>
                    <span className="mi-name">暂无知识库</span>
                  </div>
                )}
                {kbs.map((k) => (
                  <button
                    key={k.name}
                    className={`kb-menu-item ${k.name === kb ? "active" : ""}`}
                    onClick={() => { setKb(k.name); setMenuOpen(false); }}
                  >
                    <span className="mi-name">{k.name}</span>
                    <span className="mi-meta">{k.document_count} 篇</span>
                  </button>
                ))}
              </div>
              <button className="kb-menu-new" onClick={handleCreate} disabled={creating}>
                <Plus size={14} />
                新建知识库
              </button>
            </div>
          )}
        </div>
        <nav className="nav">
          {NAV.map(({ view: v, label, icon: Icon }) => (
            <button key={v} className={`nav-item ${view === v ? "active" : ""}`} onClick={() => setView(v)}>
              <Icon size={16} />
              <span>{label}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">
          <button className="icon-btn" title="连接设置" onClick={() => setSettingsOpen(true)}>
            <Settings size={16} />
          </button>
        </div>
      </aside>
    </>
  );
}
