import { createContext, useContext, useState, useCallback, useRef } from "react";
import { getApiBase, getToken, setConnection } from "../api/client.js";
import { useI18n } from "../i18n.jsx";

const AppContext = createContext(null);

export function AppProvider({ children }) {
  const { t } = useI18n();
  const [apiBase, setApiBaseState] = useState(getApiBase());
  const [token, setTokenState] = useState(getToken());
  const [kbs, setKbs] = useState([]);
  const [kb, setKb] = useState(null);
  const [view, setView] = useState("overview");
  // Auth is opt-in server-side, so don't prompt on load — just talk to the
  // same-origin API. The dialog opens reactively only if a request 401s
  // (i.e. the server has a token configured), via the openkb:unauthorized event.
  const [settingsOpen, setSettingsOpen] = useState(false);
  // Right-pane reasoning timeline.
  const [inspItems, setInspItems] = useState([]);
  const [inspBusy, setInspBusy] = useState(false);
  const [toast, setToastState] = useState(null);
  const toastTimer = useRef(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const saveConnection = useCallback((base, tok) => {
    const cleaned = (base || "").trim().replace(/\/$/, "");
    setConnection(cleaned, (tok || "").trim());
    setApiBaseState(cleaned);
    setTokenState((tok || "").trim());
  }, []);

  const setViewSafe = useCallback((v) => {
    setView(v);
    setSidebarOpen(false);
  }, []);

  const toastMsg = useCallback((message, kind = "") => {
    setToastState({ message, kind });
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToastState(null), 3200);
  }, []);

  // Inspector timeline helpers.
  const inspReset = useCallback((busy) => {
    setInspItems(busy ? [{ kind: "start", tag: t("start"), body: t("startRetrieval") }] : []);
    setInspBusy(!!busy);
  }, [t]);

  const inspAdd = useCallback((kind, tag, body) => {
    // delta is too granular for the timeline; render in the answer pane instead.
    if (kind === "delta") return;
    setInspItems((prev) => [...prev, { kind, tag, body }]);
  }, []);

  const inspDone = useCallback(() => {
    setInspBusy(false);
  }, []);

 const value = {
    apiBase,
    token,
    kbs,
    setKbs,
    kb,
    setKb,
    view,
    setView: setViewSafe,
    settingsOpen,
    setSettingsOpen,
    sidebarOpen,
    setSidebarOpen,
    saveConnection,
    toast,
    toastMsg,
    inspItems,
    inspBusy,
    inspReset,
    inspAdd,
    inspDone,
  };

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useApp() {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useApp must be used within AppProvider");
  return ctx;
}
