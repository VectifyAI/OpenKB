---
name: openkb-deck-neon
description: |
  Use when the user asks the openkb chat to make a deck / slide presentation /
  PPT / slides / 演示稿 / 幻灯片 from their compiled KB content AND wants a
  dark, high-tech, neon / glow / glassmorphism look (赛博 / 科技风 / 暗色 /
  霓虹 / 炫酷). Generates a polished single-file HTML deck in the Aurora Glass
  visual direction (near-black background, teal/sky/magenta/amber neon accents,
  glassmorphism panels, aurora gradient atmosphere) — opened in a browser,
  full-screened, shared. For the warm, printed, serif look use
  openkb-deck-editorial instead. Does NOT apply to generating skills
  (`openkb skill new`), research reports, or scrolling long-form documents.
od:
  mode: deck
  output_path_template: "output/decks/{slug}/index.html"
  deck_grammar:
    kind_attr: data-type
    required: [cover, closing]
    allowed: [cover, chapter, thesis, quote, compare, data, closing]
    min_distinct: 4
    max_consecutive_same: 2
---

# Aurora Glass deck skill

You are designing a presentation, not writing a research report. Each slide
carries one idea. In this visual direction, **light, color and glow** organize
the slide — dark canvas holds it, neon points the eye, glass adds depth.

## How this skill is invoked

The user typed something like "make a deck about X" (and asked for a dark /
neon / 科技 look) inside `openkb chat`. You have wiki-read tools in your normal
tool set, plus a `write_file` tool that can write under `output/**`, plus a
tool to read this SKILL.md and files in `skills/openkb-deck-neon/` if needed.

Pick a kebab-case slug (e.g. `okf-pitch`) and write the output to
`output/decks/<slug>/index.html`.

## Required output

Exactly one file: `output/decks/<slug>/index.html`.

It must be **self-contained**: NO external `<link rel="stylesheet">`, NO
external `<script src>`, NO remote `<img>`, **and NO web fonts** (no Google
Fonts link — that is an external link and breaks self-containment). All CSS in
one inline `<style>`; keyboard-nav JS in one inline `<script>` at end of
`<body>` (no scaling script needed — CSS fills the viewport). Use the local font stacks in §Type system — do not reach for Inter,
Chakra Petch, Orbitron, or any `fonts.googleapis.com` import.

The body is a sequence of `<section class="slide" data-type="...">` blocks;
each `data-type` is one of the 7 values in §Slide grammar. Keyboard nav:
← / → move between slides, `F` toggles fullscreen, `P` triggers print.

## Design system: Aurora Glass

Use this fixed design system. Do not improvise nearby colors, do not add a
fifth accent hue, do not bring in emojis. Glow and gradient are allowed here
(unlike the editorial skill) — but they are seasoning, not the meal.

### Color palette

```css
:root {
  --bg:      #080b11;  /* near-black blue — the canvas, never pure #000 */
  --bg-elev: #0f141d;  /* raised surface / slide inner */
  --ink:     #eef2f7;  /* primary text — body copy uses THIS, not muted */
  --soft:    #aeb8c7;  /* secondary text */
  --muted:   #69748a;  /* labels / folio / metadata */
  --line:    rgba(255,255,255,.09);  /* hairline borders */
  --glass:   rgba(255,255,255,.04);  /* glassmorphism fill */
  --teal:    #2dd4bf;  /* PRIMARY accent */
  --sky:     #38bdf8;  /* secondary accent */
  --magenta: #e879f9;  /* tertiary accent */
  --amber:   #f6b94b;  /* highlight / numbers-in-code only */
}
```

These 4 neon hues + the neutrals are the **only** colors. No purple-on-white
gradients, no rainbow, no per-slide recoloring.

### Type system (local fonts only — NO web fonts)

```css
--font-display: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto,
                "PingFang SC", "Microsoft YaHei", sans-serif; /* heavy weights */
--font-mono:    ui-monospace, "SF Mono", "JetBrains Mono", Menlo, Consolas,
                monospace;  /* labels, numbers, code, kickers */
```

Display titles use `--font-display` at weight 800 with `letter-spacing:-.02em`.
The tech character here comes from **mono for every label / number / kicker /
code**, not from a fancy downloaded display face.

