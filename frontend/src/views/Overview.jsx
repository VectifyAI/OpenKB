import { useEffect, useState } from "react";
import { api } from "../api/client.js";
import { useApp } from "../state/AppContext.jsx";
import { useI18n } from "../i18n.jsx";
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
    const lang = localStorage.getItem("openkb_lang") || "en";
    return d.toLocaleString(lang === "zh" ? "zh-CN" : "en-US", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  } catch {
    return iso;
  }
}

export default function Overview({ kb }) {
  const { setView, toastMsg } = useApp();
  const { t } = useI18n();
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

  if (err) return <EmptyState title={t("error")} desc={err} />;
  if (!data) return <div className="empty-state"><Spinner /></div>;

  const { status, list } = data;
  const dirs = status.directories || {};

  return (
    <>
      <div className="stat-grid">
        <StatCard label={t("documentsCol")} value={status.total_indexed} sub={`${t("rawFiles")} ${status.raw_count}`} color="accent" />
        <StatCard label={t("concepts")} value={dirs.concepts || 0}  color="cyan" />
        <StatCard label={t("summaries")} value={dirs.summaries || 0}  color="green" />
        <StatCard label={t("lintResults")} value={dirs.reports || 0}  color="purple" />
      </div>

      {list.concepts && list.concepts.length > 0 && (
        <div className="panel">
          <div className="panel-head">
            <span className="panel-title">{t("concepts")}</span>
            <span className="tag">{list.concepts.length}</span>
          </div>
          <div className="concept-chips">
            {list.concepts.slice(0, 40).map((c) => (
              <button
                key={c}
                className="concept-chip"
                onClick={() => { setView("query"); setTimeout(() => window.dispatchEvent(new CustomEvent("openkb:prefill-query", { detail: t("prefillTemplate").replace("{concept}", c) })), 30); }}
              >
                {c}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="panel">
        <div className="panel-head"><span className="panel-title">{t("recentDocs")}</span></div>
        <div className="panel-body">
          {list.documents && list.documents.length > 0 ? (
            <table className="table">
              <thead><tr><th>{t("docName")}</th><th>{t("docType")}</th><th>{t("pages")}</th></tr></thead>
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
            <EmptyState title={t("noDocsYet")} desc={t("noDocsYetDesc")} />
          )}
        </div>
      </div>

      {(status.last_compile || status.last_lint) && (
        <div className="panel">
          <div className="panel-head"><span className="panel-title">{t("activity")}</span></div>
          <div className="panel-body">
            {status.last_compile && <div className="log-line ok" style={{ padding: "4px 16px" }}>{t("lastCompile")}: {fmtTime(status.last_compile)}</div>}
            {status.last_lint && <div className="log-line ok" style={{ padding: "4px 16px" }}>{t("lastLint")}: {fmtTime(status.last_lint)}</div>}
          </div>
        </div>
      )}
    </>
  );
}
