import { useState, useEffect } from "react";
import { X } from "lucide-react";
import { useApp } from "../state/AppContext.jsx";

export default function SettingsModal() {
  const { settingsOpen, setSettingsOpen, saveConnection, apiBase, token, toastMsg } = useApp();
  const [base, setBase] = useState(apiBase);
  const [tok, setTok] = useState(token);

  useEffect(() => {
    setBase(apiBase);
    setTok(token);
  }, [apiBase, token, settingsOpen]);

  if (!settingsOpen) return null;

  function handleSave() {
    const cleaned = (base || "").trim().replace(/\/$/, "");
    saveConnection(cleaned, tok);
    setSettingsOpen(false);
    toastMsg("已保存，正在刷新…", "ok");
    window.dispatchEvent(new CustomEvent("openkb:reload-kbs"));
  }

  return (
    <div className="overlay" onClick={() => setSettingsOpen(false)}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>连接设置</h2>
          <button className="icon-btn" onClick={() => setSettingsOpen(false)}>
            <X size={18} />
          </button>
        </div>
        <div className="modal-body">
          <label className="field">
            <span className="field-label">API 地址</span>
            <input value={base} onChange={(e) => setBase(e.target.value)} placeholder="留空则同源访问（如 http://127.0.0.1:8000）" />
          </label>
          <label className="field">
            <span className="field-label">Bearer Token</span>
            <input type="password" value={tok} onChange={(e) => setTok(e.target.value)} placeholder="OPENKB_API_TOKEN" />
          </label>
          <p className="field-hint">配置信息仅保存在本浏览器本地。</p>
        </div>
        <div className="modal-foot">
          <button className="btn btn-ghost" onClick={() => setSettingsOpen(false)}>取消</button>
          <button className="btn btn-primary" onClick={handleSave}>保存</button>
        </div>
      </div>
    </div>
  );
}
