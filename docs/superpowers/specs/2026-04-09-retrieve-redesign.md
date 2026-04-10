# Retrieve Redesign: Unified Query, Brief Summaries, and Local Page Content

**Date:** 2026-04-09
**Status:** Approved
**Branch:** bugfix/compile

## Problems

### 1. Long vs Short Doc Split in Query

The query agent treats long documents (PageIndex-indexed) and short documents differently:

- **Short docs**: agent reads `wiki/sources/{name}.md` via `read_file`
- **Long docs**: agent calls `pageindex_retrieve(doc_id, question)` — a black-box RAG call

**Design Principle**: PageIndex is an indexer, not a retriever. Query-time retrieval should be done by the agent navigating the wiki, using the same tools for all documents.

### 2. index.md Has No Brief Summaries

Karpathy's gist says index.md should have "each page listed with a link, **a one-line summary**". Currently it only has wikilinks with no descriptions. The query agent must open every file to understand what's available.

### 3. No Brief Summaries on Concepts Either

Same problem: concept entries in index.md have no description. The agent can't judge relevance from the index alone.

## Design

### Part 1: Structured LLM Output with Brief Summaries

All LLM generation steps (summary, concept create, concept update) now return a JSON object with both a one-line brief and the full content.

#### Summary Generation

`_SUMMARY_USER` prompt changes to request JSON output:

```
Write a summary page for this document in Markdown.

Return a JSON object with two keys:
- "brief": A single sentence (under 100 chars) describing the document's main contribution
- "content": The full summary in Markdown. Include key concepts, findings, and [[wikilinks]]

Return ONLY valid JSON, no fences.
```

LLM returns:
```json
{
  "brief": "Introduces the Transformer architecture based entirely on self-attention",
  "content": "# Attention Is All You Need\n\nThis paper proposes..."
}
```

The `brief` is:
- Written into summary frontmatter: `brief: Introduces the Transformer...`
- Passed to `_update_index` for the Documents section

The `content` is written to `wiki/summaries/{name}.md` as before.

#### Concept Generation (create)

`_CONCEPT_PAGE_USER` prompt changes similarly:

```
Write the concept page for: {title}

Return a JSON object with two keys:
- "brief": A single sentence (under 100 chars) defining this concept
- "content": The full concept page in Markdown with [[wikilinks]]

Return ONLY valid JSON, no fences.
```

The `brief` is:
- Written into concept frontmatter: `brief: Mechanism allowing each position to attend to all others`
- Passed to `_update_index` for the Concepts section
- Used by `_read_concept_briefs` (read from frontmatter instead of truncating body text)

#### Concept Generation (update)

`_CONCEPT_UPDATE_USER` also returns `{"brief": "...", "content": "..."}`. The brief may change as the concept evolves with new information.

#### Long Doc Summary (overview)

Long documents do NOT need the LLM to generate a brief. The brief comes directly from PageIndex's `doc_description` field (available via `IndexResult.description`), which is already a document-level summary generated during indexing. `_LONG_DOC_SUMMARY_USER` stays unchanged (returns plain markdown overview, not JSON) — the brief is passed through from the indexer.

In `compile_long_doc`, the `doc_description` is passed to `_compile_concepts` which forwards it to `_update_index` as the doc brief.

#### Parsing

All LLM responses go through `_parse_json`. Callers extract `brief` and `content`:

```python
parsed = _parse_json(raw)
brief = parsed.get("brief", "")
content = parsed.get("content", raw)  # fallback: treat raw as content if not JSON
```

The fallback ensures backward compatibility if the LLM returns plain text instead of JSON.

### Part 2: index.md with Brief Summaries

`_update_index` signature changes:

```python
def _update_index(wiki_dir, doc_name, concept_names, doc_brief="", concept_briefs=None):
```

Output format:

```markdown
## Documents
- [[summaries/attention-is-all-you-need]] — Introduces the Transformer architecture based on self-attention
- [[summaries/flash-attention]] — Efficient attention algorithm reducing memory from quadratic to linear

## Concepts
- [[concepts/self-attention]] — Mechanism allowing each position to attend to all others in a sequence
- [[concepts/transformer]] — Neural network architecture based entirely on attention mechanisms
```

When updating an existing entry (re-compile), the brief is updated in place.

### Part 3: Frontmatter with Brief

Summary and concept pages get a `brief` field in frontmatter:

```markdown
---
sources: [paper.pdf]
brief: Introduces the Transformer architecture based on self-attention
---

# Attention Is All You Need
...
```

