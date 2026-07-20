import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { HashRouter } from "react-router"
import App from "./App"
import { ThemeProvider } from "@/lib/theme"
import { LanguageProvider } from "@/lib/language"
import "./lib/i18n"
import "./index.css"

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <LanguageProvider>
      <ThemeProvider>
        <HashRouter>
          <App />
        </HashRouter>
      </ThemeProvider>
    </LanguageProvider>
  </StrictMode>,
)
