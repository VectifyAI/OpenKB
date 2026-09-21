# OpenKB CLI reference

Read commands the skill calls on. Write commands are listed at the
bottom — the agent MUST NOT run them autonomously.

## `openkb status`

KB overview. First line carries the absolute path of the active KB
— parse it before any file read:

```
$ openkb status
Knowledge base: /path/to/kb
Knowledge Base Status:
  ...directory counts and timestamps...
```

Resolution: walks up from cwd, then falls back to `openkb use`'s
global default. Empty case prints "No knowledge base found. Run
`openkb init` first." — stop and tell the user; don't try to read.

## `openkb list-taxonomy [--kind concept|entity] [--json]`

Concept/entity pages with their one-line briefs, for semantic browsing
(pick a slug by meaning, not keyword search). Omit `--kind` for both.

```
$ openkb list-taxonomy
[concept] attention — Mechanism for weighting input relevance.
[entity] ada-lovelace (person) — Early computing pioneer.
```

## `openkb search-taxonomy "<term>" [--kind concept|entity] [--top-k N] [--json]`

Ranks concept/entity pages by BM25 match against their slug + one-line
brief (never the full body). Use instead of `list-taxonomy` once a KB has
too many taxonomy items to scan by eye — check counts via `openkb status`
first. `--top-k` defaults to 20 (higher than `search`'s default, since a
brief is short). Omit `--kind` for both.

```
$ openkb search-taxonomy "attention"
[concept] attention — Mechanism for weighting input relevance. (score: 4.82)
```

## `openkb list-documents [--kind summary|exploration] [--json]`

Summary/exploration pages with their one-line briefs. An exploration's
brief is its originally-saved question. Omit `--kind` for both.

## `openkb search "<term>" [--scope briefs,summaries,sources,explorations] [--top-k N] [--json]`

Tiered BM25 search. `--scope` is a **comma-separated** list (not a
repeated flag) — omit for all four tiers. Never covers concepts/entities
— use `list-taxonomy` for those. Each hit under `sources` carries a
`locator` (`line` for a short doc, `page` for a long PageIndex `.json`).

## `openkb list`

Deprecated — prefer `list-taxonomy`/`list-documents` above, which have
briefs, a `--kind` filter, and `--json` output. Kept only for the
plain Documents/Summaries/Concepts/Entities table it prints.

## `openkb query "<question>"`

Full RAG pipeline — costs an LLM call inside openkb. Use only when
no obvious slug matches and direct reads can't answer. Returns
free-form answer text plus cited `[[concepts/...]]` / `[[summaries/...]]`
paths. Add `--save` to persist to `wiki/explorations/<slug>.md` —
only when the user asks for it.

## Read-only commands the skill should NOT call

- `openkb chat` — interactive REPL
- `openkb watch` — daemon
- `openkb lint` — health-check report (run only if the user
  explicitly asks about wiki health)

## Write commands — MUST NOT run autonomously

These mutate the user's knowledge base. Suggest with a one-line
description of what they do; let the user run them:

- `openkb add <path>` — ingest a document (LLM cost, modifies wiki)
- `openkb remove <doc>` — destructive removal
- `openkb lint --fix` — auto-edits wiki pages
- `openkb init` — one-time KB setup
- `openkb use <path>` — set the default KB

Also: never directly `Edit`/`Write` any file under `<kb>/wiki/` or
`<kb>/.openkb/`. That's the user's curated content (and openkb's
internal state) — the agent must not patch it directly.