`_read_concept_briefs` is updated to read from `brief:` frontmatter field instead of truncating body text. Fallback to body truncation if `brief:` is absent (backward compat with existing pages).

### Part 4: Long Doc Sources from Markdown to JSON

Store per-page content as JSON instead of a giant markdown file.

**Current**:
```
wiki/sources/paper.md          ← rendered markdown, 10K-50K tokens
```

**New**:
```
wiki/sources/paper.json        ← per-page JSON array
```

**JSON format** (only the `pages` array from PageIndex, not the full doc object):
```json
[
    {
        "page": 1,
        "content": "Full text of page 1...",
        "images": [{"path": "images/paper/p1_img1.png", "width": 400, "height": 300}]
    },
    {
        "page": 2,
        "content": "Full text of page 2..."
    }
]
```

`images` field is optional. Image paths are relative to `wiki/sources/`. Short documents are not affected — they stay as `.md`.

#### Indexer Changes

In `indexer.py`, replace `render_source_md` + `_relocate_images` with:
1. `col.get_page_content(doc_id, "1-9999")` to get all pages
2. Relocate image paths in each page's `images` array
3. Write as JSON to `wiki/sources/{name}.json`

### Part 5: New Tool `get_page_content`

Add to `openkb/agent/tools.py`:

```python
def get_page_content(doc_name: str, pages: str, wiki_root: str) -> str:
    """Get text content of specific pages from a long document.

    Args:
        doc_name: Document name (e.g. 'attention-is-all-you-need').
        pages: Page specification (e.g. '3-5,7,10-12').
        wiki_root: Absolute path to the wiki root directory.
    """
```

Implementation:
1. Read `wiki/sources/{doc_name}.json`
2. Parse `pages` spec into a set of page numbers (comma-separated, ranges with `-`)
3. Filter pages, format as `[Page N]\n{content}\n\n`
4. Return concatenated text, or error if file not found

### Part 6: Query Agent Changes

**Remove**: `pageindex_retrieve` tool and `_pageindex_retrieve_impl` entirely.

**Add**: `get_page_content` tool.

**Update instructions**:
```
## Search strategy
1. Read index.md to understand what documents and concepts are available.
   Each entry has a brief summary to help you judge relevance.
2. Read relevant summary pages (summaries/) for document overviews.
3. Read concept pages (concepts/) for cross-document synthesis.
4. For long documents, use get_page_content(doc_name, pages) to read
   specific pages. The summary page shows chapter structure with page
   ranges to help you decide which pages to read.
5. Synthesise a clear, well-cited answer.
```

**Remove**: `openkb_dir` and `model` parameters from `build_query_agent`.

### What Gets Removed

- `_pageindex_retrieve_impl` (~110 lines)
- `pageindex_retrieve` tool
- `render_source_md` from `tree_renderer.py`
- `_relocate_images` in current form (replaced by per-page relocation)
- PageIndex imports in `query.py`

### What Stays

- `render_summary_md` — summaries still markdown
- Short doc pipeline — unchanged
- Image files in `wiki/sources/images/`
- PageIndex in `indexer.py` — still used for tree building

## Compile Pipeline Changes Summary

The compile pipeline (`_compile_concepts`, `compile_short_doc`, `compile_long_doc`) changes:

1. **Summary step**: parse JSON response, extract `brief` + `content`
2. **Concept create/update steps**: parse JSON response, extract `brief` + `content`
3. **`_write_summary`**: add `brief` to frontmatter
4. **`_write_concept`**: add/update `brief` in frontmatter
5. **`_update_index`**: write `— {brief}` after each wikilink
6. **`_read_concept_briefs`**: read from `brief:` frontmatter field (fallback to body truncation)

## Files Changed

- `openkb/agent/compiler.py` — prompt templates return JSON with brief+content, parse responses, pass briefs to index/frontmatter
- `openkb/indexer.py` — sources output from md to json, image relocation per-page
- `openkb/agent/tools.py` — add `get_page_content`
- `openkb/agent/query.py` — remove `pageindex_retrieve`, add `get_page_content`, update instructions
- `openkb/tree_renderer.py` — remove `render_source_md`
- `openkb/schema.py` — update AGENTS_MD
- `tests/test_compiler.py` — update for JSON LLM responses
- `tests/test_indexer.py` — update for JSON output
- `tests/test_query.py` — update for new tool set
- `tests/test_agent_tools.py` — add tests for `get_page_content`

## Not In Scope

- Cloud PageIndex query support (removed entirely)
- Changes to the lint pipeline
- Interactive ingest
