import { UN_LANGUAGES } from "@/lib/languages"

/** Shared id linking a text `<input list=…>` to the UN-languages suggestions. */
export const UN_LANG_LIST_ID = "un-languages"

/**
 * A native `<datalist>` of the six official UN languages, used to turn a plain
 * text input for the wiki output language into a combobox: the input stays
 * free-text (any language can be typed) while the six languages surface as
 * quick-pick suggestions. Reference it from an input via `list={UN_LANG_LIST_ID}`.
 * Render once anywhere in the same document as the input(s) that use it.
 */
export function UnLanguageDatalist() {
  return (
    <datalist id={UN_LANG_LIST_ID}>
      {UN_LANGUAGES.map((l) => (
        <option key={l.value} value={l.value} />
      ))}
    </datalist>
  )
}
