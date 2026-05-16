# OpenKB Wiki Schema

This document describes the full directory layout and conventions of
an OpenKB-compiled wiki. Read this when you need details beyond what
`SKILL.md` covers.

## Directory layout

```
<kb-root>/
├── raw/                       Original files the user ingested
│   ├── paper.pdf
│   └── notes.md
├── wiki/                      The compiled knowledge artifact
│   ├── AGENTS.md              Compile-time schema (for write side)
│   ├── index.md               Top-level table of contents
│   ├── log.md                 Chronological ingest/edit log
│   ├── summaries/             One file per ingested document
│   │   ├── paper.md
│   │   └── notes.md
│   ├── concepts/              Cross-document synthesis pages
│   │   ├── attention.md
│   │   └── transformer.md
│   ├── sources/               Converted source content
│   │   ├── paper.json         Long-doc paginated content
│   │   ├── notes.md           Short-doc full text
│   │   └── images/            Extracted images (per-doc subdirs)
│   │       └── paper/
│   │           ├── p1_img1.png
│   │           └── ...
│   ├── explorations/          Saved `openkb query --save` answers
│   └── reports/               Auto-generated lint reports
└── .openkb/
    ├── config.yaml            Model, language, pageindex_threshold
    ├── hashes.json            Hash registry (with doc_name, doc_id)
    └── pageindex.db           SQLite store for long PDFs (optional)
```

## File conventions

### `wiki/index.md`

Plain Markdown with three top-level sections:

```markdown
# Knowledge Base Index

## Documents
- [[summaries/paper]] (pageindex) — Brief from the summary frontmatter.
- [[summaries/notes]] (short) — ...

## Concepts
- [[concepts/attention]] — Brief from the concept frontmatter.
- [[concepts/transformer]] — ...

## Explorations
- [[explorations/some-saved-query]] — User's saved query answer.
```

The type tag in parentheses is always either `(short)` or
`(pageindex)` — never the file extension. Short = anything the
markitdown path can convert (md, docx, html, txt, short PDFs);
pageindex = a long PDF indexed by PageIndex.

Section headings are kept even when empty (e.g. after removing all
documents the `## Documents` heading stays). Entry order is roughly
insertion order, not alphabetical.

### `wiki/summaries/<doc_name>.md`

Per-document summary. Frontmatter:

```yaml
---
sources: [raw/paper.pdf]        # The original ingested file
brief: One-line description.
doc_type: short                  # short | pageindex
full_text: sources/paper.md      # short docs: .md ; long PDFs: .json
---
```

`full_text` always points at the converted source file: short docs
get `sources/<name>.md` (markitdown output); long PDFs get
`sources/<name>.json` (per-page content array — see the long-doc
section below for how to read it).

Body is the LLM-synthesized summary plus a `## Related Concepts`
section linking to the concepts this doc touches.

### `wiki/concepts/<slug>.md`

Cross-document synthesis. Frontmatter:

```yaml
---
sources: [summaries/paper.md, summaries/notes.md]
brief: One-line summary.
---
```

Body has free-form sections plus `## Related Documents` listing the
contributing summaries. Multi-source = cross-document synthesis (the
high-value output of OpenKB's compile pipeline).

### `wiki/sources/<doc_name>.md` (short docs)

Plain Markdown — the markitdown-converted full text of the original
document. Images appear as `![](sources/images/<doc_name>/p1_img1.png)`
relative paths.

### `wiki/sources/<doc_name>.json` (long PDFs)

JSON array, one entry per page:

```json
[
  {"page": 1, "content": "Page text...", "images": ["sources/images/.../p1_img1.png"]},
  {"page": 2, "content": "..."}
]
```

Pages are 0-indexed in the array but their `page` field is 1-indexed
(matching PDF page numbers). To fetch page 14:

```bash
jq '.[13]' wiki/sources/paper.json        # page array index 13 = page 14
jq '.[] | select(.page == 14)' wiki/sources/paper.json   # by page number
```

The file can be large (100+ MB for very long docs). Always slice with
`jq`; never `Read` the whole file unless you need the full content.

### `wiki/log.md`

Append-only audit log. Each operation records timestamp + action +
filename:

```markdown
## [2026-05-16 12:14:12] ingest | paper.pdf
## [2026-05-16 15:30:01] remove | old-notes.md
```

### `.openkb/hashes.json`

Hash registry — SHA-256 file hash → metadata. Each entry has at least:

```json
{
  "<sha256>": {
    "name": "paper.pdf",          // original filename
    "doc_name": "paper",           // slug used everywhere in wiki/
    "type": "long_pdf",            // or "md", "docx", etc.
    "doc_id": "pi-doc-xyz..."      // long_pdf only — PageIndex doc_id
  }
}
```

Use `openkb list` for a formatted view rather than parsing this file
directly.

## Wikilinks

Concept and summary bodies use Obsidian-compatible `[[wikilink]]`
syntax. Three forms:

- `[[concepts/attention]]` → relative path `wiki/concepts/attention.md`
- `[[summaries/paper]]` → `wiki/summaries/paper.md`
- `[[concepts/attention|self-attention]]` → display alias "self-attention"
  but target is `wiki/concepts/attention.md`

`openkb lint --fix` strips broken wikilinks (targets that no longer
exist), so links in the wiki should always resolve. If you encounter
a broken one, the user has hand-edited or the wiki is mid-update.

## Short vs long documents

OpenKB classifies each ingested document at add time:

| | Short | Long (PageIndex) |
|---|---|---|
| Trigger | PDF < 20 pages, or any non-PDF | PDF ≥ 20 pages |
| Stored at | `wiki/sources/<doc>.md` | `wiki/sources/<doc>.json` + `.openkb/pageindex.db` |
| Frontmatter `doc_type` | `short` | `pageindex` |
| Registry `type` | extension (md, docx, …) | `long_pdf` |
| How to read | `Read` the `.md` | `jq` the `.json` |

The threshold is configurable in `.openkb/config.yaml`
(`pageindex_threshold: 20`).