Type scale — use `clamp()` so type scales with the viewport (fixed px look tiny
on a 2560px screen). vw drives the middle value; the rem caps keep it sane:
* `--type-display`: clamp(2.8rem, 5.2vw, 5.5rem) / 1.05  — cover/chapter titles
* `--type-title`:   clamp(1.9rem, 3.2vw, 3rem)    / 1.12 — normal slide titles
* `--type-body`:    clamp(1.05rem, 1.4vw, 1.4rem) / 1.6  — body copy (var(--ink))
* `--type-quote`:   clamp(1.6rem, 2.6vw, 2.2rem)  / 1.35 — pull quotes
* `--type-label`:   size `clamp(.66rem,.8vw,.8rem)`, `letter-spacing:.22em`, uppercase, mono — label tracks

### Atmosphere (fixed background layers on every slide)

1. **Aurora**: 3–4 blurred `radial-gradient`s in teal / sky / magenta / amber,
   low alpha (.10–.16), `filter: blur(14px)`. Positioned off the title.
2. **Grain**: an inline SVG `feTurbulence` noise data-URI at `opacity:.04`
   (data-URI is inline, NOT an external image — allowed).
3. Optional faint dot-grid via `radial-gradient` background, alpha < .06.

These three layers are what stop the dark background from looking flat/cheap.

### Frame (every slide)

* **Every slide fills the entire viewport — edge to edge, no letterbox, no
  fixed card.** The slide IS the window; design for a wide canvas and let
  flexbox + `clamp()` sizing adapt to any window ratio:
  ```css
  html,body{height:100%;margin:0;overflow:hidden;background:var(--bg)}
  .slide{position:fixed;inset:0;display:none;flex-direction:column;
    justify-content:center;
    padding:clamp(40px,5vh,72px) clamp(56px,5vw,128px)}
  .slide.active{display:flex}
  ```
  No scaling/transform tricks — CSS alone fills the viewport. On an ultrawide
  window the slide is simply wider than 16:9; content reflows and the dark
  background + aurora bleed to all four edges — never a centered card with dark
  side-bands. One slide visible at a time; ← / → swap the active slide, `F`
  fullscreen, `P` print (they do not scroll).
* The aurora, dot-grid, top label row and the bottom signature bar all span the
  FULL viewport width (they live on `.slide`, which is the whole window). Inner
  padding uses the `clamp()` values above so content breathes on any size.
* **One screen, no scroll — content MUST fit the viewport height.** With
  `overflow:hidden` + `justify-content:center`, a slide taller than the window
  is clipped at BOTH top and bottom and is unreachable. Stay within the
  bullet/word caps (§Failure modes), lean on the `vh` terms in the type scale so
  text shrinks on short viewports, and if a slide would overflow, CUT content —
  never let it clip.
* **Visual signature:** a glowing 3px bar along the bottom edge, gradient
  `teal → sky → magenta`, with `box-shadow:0 0 14px` of --teal. This is the
  deck's signature — present on every slide.
* Top label row (mono, --muted): left = chapter id ("CHAPTER 03"), right =
  source mark. Cover/closing use "OPENKB" on the left instead.
* Bottom folio row (mono): left = `N / Total`, right = source short label.

### Glow & glass rules (read this — it is the #1 failure mode)

* **Glow only the protagonist** of a slide: the display title, the one big
  number, graph nodes. Body copy, labels, tables NEVER glow. A slide where
  everything glows reads as blurry mush.
* Glass (`--glass` fill + `backdrop-filter:blur(8px)` + 1px --line border +
  subtle inset highlight) is for **compare columns and data cards only**.
* Gradient text (teal→sky→magenta via `background-clip:text`) is reserved for
  cover / chapter / closing big titles and `data` numbers — not for body.

### Composition rules

**Use the WHOLE canvas — never a left sliver with a big empty right half.**
This is the #1 layout failure under fill-viewport. Every slide must span the
width via ONE of these (vary them across the deck):

