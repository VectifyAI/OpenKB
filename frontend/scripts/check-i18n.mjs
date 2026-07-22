#!/usr/bin/env node
// i18n guard — fails the build on either regression:
//   (1) a Chinese (CJK) literal in `src/**/*.{ts,tsx}` that is NOT a comment
//       and NOT a locale JSON — i.e. a hardcoded UI string that skipped t().
//   (2) a zh/en key-set drift in any `src/locales/{zh,en}/<ns>.json` pair
//       (a missing `en` key silently falls back to zh, so both directions matter).
//
// Comment-aware: strips `//`, `/* */`, JSDoc `* ` (inside a block), and JSX
// `{/* */}` (the `/* */` inside the braces) before scanning, while KEEPING
// string-literal and JSX-text content — so a Chinese string literal or a raw
// Chinese JSX text node is still caught. Zero deps; ESM; run from `frontend/`.

import { readFileSync, readdirSync, statSync } from "node:fs"
import { fileURLToPath } from "node:url"
import { dirname, join, relative } from "node:path"

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..")
const SRC = join(ROOT, "src")
const LOCALES = join(SRC, "locales")
const CJK = /[一-鿿]/

/** Replace comments with spaces (newlines preserved), keeping string/template
 * literal contents and JSX text intact. A tiny state machine over the source. */
function stripComments(src) {
  let out = ""
  let state = "normal" // normal | line | block | sq | dq | tpl
  for (let i = 0; i < src.length; i++) {
    const c = src[i]
    const n = src[i + 1] ?? ""
    if (state === "normal") {
      if (c === "/" && n === "/") { state = "line"; out += "  "; i++; continue }
      if (c === "/" && n === "*") { state = "block"; out += "  "; i++; continue }
      if (c === "'") { state = "sq"; out += c; continue }
      if (c === '"') { state = "dq"; out += c; continue }
      if (c === "`") { state = "tpl"; out += c; continue }
      out += c; continue
    }
    if (state === "line") {
      if (c === "\n") { state = "normal"; out += c } else { out += " " }
      continue
    }
    if (state === "block") {
      if (c === "*" && n === "/") { state = "normal"; out += "  "; i++; continue }
      out += c === "\n" ? "\n" : " "; continue
    }
    // string / template literal: preserve contents, honor escapes
    const quote = state === "sq" ? "'" : state === "dq" ? '"' : "`"
    if (c === "\\") { out += c + (src[i + 1] ?? ""); i++; continue }
    if (c === quote) { state = "normal"; out += c; continue }
    out += c
  }
  return out
}

function walk(dir, acc = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    const st = statSync(p)
    if (st.isDirectory()) {
      if (p === LOCALES) continue // locale JSON is the source of truth, skip
      walk(p, acc)
    } else if (/\.tsx?$/.test(p)) {
      acc.push(p)
    }
  }
  return acc
}

// ---- Check 1: no leftover UI CJK outside comments/locales ------------------
const leftovers = []
for (const file of walk(SRC)) {
  const stripped = stripComments(readFileSync(file, "utf8"))
  stripped.split("\n").forEach((line, idx) => {
    if (CJK.test(line)) {
      leftovers.push(`${relative(ROOT, file)}:${idx + 1}: ${line.trim()}`)
    }
  })
}

// ---- Check 2: zh/en key-set parity across every namespace ------------------
function flatten(obj, prefix = "", keys = new Set()) {
  for (const [k, v] of Object.entries(obj)) {
    const p = prefix ? `${prefix}.${k}` : k
    if (v && typeof v === "object" && !Array.isArray(v)) flatten(v, p, keys)
    else keys.add(p)
  }
  return keys
}

const parityErrors = []
const enDir = join(LOCALES, "en")
for (const f of readdirSync(enDir).filter((f) => f.endsWith(".json"))) {
  const ns = f.slice(0, -5)
  const en = flatten(JSON.parse(readFileSync(join(LOCALES, "en", f), "utf8")))
  const zh = flatten(JSON.parse(readFileSync(join(LOCALES, "zh", f), "utf8")))
  const onlyEn = [...en].filter((k) => !zh.has(k))
  const onlyZh = [...zh].filter((k) => !en.has(k))
  if (onlyEn.length) parityErrors.push(`${ns}: keys only in en → ${onlyEn.join(", ")}`)
  if (onlyZh.length) parityErrors.push(`${ns}: keys only in zh → ${onlyZh.join(", ")}`)
}

// ---- Report ----------------------------------------------------------------
let failed = false
if (leftovers.length) {
  failed = true
  console.error("i18n guard: hardcoded Chinese UI literal(s) found outside locale files.")
  console.error("Move the string into src/locales/{zh,en}/<ns>.json and wrap it with t().\n")
  for (const l of leftovers) console.error("  " + l)
  console.error("")
}
if (parityErrors.length) {
  failed = true
  console.error("i18n guard: zh/en locale key-set drift (a missing en key falls back to zh).\n")
  for (const e of parityErrors) console.error("  " + e)
  console.error("")
}

if (failed) process.exit(1)
console.log("i18n guard: OK (no leftover UI CJK; zh/en key sets identical across all namespaces).")
