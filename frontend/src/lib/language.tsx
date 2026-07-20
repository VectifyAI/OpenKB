import { createContext, useContext, useState, type ReactNode } from "react"
import { Languages } from "lucide-react"
import { useTranslation } from "react-i18next"
import i18n, { type Language, SUPPORTED_LANGUAGES } from "@/lib/i18n"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"

const KEY = "openkb_lang"

const LanguageCtx = createContext<{
  language: Language
  setLanguage: (l: Language) => void
} | null>(null)

/** Initial language: a stored choice wins; else browser/OS (anything starting
 * "zh" → zh, else en — `en` is the only non-Chinese bucket for the 2-locale
 * scope); else the zh fallback. Detection only picks the INITIAL value. */
function detectInitial(): Language {
  const supported = SUPPORTED_LANGUAGES as readonly string[]
  const stored = localStorage.getItem(KEY)
  if (stored && supported.includes(stored)) return stored as Language
  // Match the browser/OS language by its base subtag ("fr-CA" → "fr").
  const nav = (navigator.languages?.[0] ?? navigator.language ?? "").toLowerCase()
  const code = nav.split("-")[0]
  if (supported.includes(code)) return code as Language
  return "en"
}

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [language, setLanguageState] = useState<Language>(() => {
    const initial = detectInitial()
    if (i18n.language !== initial) void i18n.changeLanguage(initial)
    return initial
  })

  const setLanguage = (l: Language) => {
    localStorage.setItem(KEY, l)
    setLanguageState(l)
    void i18n.changeLanguage(l) // re-renders every mounted useTranslation() consumer
  }

  return <LanguageCtx.Provider value={{ language, setLanguage }}>{children}</LanguageCtx.Provider>
}

export function useLanguage() {
  const ctx = useContext(LanguageCtx)
  if (!ctx) throw new Error("useLanguage must be used within LanguageProvider")
  return ctx
}

/** Language menu button. A dropdown of every supported language (shown by its
 * own autonym); same `h-8 w-8` shape as ThemeToggle, slots into App.tsx's
 * top-right chrome pill next to it. */
export function LanguageToggle({ className }: { className?: string }) {
  const { language, setLanguage } = useLanguage()
  const { t } = useTranslation("common")
  const label = t(`language.${language}`)
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          title={t("language.toggleTitle", { label })}
          aria-label={t("language.toggleAria", { label })}
          className={`grid h-8 w-8 place-items-center rounded-lg ${className ?? ""}`}
        >
          <Languages className="w-4 h-4" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuRadioGroup
          value={language}
          onValueChange={(v) => setLanguage(v as Language)}
        >
          {SUPPORTED_LANGUAGES.map((lng) => (
            <DropdownMenuRadioItem key={lng} value={lng}>
              {t(`language.${lng}`)}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