* **Split** — default for `thesis` / `chapter` / `closing` (and `cover` when it
  has a visual). A 2-column grid that fills the slide:
  `.inner{display:grid; grid-template-columns:1.05fr .95fr;
  gap:clamp(40px,5vw,96px); align-items:center; width:100%;
  max-width:min(1600px,92vw); margin:0 auto}`. Text in one column; a
  **first-class VISUAL in the other** — a big labeled stat, a schema/spec
  table, stacked labeled cards, or a simple diagram — sized `width:100%` to
  fill its column, NEVER a thumbnail in a corner.
  **Build the visual with HTML + CSS (flex/grid); put every label in its own
  HTML box** so boxes auto-wrap and auto-space and never collide. Do NOT put
  text in `<svg><text>` — SVG text does not wrap or reflow, so multiple labels
  in a narrow column overlap into an unreadable smear. Use SVG ONLY for
  lines / arrows / shapes between the HTML boxes (or skip SVG). Keep it to ≤4
  nodes/cards with SHORT labels (node ≤2 words, caption ≤4 words); if labels run
  long or there are many, use a VERTICAL stack of full-width rows, never a
  cramped horizontal graph.
* **Full-bleed headline** — `cover`, big `thesis`. The gradient display title
  spans ~70vw across the upper canvas; subtitle + a thin full-width detail row
  (kicker / stat strip / row of chips) beneath. The title fills the width, so
  there is no right-hand void.
* **Centered focus** — `data`, `quote`. `.inner{max-width:min(900px,72vw);
  margin:0 auto; text-align:center}` with SYMMETRIC breathing room on both
  sides (never one-sided dead space).
* **`compare`** — `.inner{max-width:min(1180px,90vw); margin:0 auto;
  display:grid; grid-template-columns:1fr 1fr; gap:clamp(24px,3vw,56px)}`, with
  the glowing teal rule between columns.

**Per-slide self-test: is there a large empty right half? Then the layout is
wrong** — widen the headline, add the visual column, or center it. The closing
diagram, compare columns, and any flow/graph MUST fill their region, never
shrink to a corner. Never size a content box to `min-content`/`fit-content`
(collapses to a sliver).

* `data` big number: give it its OWN width — `display:inline-block;
  width:max-content; max-width:92vw; white-space:nowrap; margin:0 auto` — so it
  is NOT clipped by the `.inner` text column, and (with `background-clip:text`)
  the gradient covers the WHOLE string instead of stopping at the column edge
  and leaving the last char unpainted/smeared under the glow. Size a SINGLE
  number with `clamp(3.5rem,11vh,9rem)`; a COMPARISON number (`A → B`, `A vs B`)
  is far wider — drop it to `clamp(2.4rem,7vh,5.5rem)` and keep the whole string
  (incl. the trailing `%`) inside 92vw, never overflowing. Body beneath stays
  centered in the `.inner` as a readable ~3-line paragraph.
* Cover/chapter titles never wrap an article ("the"/"an"/"to") onto its own line.
* Contrast: body is `--ink` on `--bg`; never body copy in `--muted`/`--soft`
  (exception: `quote` pull-quotes and cover/closing subtitles may be `--soft`).

## Slide grammar (7 permitted `data-type` values)

| `data-type` | Use | Neon signature |
|---|---|---|
| `cover`   | tag + huge gradient title + 1-line subtitle | strongest aurora; mono "OPENKB" top-left; gradient display title |
| `chapter` | section divider: oversize number + name | number `clamp(4rem,12vh,9rem)` mono, teal with glow; name in `--type-display` |
| `thesis`  | one claim + short explanation | title fills ~60% height; ONE keyword in teal+glow; rest --ink |
| `quote`   | italic pull-quote + attribution | centered; left teal glowing vertical rule; quote in --soft |
| `compare` | two-column comparison, 3–5 lines each | two glass panels; glowing teal vertical rule between them |
| `data`    | one number + label + one-line read | single number `clamp(3.5rem,11vh,9rem)`; comparison `A → B` `clamp(2.4rem,7vh,5.5rem)`; `width:max-content;max-width:92vw` (per §Composition); micro-copy in `--type-label` |
| `closing` | mirrors cover; thanks / next step | same scale as cover; aurora dims toward calm |

Cover/closing have no chapter context → top-left label is "OPENKB".

## Working method

1. **Survey first.** Use wiki-read tools to list `concepts/` and `summaries/`
   and read `wiki/index.md`. Build a mental map before deciding the argument.
2. **Choose a narrative arc.** One-line thesis, then an 8–12 step arc. Each
   step → 1–2 slides, landing 8–15 slides total.
3. **Read the relevant content.** For each concept the arc touches, read the
   concept page; for cited documents read a targeted slice. **The deck is only
   as expert as the source-reading you do here.** Generic restatements are a
   failure mode — name a specific technique, number, or quote on each slide.
4. **Outline slides** with concrete `data-type` assignments. Vary types — ≥4
   distinct, no run of 3+ consecutive same type.
