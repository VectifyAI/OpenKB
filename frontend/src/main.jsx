import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";
import "./styles.css";

// Vite mangles the zh-CN <title> on Windows; set it at runtime to stay correct.
document.title = "OpenKB · 知识工作台";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
