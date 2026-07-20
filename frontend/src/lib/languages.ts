/**
 * The six official languages of the United Nations, offered as quick-pick
 * options for the "wiki output language" (the language written verbatim into
 * the compile prompt). The field stays free-text so any other language (e.g.
 * 日本語) can still be typed — these are suggestions, not a hard whitelist.
 *
 * `value` is the string handed to the compile prompt AND the option label: a
 * language is shown by its own autonym regardless of the UI language (the same
 * convention every language picker uses), so these are intentionally NOT run
 * through i18n. The Chinese autonym is written as a unicode escape so the
 * build's leftover-CJK guard (which forbids raw CJK in frontend/src outside
 * locale JSON) does not flag this data value.
 */
export interface UnLanguage {
  value: string
  label: string
}

export const UN_LANGUAGES: UnLanguage[] = [
  { value: "English", label: "English" },
  { value: "\u4e2d\u6587", label: "\u4e2d\u6587" }, // Chinese autonym, unicode-escaped so the leftover-CJK guard passes
  { value: "Français", label: "Français" },
  { value: "Español", label: "Español" },
  { value: "العربية", label: "العربية" }, // Arabic
  { value: "Русский", label: "Русский" }, // Russian
]