5. **Write** `output/decks/<slug>/index.html` in one `write_file` call. Inline
   all CSS, inline keyboard-nav JS, inline `<svg>` for any graphics, inline
   noise as a data-URI. No external anything.
6. **Revise** against §Failure modes; touch at least one slide if any match.
7. **Self-check** the invariants below; fix anything failing.
8. Report the deck path + a one-line summary of the arc.

## Failure modes (negative checklist)

1. **External fonts/assets** — any `fonts.googleapis.com`, `<script src>`,
   `<link>`, or remote `<img>`. Breaks self-containment AND the deck validator.
   Use the local stacks; the look does not depend on a downloaded font.
2. **Glow soup** — more than the protagonist glowing. Body/labels/tables flat.
3. **Low contrast** — body text in --muted/--soft on dark. Body is --ink.
4. **Palette drift** — any hue outside teal/sky/magenta/amber + neutrals;
   purple-gradient-on-white; emoji; per-slide recoloring.
5. **Bullet dump** — >5 bullets. Cut to 3 or restructure into compare/data.
6. **Wall of text** — slide body >~80 words. Cut or split.
7. **Visual monotony** — 3+ consecutive slides of one `data-type`.
8. **Decorative-only viz** — an SVG/graph that says nothing. If you draw a
   graph, its nodes/edges must encode real concepts/links from the KB. On a
   single slide prefer a small, exact, hand-placed SVG over a fake "force layout".
9. **Generic titles** — "Introduction"/"Background". Titles carry content.
10. **Definition-grade content** — "X is Y where Y is…" with no named
    technique, number, or source quote. Re-read sources (step 3) first.
11. **Letterbox / fixed-size card** — capping the slide with `max-width` or a
    fixed `1280×720` box, leaving dark side-bands on a wide window. Slides are
    `position:fixed;inset:0` and fill the viewport edge-to-edge (§Frame).
12. **Collapsed content column** — a centered / `data` box sizing to
    `min-content` or a tiny `max-width`, squashing text into a narrow sliver on
    a wide screen (and breaking big numbers). Wrap content in an `.inner` with
    an explicit `max-width` (§Composition); big numbers `white-space:nowrap`.
13. **Invalid SVG sizing** — `height="auto"` written as an SVG *attribute*
    (invalid; fires a console error). Give inline SVG a `viewBox` and size it
    with CSS (`width:100%;height:auto` in a `style`) or fixed `width`/`height`
    attributes — never `height="auto"` as an attribute.
14. **Left sliver / right void** — content crammed into a left column with a
    large empty right half (the worst look). Use a split / full-bleed / centered
    layout (§Composition) so the right half is content or symmetric space.
15. **Overflowing big number** — a `data` comparison number (`A → B`/`A vs B`)
    at the single-number font size overruns its container; the trailing `%`
    falls outside the `background-clip:text` gradient and smears under the glow.
    Give the number `width:max-content; max-width:92vw` and the smaller
    comparison font size (§Composition).
16. **Overlapping diagram labels** — text placed in `<svg><text>` (which never
    wraps) so multiple labels in the narrow visual column collide into a smear.
    Build the visual in HTML/CSS with each label in its own auto-spaced box;
    use SVG for lines/shapes only, ≤4 nodes, short labels (§Composition).

## Self-check (before reporting back)

1. Does `output/decks/<slug>/index.html` exist with NO external
   `<link>`/`<script src>`/web-font import/remote `<img>`?
2. At least one `data-type="cover"` and one `data-type="closing"`?
3. Total slide count between 8 and 15?
4. At least 4 distinct `data-type` values, no run of 3+ same?
5. Is body copy in `--ink` (readable on dark) — `quote` pull-quotes and
   cover/closing subtitles in `--soft` being the only allowed exceptions — and
   is glow limited to titles / big numbers / graph nodes only?
6. Do slides fill the viewport edge-to-edge (`position:fixed;inset:0`, no
   `max-width` / fixed `1280px` box, no letterbox side-bands)?
7. Is content wrapped in an `.inner` with an explicit `max-width` — no narrow
   sliver / `min-content` collapse, and big numbers `white-space:nowrap`?
8. Does every slide use the full width (split / full-bleed / centered) with NO
   large empty right half, and do text-heavy slides carry a visual?

If any answer is no, revise and re-run this self-check.
