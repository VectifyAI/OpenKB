import { useEffect, useState } from "react";
import { api } from "../api/client.js";
import { useApp } from "../state/AppContext.jsx";
import EmptyState from "../components/EmptyState.jsx";
import Spinner from "../components/Spinner.jsx";

function StatCard({ label, value, sub, color }) {
  return (
    <div className={`stat-card ${color || ""}`}>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      <div className="stat-sub">{sub}</div>
    </div>
  );
}

function fmtTime(iso) {
  if (!iso) return null;
  try {
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    return d.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  } catch {
    return iso;
  }
}

export default function Overview({ kb }) {
  const { setView, toastMsg } = useApp();
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    let alive = true;
    setData(null);
    setErr(null);
    Promise.all([api.status(kb), api.list(kb)])
      .then(([status, list]) => { if (alive) setData({ status, list }); })
      .catch((e) => { if (alive) setErr(e.message); });
    return () => { alive = false; };
  }, [kb]);

  if (err) return <EmptyState title="加载失败" desc={err} />;
  if (!data) return <div className="empty-state"><Spinner /></div>;

  const { status, list } = data;
  const dirs = status.directories || {};

  return (
    <>
      <div className="stat-grid">
        <StatCard label="已索引文档" value={status.total_indexed} sub={`原始文件 ${status.raw_count}`} color="accent" />
        <StatCard label="概念页" value={dirs.concepts || 0} sub="跨文档综合" color="cyan" />
        <StatCard label="摘要页" value={dirs.summaries || 0} sub="每篇一摘要" color="green" />
        <StatCard label="报告页" value={dirs.reports || 0} sub="检查与合成" color="purple" />
      </div>

      {list.concepts && list.concepts.length > 0 && (
        <div className="panel">
          <div className="panel-head">
            <span className="panel-title">核心概念</span>
            <span className="tag">{list.concepts.length}</span>
          </div>
          <div className="concept-chips">
            {list.concepts.slice(0, 40).map((c) => (
              <button
                key={c}
                className="concept-chip"
                onClick={() => { setView("query"); setTimeout(() => window.dispatchEvent(new CustomEvent("openkb:prefill-query", { detail: `什么是「${c}」？请基于知识库解释。` })), 30); }}
              >
                {c}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="panel">
        <div className="panel-head"><span className="panel-title">最近文档</span></div>
        <div className="panel-body">
          {list.documents && list.documents.length > 0 ? (
            <table className="table">
              <thead><tr><th>文档</th><th>类型</th><th>页数</th></tr></thead>
              <tbody>
                {list.documents.slice(-8).reverse().map((d) => (
                  <tr key={d.hash}>
                    <td><span className="icon-cell"><span className="file-ico">{d.display_type || d.type || "FILE"}</span><span className="cell-name">{d.name}</span></span></td>
                    <td><span className="tag">{d.display_type || d.type || "—"}</span></td>
                    <td className="cell-meta">{d.pages != null ? d.pages : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <EmptyState title="暂无文档" desc="去「文档」页添加文件开始编译。" />
          )}
        </div>
      </div>

      {(status.last_compile || status.last_lint) && (
        <div className="panel">
          <div className="panel-head"><span className="panel-title">活动</span></div>
          <div className="panel-body">
            {status.last_compile && <div className="log-line ok" style={{ padding: "4px 16px" }}>上次编译：{fmtTime(status.last_compile)}</div>}
            {status.last_lint && <div className="log-line ok" style={{ padding: "4px 16px" }}>上次检查：{fmtTime(status.last_lint)}</div>}
          </div>
        </div>
      )}
    </>
  );
}
