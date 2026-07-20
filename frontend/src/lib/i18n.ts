import i18n from "i18next"
import { initReactI18next } from "react-i18next"
import resourcesToBackend from "i18next-resources-to-backend"

export const SUPPORTED_LANGUAGES = ["zh", "en", "fr", "es", "ru"] as const
export type Language = (typeof SUPPORTED_LANGUAGES)[number]

/** All namespaces. Only `common` is preloaded (see `ns` below); every other
 * namespace lazy-loads its {zh,en} JSON on the first useTranslation('<ns>'),
 * code-split per locale-namespace by Vite. */
export const NAMESPACES = [
  "common",
  "home",
  "kbList",
  "kb",
  "kbSettings",
  "settings",
  "chat",
  "artifacts",
] as const

void i18n
  // Turns a dynamic import() into an i18next backend: one code-split chunk per
  // `locales/<lng>/<ns>.json`. Mirrors E's mermaid `import()` lazy pattern.
  .use(
    resourcesToBackend(
      (language: string, namespace: string) => import(`../locales/${language}/${namespace}.json`),
    ),
  )
  .use(initReactI18next)
  .init({
    fallbackLng: "zh",
    supportedLngs: [...SUPPORTED_LANGUAGES],
    ns: ["common"], // preload only the always-visible chrome; rest are lazy
    defaultNS: "common",
    load: "languageOnly", // "en-US" → "en"
    interpolation: { escapeValue: false }, // React already escapes
    react: { useSuspense: false }, // no Suspense boundaries in the tree; avoid a hard suspend
  })

export default i18n
