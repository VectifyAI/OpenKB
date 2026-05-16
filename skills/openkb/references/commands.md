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
  paper.pdf                                long_pdf     42
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

- `Type` shows the registry's `type` field: `long_pdf` for
  PageIndex-indexed PDFs, otherwise the file extension (`md`,
  `docx`, `pdf`, …).
- `Pages` only populated for long PDFs.
- The Summaries and Concepts lists are simply directory listings of
  `wiki/summaries/` and `wiki/concepts/` minus their `.md` suffix.

## `openkb status`

Knowledge base overview (run from inside a KB directory).

```
$ openkb status
Knowledge base at /path/to/kb
  Documents:   2  (long_pdf: 1, short: 1)
  Concepts:    5
  Last ingest: 2026-05-16 12:14:12  (paper.pdf)
```

Use this as a first read when the user asks "what does your KB look
like?" or "how big is the KB?".

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
