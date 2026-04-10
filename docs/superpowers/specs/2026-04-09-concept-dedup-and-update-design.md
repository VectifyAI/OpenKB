# Concept Dedup & Existing Page Update

**Date:** 2026-04-09
**Status:** Approved
**Branch:** bugfix/compile

## Problem

The compiler pipeline generates concept pages per document, but:

1. **No dedup** — LLM only sees concept slug names, not content. It can't reliably judge whether a new concept overlaps with an existing one. As the KB grows, concepts duplicate and diverge.
2. **No update of existing pages** — When a new document has information relevant to existing concepts, those pages are not updated. Knowledge doesn't compound across documents.

The old agent-based approach solved this (the agent could read/write wiki files freely), but was too slow — 20-30 tool-call round-trips per document.

## Design

Extend the existing deterministic pipeline to give the LLM enough context for dedup/update decisions, without adding agent loops or breaking prompt caching.

### Prompt Caching Invariant

The cached prefix `[system_msg, doc_msg]` must remain identical across all LLM calls within a single document compilation. All new context (concept briefs, existing page content) goes into messages **after** the cached prefix.

### Pipeline Overview

```
Step 1: [system, doc] → summary                          (unchanged)
Step 2: [system, doc, summary, concepts_plan_prompt] → concepts plan JSON
Step 3a: [system, doc, summary, create_prompt] × N  → new concept pages     (concurrent)
Step 3b: [system, doc, summary, update_prompt] × M  → rewritten concept pages (concurrent)
Step 3c: code-only × K                              → add cross-ref links to related concepts
Step 4: update index                                 (unchanged)
```

Steps 3a and 3b share a single semaphore and run concurrently together.

### Part 1: Concept Briefs

New function `_read_concept_briefs(wiki_dir)` reads existing concept pages and returns a compact summary string:

```
- attention: Attention is a mechanism that allows models to focus on relevant parts...
- transformer-architecture: The Transformer is a neural network architecture...
```

For each concept file in `wiki/concepts/*.md`:
- Skip YAML frontmatter
- Take first 150 characters of body text
- Format as `- {slug}: {brief}`

This replaces the current `", ".join(existing_concepts)` in the concepts-list prompt. Pure file I/O, no LLM call.

### Part 2: Concepts Plan Prompt

The `_CONCEPTS_LIST_USER` template is replaced with a new `_CONCEPTS_PLAN_USER` template that asks the LLM to return a JSON object with three action types:

```json
{
  "create": [{"name": "flash-attention", "title": "Flash Attention"}],
  "update": [{"name": "attention", "title": "Attention Mechanism"}],
  "related": ["transformer-architecture"]
}
```

- **create** — New concept not covered by any existing page.
- **update** — Existing concept with significant new information worth integrating.
- **related** — Existing concept tangentially related; only needs a cross-reference link.

The prompt includes rules:
- Don't create concepts that overlap with existing ones — use "update" instead.
- Don't create concepts that are just the document topic itself.
- For first few documents, create 2-3 foundational concepts at most.
- "related" is for lightweight cross-linking only.

### Part 3: Three Execution Paths

#### create (unchanged)

Same as current: concurrent `_llm_call_async` with `_CONCEPT_PAGE_USER` template. Written via `_write_concept` with `is_update=False`.

#### update (new)

New template `_CONCEPT_UPDATE_USER`:

```
Update the concept page for: {title}

Current content of this page:
{existing_content}

New information from document "{doc_name}" (summarized above) should be
integrated into this page. Rewrite the full page incorporating the new
information naturally. Maintain existing cross-references and add new ones
where appropriate.

Return ONLY the Markdown content (no frontmatter, no code fences).
```

Call structure: `[system_msg, doc_msg, {assistant: summary}, update_user_msg]`

The cached prefix `[system_msg, doc_msg]` is shared with create calls. The `existing_content` (typically 200-500 tokens) is in the final user message only.

Written via `_write_concept` with `is_update=True`. The frontmatter `sources:` list is updated to include the new source file.

#### related (code-only, no LLM)

For each related slug:
1. Read the concept file
2. If `summaries/{doc_name}` is not already linked, append `\n\nSee also: [[summaries/{doc_name}]]`
3. Update frontmatter `sources:` list

Pure file I/O, millisecond-level.

### Part 4: Shared Logic Between Short and Long Doc

Current `compile_short_doc` and `compile_long_doc` duplicate Steps 2-4. Extract shared logic into `_compile_concepts(wiki_dir, model, system_msg, doc_msg, summary, doc_name, kb_dir, max_concurrency)`.

Public functions become:
- `compile_short_doc`: builds context A from source text → calls `_compile_concepts`
- `compile_long_doc`: builds context A from PageIndex summary → calls `_compile_concepts`

### Part 5: JSON Parsing Fallback

If the LLM returns a flat JSON array instead of the expected dict, treat it as all "create" actions:

```python
if isinstance(parsed, list):
    create_list, update_list, related_list = parsed, [], []
else:
    create_list = parsed.get("create", [])
    update_list = parsed.get("update", [])
    related_list = parsed.get("related", [])
```

This ensures backward compatibility if the LLM doesn't follow the new format.

## Token Cost Analysis

Compared to current pipeline (per document with C existing concepts):

| Step | Current | New | Delta |
|------|---------|-----|-------|
| concepts-list prompt | ~50 tokens (slug names) | ~50 + C×30 tokens (briefs) | +C×30 |
| update calls | 0 | M × ~500 tokens (existing content) | +M×500 |
| related | 0 | 0 (code-only) | 0 |

At C=30 existing concepts: +900 tokens in concepts-list prompt.
At M=2 update calls: +1000 tokens total.

Total overhead: ~2000 tokens per document. Negligible compared to document content (5K-20K tokens).

## Files Changed

- `openkb/agent/compiler.py` — all changes
  - New: `_read_concept_briefs()`, `_CONCEPTS_PLAN_USER`, `_CONCEPT_UPDATE_USER`, `_add_related_link()`, `_compile_concepts()`
  - Modified: `compile_short_doc()`, `compile_long_doc()`, `_parse_json()` caller logic
- `tests/test_compiler.py` — update tests for new JSON format and update/related paths

## Not In Scope

- Concept briefs truncation/filtering for very large KBs (100+ concepts) — revisit when needed
- Interactive ingest (human-in-the-loop checkpoint) — separate feature
- Lint --fix auto-repair — separate feature
