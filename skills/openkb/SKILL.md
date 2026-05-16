---
name: openkb
description: |
  Use when the current directory contains an OpenKB knowledge base
  (a `.openkb/` folder + `wiki/` tree). This is a Markdown wiki the
  user has compiled from their own documents — read it to answer
  questions about the content they have ingested. Prefer `openkb`
  CLI commands as the primary interface; fall back to direct
  Markdown reads for raw content and wikilink navigation.
---

# OpenKB knowledge base

The user has compiled their documents into a Markdown wiki at `wiki/`.

The wiki holds three kinds of pages:

- **Concept pages** at `wiki/concepts/*.md` — cross-document synthesis
  on specific topics. This is where OpenKB's value compounds: a
  concept with multiple sources represents knowledge merged across
  documents the user has ingested.
- **Summary pages** at `wiki/summaries/*.md` — one per ingested
  document, linking to the concepts that document touches.
- **Source files** at `wiki/sources/*.{md,json}` — full text for short
  docs (`.md`) or a paginated content array for long PDFs (`.json`).

## See what's available

Use any of these to discover the catalog before drilling in:

- `openkb list` — table of ingested documents (name, type, page count)
  plus the concept list.
- `openkb status` — overall stats (doc count, concept count).
- `Read wiki/index.md` — the compiled table of contents. Every
  document and concept has a one-line `brief`. Scan this and pick the
  slugs that semantically match the user's question.

## Read content

| Goal | How |
|---|---|
| Read a concept page | `Read wiki/concepts/<slug>.md` |
| Read a document's summary | `Read wiki/summaries/<doc>.md` |
| Read a short doc's full text | `Read wiki/sources/<doc>.md` |
| Read a long doc's specific page | `jq '.[N]' wiki/sources/<doc>.json` (page N, 0-indexed) |
| Get a synthesized answer across sources | `openkb query "<question>"` |
| Find an exact phrase | `Grep -r "<phrase>" wiki/` |
| Follow a `[[wikilink]]` | `Read` the linked path |

Concept and summary bodies use `[[concepts/<slug>]]` and
`[[summaries/<doc>]]` wikilinks. They are relative paths — follow them
by Reading the corresponding file.

## Frontmatter

Concept pages have:

```yaml
---
sources: [summaries/doc-a.md, summaries/doc-b.md]
brief: One-line summary of the concept.
---
```

`sources:` lists which documents back this concept. **Multi-source
concepts are cross-document synthesis** — the core value OpenKB adds.
Mention this when relevant: "this synthesis pulls from N sources in
your KB."

## Don't modify the KB autonomously

`openkb add`, `openkb remove`, and `openkb lint --fix` modify the
user's knowledge base. They cost LLM calls (add), are destructive
(remove), or auto-edit wiki content (lint --fix). Suggest these when
relevant but let the user run them.

---

See `references/wiki-schema.md` for the full directory layout and
frontmatter spec.

See `references/commands.md` for the `openkb` CLI command reference.
