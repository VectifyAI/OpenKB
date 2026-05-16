# OpenKB CLI command reference

Only the commands relevant to **reading** an OpenKB knowledge base
are listed here. Write commands (`add`, `remove`, `lint --fix`, etc.)
should be suggested to the user, not run autonomously by the agent.

## `openkb list`

List ingested documents and their compiled concepts.

```
$ openkb list
Documents (2):
  Name                                     Type         Pages
  ---------------------------------------- ------------ --------
  paper.pdf                                pageindex    42
  notes.md                                 short

Summaries (2):
  - paper
  - notes

Concepts (5):
  - attention
  - transformer
  - positional-encoding
  - self-attention
  - multi-head-attention
```

- `Type` is the *display* form of the registry's `type` field, mapped
  through `_TYPE_DISPLAY_MAP`:
    - PageIndex-indexed long PDFs (registry `type: long_pdf`) display
      as `pageindex`.
    - Every other format (`md`, `docx`, `pdf` short, `txt`, …) displays
      as `short`.
  The raw registry value lives in `.openkb/hashes.json`; the displayed
  value is what surfaces in `openkb list` and in `index.md` type tags
  (`(short)` / `(pageindex)`).
- `Pages` only populated for long PDFs.
- The Summaries and Concepts lists are simply directory listings of
  `wiki/summaries/` and `wiki/concepts/` minus their `.md` suffix.

## `openkb status`

Knowledge base overview. **Always run this first** when working with
an OpenKB KB — its first line tells you where the KB lives, which is
what you need for every `Read` / `Grep` / `jq` call afterwards.

```
$ openkb status
Knowledge base: /path/to/kb

Knowledge Base Status:
  Directory            Files
  -------------------- ----------
  sources              5
  summaries            5
  concepts             12
  reports              2
  raw                  5

  Total indexed: 5 document(s)
  Last compile:  2026-05-16 12:14:12
  Last lint:     2026-05-16 12:16:31
```

- The `Knowledge base: <path>` line is parseable: it's the absolute
  path of the active KB. The user may have invoked you from anywhere
  — never assume cwd is the KB root; use this path.
- Resolution: walks up from cwd looking for `.openkb/`, then falls
  back to the global default set by `openkb use`.
- Empty case: prints "No knowledge base found. Run `openkb init`
  first." Tell the user this and stop — don't try to read files.

## `openkb query "<question>"`

Run a full retrieval-augmented query against the wiki. Returns a
synthesized answer with citations. **Costs an LLM call inside
OpenKB**, so use this when the user explicitly wants a synthesized
answer across the whole KB, not for simple lookups that can be
answered by reading directly.

```
$ openkb query "How does self-attention scale with sequence length?"
Self-attention is O(n²) in sequence length because every token attends
to every other token...

Sources:
- [[concepts/self-attention]]
- [[summaries/transformers]] (sources/transformers.md)
```

Add `--save` to persist the answer at
`wiki/explorations/<slug>.md` — but only when the user asks for it.

## Read-only commands NOT typically needed from a skill

- `openkb chat` — interactive REPL, not appropriate for skill usage
- `openkb watch` — daemon for auto-ingesting from `raw/`
- `openkb lint` — health check; produces a report file. Don't run
  unless the user explicitly asks about wiki health.

## Write commands — DO NOT run autonomously

These mutate the user's knowledge base:

- `openkb add <path>` — ingest a new document (LLM cost)
- `openkb remove <doc>` — destructive removal
- `openkb lint --fix` — auto-edits wiki pages
- `openkb init` — one-time setup
- `openkb use <path>` — sets the default KB

Suggest these to the user with a sentence explaining what they do, but
do not invoke them yourself.

## How to identify "is this an OpenKB knowledge base?"

Look for a `.openkb/` directory alongside `wiki/` in the user's cwd
(or an ancestor). The presence of `.openkb/config.yaml` confirms it.
If the user's question is about content but no KB is present, suggest
they `openkb init` and `openkb add` their documents.
