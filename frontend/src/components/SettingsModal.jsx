import { useState, useEffect } from "react";
import { X } from "lucide-react";
import { useApp } from "../state/AppContext.jsx";
import { useI18n } from "../i18n.jsx";

export default function SettingsModal() {
  const { settingsOpen, setSettingsOpen, saveConnection, apiBase, token, toastMsg } = useApp();
  const { t } = useI18n();
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
    toastMsg(t("connect"), "ok");
    window.dispatchEvent(new CustomEvent("openkb:reload-kbs"));
  }

  return (
    <div className="overlay" onClick={() => setSettingsOpen(false)}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>{t("settings")}</h2>
          <button className="icon-btn" onClick={() => setSettingsOpen(false)}>
            <X size={18} />
          </button>
        </div>
        <div className="modal-body">
          <label className="field">
            <span className="field-label">{t("apiBase")}</span>
            <input value={base} onChange={(e) => setBase(e.target.value)} placeholder={t("apiBasePlaceholder")} />
          </label>
          <label className="field">
            <span className="field-label">Bearer Token</span>
            <input type="password" value={tok} onChange={(e) => setTok(e.target.value)} placeholder="OPENKB_API_TOKEN" />
          </label>
          <p className="field-hint">{t("insecureWarn")}</p>
        </div>
        <div className="modal-foot">
          <button className="btn btn-ghost" onClick={() => setSettingsOpen(false)}>{t("cancel")}</button>
          <button className="btn btn-primary" onClick={handleSave}>{t("save")}</button>
        </div>
      </div>
    </div>
  );
}
