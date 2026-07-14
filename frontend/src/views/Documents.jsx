import { useEffect, useState, useRef, useCallback } from "react";
import { UploadCloud, Trash2, RefreshCw } from "lucide-react";
import { api } from "../api/client.js";
import { streamUpload, streamSSE } from "../api/sse.js";
import { useApp } from "../state/AppContext.jsx";
import EmptyState from "../components/EmptyState.jsx";
import Spinner from "../components/Spinner.jsx";

function docRow(d) {
  const ext = (d.name.split(".").pop() || "").toLowerCase();
  const ico = d.type === "url" ? "URL" : ext.toUpperCase().slice(0, 4) || "FILE";
  return (
    <tr key={d.hash}>
      <td><span className="icon-cell"><span className="file-ico">{ico}</span><span className="cell-name">{d.name}</span></span></td>
      <td><span className="tag">{d.display_type || d.type || "—"}</span></td>
      <td className="cell-meta">{d.pages != null ? d.pages : "—"}</td>
      <td className="cell-meta">{d.hash.slice(0, 8)}</td>
      <td><div className="row-actions"><button className="btn btn-danger btn-sm" data-rm={d.name}>删除</button></div></td>
    </tr>
  );
}

export default function Documents({ kb }) {
  const { inspReset, inspAdd, inspDone, toastMsg } = useApp();
  const [docs, setDocs] = useState(null);
  const [err, setErr] = useState(null);
  const [uploads, setUploads] = useState([]);
  const [dragging, setDragging] = useState(false);
  const fileRef = useRef(null);

  const load = useCallback(() => {
    setDocs(null);
    setErr(null);
    api.list(kb).then((r) => setDocs(r.documents || [])).catch((e) => setErr(e.message));
  }, [kb]);

  useEffect(load, [load]);

  function onFiles(files) {
    if (!files || !files.length) return;
    const items = Array.from(files).map((f) => ({ name: f.name, pct: 10, status: "上传中" }));
    setUploads(items);
    inspReset(true);
    const form = new FormData();
    form.append("kb", kb);
    form.append("stream", "true");
    Array.from(files).forEach((f) => form.append("files", f));
    streamUpload("/api/v1/add", form, (ev, d) => {
      if (ev === "file_start") {
        setUploads((prev) => prev.map((u) => (u.name === d.original_name ? { ...u, pct: 50, status: "编译中" } : u)));
        inspAdd("tool", "编译", `处理 <code>${d.original_name}</code>`);
      } else if (ev === "file_done") {
        const ok = d.status === "added";
        setUploads((prev) => prev.map((u) => (u.name === d.original_name ? { ...u, pct: 100, status: ok ? "已添加" : d.status } : u)));
        inspAdd(ok ? "done" : "tool", ok ? "完成" : "跳过", `${d.original_name}：${d.message || d.status}`);
      } else if (ev === "final") {
        toastMsg(`已添加 ${d.added_count}，跳过 ${d.skipped_count}，失败 ${d.failed_count}`, d.failed_count ? "err" : "ok");
      } else if (ev === "error") {
        toastMsg(d.message || "上传失败", "err");
        inspAdd("error", "错误", d.message || "");
      }
    }).then(() => { inspAdd("done", "结束", "编译流程结束"); load(); })
      .catch((e) => { toastMsg(e.message, "err"); inspAdd("error", "错误", e.message); })
      .finally(() => { inspDone(); });
  }

  async function removeDoc(name) {
    if (!window.confirm(`确定删除文档「${name}」并清理其 wiki 页面？`)) return;
    inspReset(true);
    try {
      await streamSSE("/api/v1/remove", { kb, identifier: name, stream: true }, (ev, d) => {
        if (ev === "plan") inspAdd("tool", "计划", `将清理 ${d.name || name} 相关页面`);
        else if (ev === "final") inspAdd("done", "完成", `已删除 ${d.name || name}`);
        else if (ev === "error") { toastMsg(d.message || "删除失败", "err"); inspAdd("error", "错误", d.message || ""); }
      });
      toastMsg("已删除", "ok");
      load();
    } catch (e) {
      toastMsg(e.message, "err");
      inspAdd("error", "错误", e.message);
    } finally {
      inspDone();
    }
  }

  return (
    <>
      <div
        className={`dropzone ${dragging ? "drag" : ""}`}
        onClick={() => fileRef.current && fileRef.current.click()}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); if (e.dataTransfer.files.length) onFiles(e.dataTransfer.files); }}
      >
        <UploadCloud size={28} style={{ margin: "0 auto 8px", color: "var(--text-2)" }} />
        <div className="dz-title">拖入文件或点击上传</div>
        <div>PDF · Word · Markdown · PPT · HTML · Excel · CSV · TXT · URL</div>
        <input ref={fileRef} type="file" multiple style={{ display: "none" }} onChange={(e) => { if (e.target.files.length) onFiles(e.target.files); e.target.value = ""; }} />
      </div>

      {uploads.length > 0 && (
        <div className="panel">
          <div className="panel-head"><span className="panel-title">上传进度</span></div>
          <div className="panel-body">
            {uploads.map((u, i) => (
              <div className="upload-item" key={i}>
                <span className="up-name">{u.name}</span>
                <div className="progress"><span style={{ width: `${u.pct}%` }} /></div>
                <span className="cell-meta">{u.status}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="panel">
        <div className="panel-head">
          <span className="panel-title">已索引文档</span>
          <button className="btn btn-ghost btn-sm" onClick={load}><RefreshCw size={14} /> 刷新</button>
        </div>
        <div className="panel-body">
          {err ? <EmptyState title="加载失败" desc={err} /> :
           docs === null ? <div className="empty-state"><Spinner /></div> :
           docs.length === 0 ? <EmptyState title="暂无文档" desc="上传文件后将自动编译为 wiki。" /> : (
            <table className="table">
              <thead><tr><th>文档</th><th>类型</th><th>页数</th><th>哈希</th><th></th></tr></thead>
              <tbody>
                {docs.map((d) => {
                  const row = docRow(d);
                  return <tr key={d.hash} onClick={(e) => { const btn = e.target.closest("[data-rm]"); if (btn) removeDoc(btn.getAttribute("data-rm")); }}>{row.props.children}</tr>;
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </>
  );
}
