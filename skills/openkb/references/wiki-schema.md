# OpenKB Wiki Schema

The layout and conventions of the `wiki/` tree. Load this when you
need details beyond what `SKILL.md` covers — frontmatter fields,
long-PDF JSON shape, wikilink resolution rules.

## Directory layout

```
<kb-root>/
├── raw/                     Original ingested files (don't modify)
└── wiki/                    The compiled knowledge artifact
    ├── index.md             Top-level table of contents (start here)
    ├── log.md               Chronological ingest/edit log
    ├── summaries/<doc>.md   One per ingested document
    ├── concepts/<slug>.md   Cross-document synthesis pages
    ├── entities/<slug>.md   Named-thing pages (people/orgs/places/...)
    ├── sources/             Converted source content
    │   ├── <doc>.md         Short-doc full text
    │   ├── <doc>.json       Long-doc paginated content
    │   └── images/<doc>/    Extracted images, per-doc
    ├── explorations/        Saved `openkb query --save` answers
    └── reports/             Auto-generated lint reports
```

Internal openkb state lives at `<kb-root>/.openkb/` (config, hash
registry, PageIndex DB). **Do not read these directly** — use
`openkb status` / `openkb list` for anything you'd want from them.

## `wiki/index.md`

Four top-level sections, each entry has a one-line brief:

```markdown
## Documents
- [[summaries/paper]] (pageindex) — brief from frontmatter
- [[summaries/notes]] (short) — ...

## Concepts
- [[concepts/attention]] — brief from frontmatter

## Entities
- [[entities/ada-lovelace]] (person) — brief from frontmatter

## Explorations
- [[explorations/some-saved-query]] — saved query answer
```

The type tag is always `(short)` or `(pageindex)` — never the file
extension. Section headings persist when empty (entry order is
insertion order, not alphabetical).

## `wiki/summaries/<doc>.md`

Frontmatter:

```yaml
---
sources: [raw/paper.pdf]
brief: One-line description.
doc_type: short                # short | pageindex
full_text: sources/paper.md    # short docs: .md ; long PDFs: .json
---
```

Body: LLM-synthesized summary + a `## Related Concepts` section.

## `wiki/concepts/<slug>.md`

Frontmatter:

```yaml
---
sources: [summaries/paper.md, summaries/notes.md]
brief: One-line summary.
---
```

Body: free-form sections + `## Related Documents` listing
contributing summaries. **Multi-source = cross-document synthesis**
— this is the high-value output of OpenKB's compile pipeline.

## `wiki/entities/<slug>.md`

Frontmatter:

```yaml
---
sources: [summaries/paper.md, summaries/notes.md]
brief: One-line description.
type: person                   # person | organization | place | product | work | event | other
---
```

Body: free-form sections about the named thing + a `## Related
Documents` section. One page per entity, accumulated as more
documents mention it. For "who/what is X" questions about a named
thing, read the matching entity page first.

## `wiki/sources/<doc>.md` (short docs)

The markitdown-converted full text. Image refs appear as
`![](sources/images/<doc>/p1_img1.png)`.

## `wiki/sources/<doc>.json` (long PDFs)

Array of `{"page": <1-indexed>, "content": "...", "images": [...]}`
entries. To fetch a page, slice the array (page N → index N-1):

```bash
jq '.[13]' wiki/sources/paper.json   # page 14
```

The file may be very large (100+ MB). Always slice; never read
whole.

## Wikilinks

Obsidian-compatible `[[wikilink]]` syntax. Forms:

- `[[concepts/attention]]` → `wiki/concepts/attention.md`
- `[[summaries/paper]]` → `wiki/summaries/paper.md`
- `[[concepts/attention|alias]]` → display "alias", target is
  `wiki/concepts/attention.md`

`openkb lint --fix` strips broken wikilinks, so links in the wiki
should always resolve. A broken one means hand-edit or
mid-update — not a bug to chase.

## Short vs long classification

| | Short | Long (PageIndex) |
|---|---|---|
| Trigger | PDF < 20 pages, or any non-PDF | PDF ≥ 20 pages |
| Source file | `wiki/sources/<doc>.md` | `wiki/sources/<doc>.json` |
| Frontmatter `doc_type` | `short` | `pageindex` |
| How to read | read the `.md` | `jq` the `.json` |

The threshold is configurable but the agent shouldn't need to know
it — use `openkb list`'s Type column to tell which one a doc is.
