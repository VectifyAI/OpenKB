import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";
import { getStoredLang } from "./i18n.jsx";
import "./styles.css";

// Vite mangles the zh-CN <title> on Windows; set it at runtime, in the stored
// language, to stay correct before React mounts. Shell syncs it on toggle.
const _APP_TITLES = { en: "OpenKB · Knowledge Workbench", zh: "OpenKB · 知识工作台" };
document.title = _APP_TITLES[getStoredLang()] || _APP_TITLES.en;

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
