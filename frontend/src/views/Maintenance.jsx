import { useState, useEffect, useRef } from "react";
import { api } from "../api/client.js";
import { streamSSE } from "../api/sse.js";
import { useApp } from "../state/AppContext.jsx";
import { useI18n } from "../i18n.jsx";

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

function fmtTime(iso) {
  if (!iso) return null;
  try { const d = new Date(iso); return isNaN(d) ? iso : d.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }); }
  catch { return iso; }
}

export default function Maintenance({ kb }) {
  const { inspReset, inspAdd, inspDone, toastMsg } = useApp();
  const { t } = useI18n();
  const [fix, setFix] = useState(false);
  const [lintLog, setLintLog] = useState([]);
  const [rcScope, setRcScope] = useState("all");
  const [rcDocs, setRcDocs] = useState([]);
  const [rcDoc, setRcDoc] = useState("");
  const [rcLog, setRcLog] = useState([]);
  const [watchOn, setWatchOn] = useState(false);
  const [status, setStatus] = useState(null);
  const rcScroll = useRef(null);
  const lintRef = useRef([]);
  const rcRef = useRef([]);

  useEffect(() => {
    api.status(kb).then(setStatus).catch(() => setStatus(null));
    api.watchStatus(kb).then((r) => setWatchOn(!!r.active)).catch(() => {});
  }, [kb]);

  // Populate the document list for the "指定文档" recompile scope.
  useEffect(() => {
    let alive = true;
    api.list(kb)
      .then((r) => { if (!alive) return; const docs = (r.documents || []).map((d) => d.name).filter(Boolean); setRcDocs(docs); setRcDoc((prev) => (prev && docs.includes(prev)) ? prev : (docs[0] || "")); })
      .catch(() => { if (!alive) return; setRcDocs([]); setRcDoc(""); });
    return () => { alive = false; };
  }, [kb]);

  useEffect(() => { if (rcScroll.current) rcScroll.current.scrollTop = rcScroll.current.scrollHeight; }, [rcLog]);

  function pushLint(line) { lintRef.current = [...lintRef.current, line]; setLintLog(lintRef.current); }
  function pushRc(line) { rcRef.current = [...rcRef.current, line]; setRcLog(rcRef.current); }

  async function runLint() {
    lintRef.current = [{ kind: "plain", text: `${t("running")}${fix ? t("autoFixSuffix") : ""}...` }];
    setLintLog(lintRef.current);
    inspReset(true);
    try {
      const r = await api.lint(kb, fix);
      lintRef.current = [];
      if (r.skipped) pushLint({ kind: "warn", text: r.reason || t("skipped") });
      pushLint({ kind: "ok", text: r.message });
      if (r.lint_files_changed != null) pushLint({ kind: "plain", text: `${t("filesChanged")}: ${r.lint_files_changed}, ${t("ghostsRemoved")}: ${r.lint_ghosts_removed}` });
      if (r.structural_report) pushLint({ kind: "plain", text: esc(r.structural_report).slice(0, 600) });
      inspAdd("done", "Lint", esc(r.message));
      toastMsg(t("lintComplete"), "ok");
    } catch (e) {
      pushLint({ kind: "err", text: esc(e.message) });
      toastMsg(e.message, "err");
      inspAdd("error", t("error"), esc(e.message));
    } finally {
      inspDone();
    }
  }

  async function runRecompile() {
    rcRef.current = [{ kind: "plain", text: t("recompiling") }];
    setRcLog(rcRef.current);
    inspReset(true);
    // all -> all_docs:true; one -> doc_name (backend resolves a single doc).
    const payload = rcScope === "one"
      ? { kb, doc_name: rcDoc, stream: true }
      : { kb, all_docs: true, stream: true };
    try {
      await streamSSE("/api/v1/recompile", payload, (ev, d) => {
        if (ev === "plan") pushRc({ kind: "plain", text: `${t("targets")} ${d.targets ? d.targets.length : 0}` });
        else if (ev === "doc") pushRc({ kind: d.status === "ok" ? "ok" : d.status === "error" ? "err" : "warn", text: `${d.name || d.doc_name || ""} → ${d.status}${d.message ? "，" + d.message : ""}` });
        else if (ev === "final") { pushRc({ kind: "ok", text: `${t("recompileDone")}: ${t("recompiled")} ${d.recompiled}, ${t("skipped")} ${d.skipped}` }); toastMsg(t("recompileDone"), "ok"); inspAdd("done", t("completed"), `${t("recompiled")} ${d.recompiled}, ${t("skipped")} ${d.skipped}`); }
        else if (ev === "error") { pushRc({ kind: "err", text: d.message }); toastMsg(d.message, "err"); inspAdd("error", t("error"), esc(d.message)); }
      });
    } catch (e) {
      pushRc({ kind: "err", text: esc(e.message) });
      toastMsg(e.message, "err");
      inspAdd("error", t("error"), esc(e.message));
    } finally {
      inspDone();
      api.status(kb).then(setStatus).catch(() => {});
    }
  }

  async function toggleWatch() {
    const turnOn = !watchOn;
    try {
      if (turnOn) await api.watchStart(kb, 2);
      else await api.watchStop(kb);
      setWatchOn(turnOn);
      toastMsg(turnOn ? t("watcherOn") : t("watcherOff"), "ok");
    } catch (e) {
      toastMsg(e.message, "err");
    }
  }

  const dirs = status?.directories || {};
  const rcDisabled = rcScope === "one" && !rcDoc;

  return (
    <div className="maint-grid">
      <div className="maint-card">
        <h3>{t("healthLint")}</h3>
        <p>{t("lintDesc")}</p>
        <div className="toggle-row">
          <span className="cell-meta">{t("autoFixLabel")}</span>
          <div className={`toggle ${fix ? "on" : ""}`} onClick={() => setFix((f) => !f)} />
        </div>
        <div className="row-actions">
          <button className="btn btn-primary btn-sm" onClick={runLint}>{t("runLint")}</button>
        </div>
        <div className="maint-log">
          {lintLog.map((l, i) => <div key={i} className={`log-line ${l.kind}`}>{l.text}</div>)}
        </div>
      </div>

      <div className="maint-card">
        <h3>{t("recompileSection")}</h3>
        <p>{t("recompileDesc")}</p>
        <div className="toggle-row">
          <span className="cell-meta">{t("scope")}</span>
          <select className="select" style={{ width: "auto" }} value={rcScope} onChange={(e) => setRcScope(e.target.value)}>
            <option value="all">{t("allDocs")}</option>
            <option value="one">{t("oneDoc")}</option>
          </select>
          {rcScope === "one" && (
            <select className="select" style={{ width: "auto", marginLeft: 8 }} value={rcDoc} onChange={(e) => setRcDoc(e.target.value)} disabled={rcDocs.length === 0}>
              {rcDocs.length === 0
                ? <option value="">{t("noDocOption")}</option>
                : rcDocs.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          )}
        </div>
        <div className="row-actions">
          <button className="btn btn-ghost btn-sm" onClick={runRecompile} disabled={rcDisabled}>{t("startRecompile")}</button>
        </div>
        <div className="maint-log" ref={rcScroll}>
          {rcLog.map((l, i) => <div key={i} className={`log-line ${l.kind}`}>{l.text}</div>)}
        </div>
      </div>

      <div className="maint-card">
        <h3>{t("watchSection")}</h3>
        <p>{t("watchDesc")}</p>
        <div className="toggle-row">
          <span className="cell-meta">{t("watchStatus")}</span>
          <div className={`toggle ${watchOn ? "on" : ""}`} onClick={toggleWatch} />
        </div>
      </div>

      <div className="maint-card">
        <h3>{t("kbStatus")}</h3>
        <p>{t("kbStatusDesc")}</p>
        <div className="maint-log">
         {Object.entries(dirs).map(([k, v]) => <div key={k} className="log-line">{k}: {v}</div>)}
          {status && <div className="log-line">{`${t("rawFiles")}: ${status.raw_count}`}</div>}
          {status && <div className="log-line">{`${t("indexed")}: ${status.total_indexed}`}</div>}
          {status?.last_compile && <div className="log-line ok">{t("lastCompile")}: {fmtTime(status.last_compile)}</div>}
          {status?.last_lint && <div className="log-line ok">{t("lastLint")}: {fmtTime(status.last_lint)}</div>}
       </div>
      </div>
    </div>
  );
}
