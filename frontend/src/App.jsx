import { useEffect, useCallback } from "react";
import { Menu } from "lucide-react";
import { AppProvider, useApp } from "./state/AppContext.jsx";
import { I18nProvider, useI18n } from "./i18n.jsx";
import { api } from "./api/client.js";
import Sidebar from "./components/Sidebar.jsx";
import Inspector from "./components/Inspector.jsx";
import SettingsModal from "./components/SettingsModal.jsx";
import Toast from "./components/Toast.jsx";
import Overview from "./views/Overview.jsx";
import Documents from "./views/Documents.jsx";
import Query from "./views/Query.jsx";
import Chat from "./views/Chat.jsx";
import Maintenance from "./views/Maintenance.jsx";

function Shell() {
  const { kb, view, setKbs, setKb, kbs, setSidebarOpen, setSettingsOpen, toastMsg } = useApp();
  const { t, lang } = useI18n();

  const loadKbs = useCallback(async () => {
    // No connection gate: auth is opt-in server-side, so try the same-origin
    // API directly. If the server requires a token, listKbs 401s and
    // request() fires openkb:unauthorized, which opens the settings dialog.
    try {
      const data = await api.listKbs();
      setKbs(data.knowledge_bases || []);
      setKb((prev) => prev || (data.knowledge_bases?.[0]?.name ?? null));
    } catch {
      setKbs([]);
    }
  }, [setKbs, setKb]);

  useEffect(() => {
    loadKbs();
    function onReload() { loadKbs(); }
    function onToast(e) { toastMsg(e.detail.message, e.detail.kind); }
    function onUnauthorized() { setSettingsOpen(true); }
    window.addEventListener("openkb:reload-kbs", onReload);
    window.addEventListener("openkb:toast", onToast);
    window.addEventListener("openkb:unauthorized", onUnauthorized);
    return () => {
      window.removeEventListener("openkb:reload-kbs", onReload);
      window.removeEventListener("openkb:toast", onToast);
      window.removeEventListener("openkb:unauthorized", onUnauthorized);
    };
  }, [loadKbs, toastMsg, setSettingsOpen]);

  // Keep the browser tab title in sync with the active language.
  useEffect(() => {
    document.title = t("appTitle");
  }, [lang, t]);

  function renderView() {
    if (!kb) return <div className="empty-state"><h3>{t("noKbSelected")}</h3><p>{t("selectOrCreate")}</p></div>;
    if (view === "overview") return <Overview kb={kb} />;
    if (view === "documents") return <Documents kb={kb} />;
    if (view === "query") return <Query kb={kb} />;
    if (view === "chat") return <Chat kb={kb} />;
    if (view === "maintenance") return <Maintenance kb={kb} />;
    return null;
  }

  return (
    <div className="app">
      <button className="mobile-menu-btn" onClick={() => setSidebarOpen(true)} aria-label={t("menu")}>
        <Menu size={20} />
      </button>
      <Sidebar />
      <main className="workspace">
        <header className="ws-header">
          <h1 className="ws-title">{t(view)}</h1>
        </header>
        <section className="ws-body">{renderView()}</section>
      </main>
      <Inspector />
      <SettingsModal />
      <Toast />
    </div>
  );
}

export default function App() {
  return (
    <I18nProvider>
      <AppProvider>
        <Shell />
      </AppProvider>
    </I18nProvider>
  );
}
