# Command reference

The everyday loop. Every command resolves the active KB the same way (see
[`configuration/`](../configuration/#4-where-is-the-kb)), so you can run them from
anywhere once a KB is initialized.

> **Real artifact in this folder:** [`sample-wiki/`](sample-wiki/) is a complete
> wiki compiled by `openkb add` from one paper —
> [1 summary](sample-wiki/summaries/attention-is-all-you-need.md),
> [3 concepts](sample-wiki/concepts/), [9 entities](sample-wiki/entities/), and a
> saved [`query --save`](sample-wiki/explorations/) answer. Notice how every page
> is stitched together with `[[wikilinks]]` — that cross-linking *is* the
> knowledge graph you see in [`visualize/`](../visualize/).

| Command | Purpose | Key flags |
| --- | --- | --- |
| `add <path\|dir\|URL>` | Ingest documents | `--from-pageindex-cloud` |
| `query <question>` | One-off question | `--save`, `--raw` |
| `remove <id>` | Delete a document | `--keep-raw`, `--keep-empty`, `--dry-run`, `--yes` |
| `recompile [doc]` | Re-run the compile pipeline | `--all`, `--dry-run`, `--yes`, `--refresh-schema` |
| `lint` | Check wiki integrity | `--fix` |
| `list` | Show indexed docs & pages | – |
| `status` | KB stats + root path | – |
| `watch` | Auto-ingest files dropped in `raw/` | – |
| `feedback [msg]` | File a prefilled GitHub issue | `--type` |

---

## `add` — ingest documents

```bash
openkb add ../docs/attention-is-all-you-need.pdf   # a single file
openkb add ~/papers/                               # a directory (recursive)
openkb add https://arxiv.org/pdf/2509.11420        # a URL
openkb add --from-pageindex-cloud <DOC_ID>         # an already-indexed cloud doc
```

- **Supported formats:** `.pdf .md .markdown .docx .pptx .xlsx .xls .html .htm
  .txt .csv` (plus URLs).
- **URLs** are sniffed by content type: PDFs are downloaded and indexed; HTML is
  run through a main-content extractor (trafilatura) and ingested as Markdown.
- **Long vs. short PDFs** are split by `pageindex_threshold` — see
  [`pageindex-cloud/`](../pageindex-cloud/).
- **Idempotent:** a document is registered by content hash only after it compiles
  successfully, so re-adding the same file is skipped and a failed add can be
  retried.

Compiling [`../docs/attention-is-all-you-need.pdf`](../docs/attention-is-all-you-need.pdf)
is what produced [`sample-wiki/`](sample-wiki/) in this folder.

---

## `query` — ask a one-off question

```bash
openkb query "What are the main contributions of this paper?"
openkb query "Compare the two training objectives" --save
openkb query "How does this KB work?" --raw | less
```

- `--save` writes the answer to `wiki/explorations/<slug>.md` with a `query:`
  frontmatter field, so good answers become part of the wiki.
- `--raw` prints raw Markdown (no rich rendering) — useful for piping.
- Output streams in a terminal and switches to a plain final answer when piped or
  redirected, so it's safe in scripts.

> **See it:** a real `--save` result is in
> [`sample-wiki/explorations/`](sample-wiki/explorations/).

---

## `remove` — delete a document

Identify a doc by filename, its slug, or a unique substring:

```bash
openkb remove attention-is-all-you-need.pdf     # exact filename
openkb remove attention                         # unique substring
openkb remove attention --dry-run               # preview, change nothing
openkb remove attention --keep-empty            # keep concept & entity pages it solely sourced
openkb remove attention --keep-raw --yes        # leave raw/ file, no prompt
```

`remove` deletes the summary, sources, and extracted images; drops the doc from
every concept/entity page's `sources:` (deleting pages whose **only** source was
this doc, unless `--keep-empty`); prunes `index.md` and the hash registry; and
runs a scoped `lint --fix` to clean any dangling `[[wikilinks]]`. For long local
PDFs it also clears the PageIndex state. Use `--dry-run` first when unsure.

---

## `recompile` — regenerate wiki pages

Re-runs the compile step against already-ingested content (no re-conversion, no
re-indexing):

```bash
openkb recompile attention                      # one document
openkb recompile --all --dry-run                # preview the full set
openkb recompile --all --yes                    # rebuild everything
openkb recompile --all --refresh-schema         # also refresh wiki/AGENTS.md
```

> ⚠️ Recompiling **overwrites** generated summaries and concept pages — any manual
> edits to those are lost. `--refresh-schema` backs up the old `AGENTS.md` to
> `AGENTS.md.bak` before replacing it.

---

## `lint` — check (and fix) wiki integrity

```bash
openkb lint            # report only
openkb lint --fix      # repair broken wikilinks first, then report
```

Checks broken `[[wikilinks]]`, orphaned pages, raw files with no wiki entry,
`index.md` drift, and invalid frontmatter, plus an LLM-driven knowledge pass.
Reports are written to `wiki/reports/lint_<timestamp>.md`. `--fix` fuzzy-matches
broken links or strips them to plain text when there's no match.

---

## `list` & `status`

```bash
openkb status
```

```text
Knowledge base: /Users/you/my-kb

Knowledge Base Status:
  Directory            Files
  -------------------- ----------
  sources              1
  summaries            1
  concepts             3
  entities             9
  reports              0
  raw                  1

  Total indexed: 1 document(s)
  Last compile:  2026-06-25 14:30:22
```

`openkb list` prints the document table (name · type · pages — long PDFs and cloud
imports both show as `pageindex`) followed by the compiled summaries, concepts,
entities, and reports. (The counts above match [`sample-wiki/`](sample-wiki/).)

---

## `watch` — drop-in ingestion

```bash
openkb watch
# in another terminal:  cp new-paper.pdf raw/   → auto-compiles
```

Watches `raw/` and runs `add` on each new supported file until you press Ctrl-C.

---

## `feedback` — report an issue

```bash
openkb feedback "add support for EPUB" --type feature
openkb feedback                      # interactive
```

Opens a **prefilled** GitHub issue (title, body, non-sensitive diagnostics like
OpenKB/Python version and platform) in your browser — you file it with your own
account. `--type` is one of `bug`, `feature`, `question`, `other`. Safe in
non-interactive shells (it won't hang on the type prompt).
