---
name: openkb-deck-editorial
description: |
  Use when the user asks the openkb chat to make a deck / slide presentation /
  PPT / slides / 演示稿 / 幻灯片 from their compiled KB content. Generates a
  polished single-file HTML deck in the Editorial Monocle visual direction
  (warm cream background, serif type, brick-red accent) — designed to be
  opened in a browser, full-screened, and shared. Does NOT apply to
  generating skills (that's `openkb skill new`), long-form research reports,
  or interactive prototypes.
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

# Editorial Monocle deck skill

You are designing a presentation, not writing a research report. Each
slide carries one idea. Visual structure carries the narrative.

## How this skill is invoked

The user typed something like "make a deck about X" inside `openkb chat`.
You have wiki-read tools in your normal tool set, plus a `write_file`
tool that can write under `output/**`, plus a shell tool you can use to
read this SKILL.md and any files in `skills/openkb-deck-editorial/` if
needed.

Pick a kebab-case slug for the deck (e.g. `transformers-pitch`) and
write the output to `output/decks/<slug>/index.html`.

## Required output

Exactly one file: `output/decks/<slug>/index.html`.

It must be **self-contained**: no external `<link rel="stylesheet">`,
no external `<script src="…">`, no remote `<img>`. All CSS goes in a
single inline `<style>` in `<head>`. Helper JS for keyboard navigation
goes in a single inline `<script>` at end of `<body>`.

The body is a sequence of `<section class="slide" data-type="...">`
blocks. Each `data-type` must be one of the 7 values listed in §
"Slide grammar" below. The deck supports keyboard navigation: ← / →
move between slides, `F` toggles fullscreen, `P` triggers print.

## Design system: Editorial Monocle

Use this fixed design system. Do not improvise nearby colors, do not
introduce gradients, do not bring in emojis. This is the **only**
non-monochrome palette in the entire deck.

### Color palette

```css
:root {
  --bg:        #f3eee1;  /* oklch(94% 0.03 80)  — warm cream paper */
  --ink:       #1a1612;  /* oklch(15% 0.01 50)  — warm near-black */
  --muted:     #7a6e55;  /* oklch(55% 0.04 75)  — labels / metadata */
  --rule:      #d4cfc0;  /* oklch(82% 0.02 75)  — thin separator */
  --accent:    #a4341c;  /* oklch(45% 0.16 30)  — brick red, the ONLY non-monochrome */
  --highlight: #fff3a8;  /* oklch(95% 0.10 95)  — marker highlighter ONLY */
}
```

### Type system

```css
font-family-serif:  "Charter", "Iowan Old Style", "Times New Roman", Georgia, serif;
font-family-sans:   "Inter", -apple-system, "Helvetica Neue", sans-serif;  /* labels only */
```

Type scale (size / line-height / letter-spacing):

* `--type-display`: 56px / 1.05 / -1px      — cover/chapter big titles
* `--type-title`:   38px / 1.10 / -0.5px    — normal slide titles
* `--type-body`:    18px / 1.55 / 0         — body copy
* `--type-quote`:   28px / 1.30 / -0.3px    italic — pull quotes
* `--type-label`:   10px / 1.0 / 2.5px      uppercase — top/bottom label tracks

### Frame (every slide)

* 16:9 aspect ratio: `aspect-ratio: 16/9; width: 100vw; max-width: 1280px;`
* Per-slide padding: 64px top/bottom, 80px left/right.
* **10px brick-red bar on the right edge of every slide.** The deck's
  visual signature. 4px reads as invisible at presentation scale.
* Top label row: left = chapter id (e.g. "CHAPTER 03"), right = source mark.
* Bottom folio row: left = `N / Total`, right = source short label.

### Composition rules

* **Cover title (`.display`)** must use `max-width: 18ch` (NOT 10ch).
  Never wrap an article ("the", "an", "to") onto its own line.
* **Data slides** must center the big number horizontally on the slide
  (`.data-body { align-items: center; text-align: center }` for
  `data-type="data"` only — leave other slide types left-aligned).
  Body copy beneath stays centered, max-width 38em.
* **Cover and closing slides**: `.cover-body, .closing-body { max-width: 26em }`.

### Keyboard nav hint

```css
.kbd { opacity: 0; transition: opacity .25s ease; }
body:hover .kbd { opacity: .55; }
```

## Slide grammar (7 permitted `data-type` values)

| `data-type` | Use | Visual signature |
|---|---|---|
| `cover`   | First slide: tag + huge title + 1-line subtitle | Display type, left-aligned, never centered |
| `chapter` | Section divider: oversize number + chapter name | Number 120px brick-red, name 38px serif |
| `thesis`  | A single claim + a short explanation | Title fills ~60% height, explanation small bottom |
| `quote`   | Italic pull-quote + attribution | Centered, serif italic 28px, generous whitespace |
| `compare` | Two-column comparison: header + 3-5 lines each side | 1px brick-red vertical rule between columns |
| `data`    | One number + label + one-line interpretation | Number 120-160px brick-red, micro-copy 12px |
| `closing` | Mirrors `cover`; thanks / next steps | Same scale as cover but content closes the arc |

**Cover/closing exception:** the `cover` and `closing` slides have no
chapter context, so the top-left label is the deck identifier
("OPENKB") instead of a `CHAPTER NN` id.

## Working method

1. **Survey first.** Use your wiki-read tools to list `concepts/` and
   `summaries/`, and read `wiki/index.md`. Form a mental map before
   committing to what the deck argues.
2. **Choose a narrative arc.** Write a one-line thesis, then an 8-12
   step arc (problem → tension → resolution, or whatever shape the
   intent calls for). Each step becomes 1-2 slides, landing the final
   deck in the 8-15 range required by §Self-check.
3. **Read the relevant content.** For each concept the arc touches,
   read the concept page. For each document a concept cites, read at
   least one targeted slice of the source. **This is where the
   specific arguments, named techniques, worked examples, and
   counter-cases live. The deck is only as expert as the depth of
   source reading you do here. Generic restatements of the topic are
   a failure mode — your deck will read as a definition-grade
   summary, not an expert briefing.**
4. **Outline the slides.** Map each step to one or more slides with
   concrete `data-type` assignments. Vary `data-type` — at least 4
   distinct types, no run of 3+ consecutive same type.
5. **Write `output/decks/<slug>/index.html`** in one `write_file`
   call. Inline all CSS, inline the keyboard nav JS, use inline
   `<svg>` only for any graphics (v1 does not embed bitmap images).
6. **Revise.** Re-read against §Failure modes below; touch at least
   one slide if anything matches.
7. **Self-check** the 5 invariants in §Self-check; fix anything that
   fails.
8. Report back to the user with: the deck path and a one-line summary
   of the arc you chose.

## Failure modes (negative checklist)

1. **Bullet dump** — slide with > 5 bullet points. Cut to 3 strongest
   or restructure into a `compare` / `data` slide.
2. **Wall of text** — slide body > ~80 words. Cut, or split.
3. **Visual monotony** — 3+ consecutive slides with the same `data-type`.
4. **Centered everything** — only `quote` and `closing` are centered.
5. **AI slop palette** — any color outside the 6-value palette: no
   blue/purple gradients, no emoji, no rainbow accents.
6. **Generic titles** — "Introduction" / "Background" / "Conclusion"
   as a slide title. Title must carry specific content.
7. **Definition-grade content** — slide body is just "X is Y where Y
   is …" with no named technique, no number, no concrete example, no
   quote from the source. If you can't name *something specific* on a
   slide, the wiki may not have the depth — re-read the source pages
   (step 3) before settling for a definition.

## Self-check (before reporting back)

1. Does `output/decks/<slug>/index.html` exist and contain no external
   `<link>` or `<script src=>`?
2. Is there at least one `data-type="cover"` and one
   `data-type="closing"`?
3. Is the total slide count between 8 and 15?
4. Are at least 4 distinct `data-type` values used?
5. Is there no run of 3+ consecutive slides with the same `data-type`?

If any answer is no, revise and re-run this self-check.
