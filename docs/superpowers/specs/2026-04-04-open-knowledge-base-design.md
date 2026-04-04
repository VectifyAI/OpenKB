# OpenKnowledgeBase Design Spec

## Positioning

**One-liner**: Karpathy's LLM Knowledge Base workflow — powered by PageIndex for long document understanding.

Andrej Karpathy described a workflow where LLMs compile raw documents into a structured markdown wiki with summaries, backlinks, and cross-linked concepts. His approach works well for short articles but breaks down for long documents (books, annual reports, legal filings) that exceed LLM context windows.

OpenKnowledgeBase implements this workflow with two key enhancements:
1. **PageIndex** for long document understanding — books and reports that would blow up LLM context are handled via hierarchical tree indexing with summaries.
2. **markitdown** for broad format support — PDF, Word, PowerPoint, Excel, HTML, images, audio, and more are all converted to readable Markdown automatically.

### Comparison with Karpathy's Original Approach

| | Karpathy's Approach | OpenKnowledgeBase |
|---|---|---|
| Short documents | LLM reads directly | markitdown → LLM reads |
| Long documents (books, reports) | Doesn't fit in context | PageIndex tree index |
| Supported formats | Web clipper → .md | PDF, Word, PPT, Excel, HTML, images, audio, .md |
| Document understanding | Raw LLM reading | markitdown + PageIndex structured understanding |
| Wiki compilation | LLM agent | LLM agent (same) |
| Q&A | Query over wiki | Wiki + PageIndex retrieval for precision |
| Open source | No | Yes |

## Architecture

### Directory Structure

```
my-knowledge-base/
├── raw/                            # Original files (user drops here, untouched)
│   ├── paper.pdf
│   ├── notes.md
│   ├── slides.pptx
│   └── textbook.pdf
├── wiki/                           # LLM-maintained knowledge wiki
│   ├── SCHEMA.md                   # Wiki structure specification
│   ├── index.md                    # One-liner summaries of all wiki pages
│   ├── sources/                    # Converted full-text .md
│   │   ├── paper.md                # markitdown converted (short doc)
│   │   ├── notes.md                # Copied directly (.md input)
│   │   ├── slides.md               # markitdown converted
│   │   ├── textbook.md             # PageIndex structured text (long doc)
│   │   └── images/                 # Extracted images from documents
│   │       ├── paper/
│   │       │   ├── img_001.png
│   │       │   └── img_002.jpeg
│   │       └── slides/
│   │           └── img_001.png
│   ├── summaries/                  # Per-document summaries
│   │   ├── paper.md                # LLM-generated summary (short doc)
│   │   ├── notes.md                # LLM-generated summary
│   │   ├── slides.md               # LLM-generated summary
│   │   └── textbook.md             # PageIndex tree index with summaries (long doc)
│   ├── concepts/                   # Cross-document knowledge synthesis
│   │   ├── transformer.md
│   │   ├── attention.md
│   │   └── pretraining.md
│   └── reports/                    # Lint reports
│       └── lint-2026-04-04.md
└── .okb/                           # Internal state
    ├── config.yaml                 # Configuration
    ├── hashes.json                 # SHA-256 hashes of processed files
    └── pageindex.db                # PageIndex storage (long docs only)
```

### Wiki Directory Roles

| Directory | Content | Source |
|---|---|---|
| sources/ | Full-text in readable .md form | markitdown or PageIndex (with text) |
| sources/images/ | Extracted images from documents | markitdown base64 → file extraction |
| summaries/ | Per-document summary | LLM summary or PageIndex tree index |
| concepts/ | Cross-document knowledge synthesis | LLM generates from summaries |
| reports/ | Lint health check reports | Lint agent |

### Two Indexing Paths

**Short documents** (PDF < 50 pages, or any non-PDF format):
```
okb add paper.pdf
  1. Copy paper.pdf → raw/
  2. Check hash — if already in .okb/hashes.json, skip with message
  3. markitdown → markdown with base64 images
  4. Image extraction: base64 data URIs → wiki/sources/images/paper/*.png
     Replace data URIs with relative paths: ![alt](images/paper/img_001.png)
  5. Save processed markdown → wiki/sources/paper.md
  6—8. Single LLM agent session (see Wiki Compilation below)
```

**Markdown input** (`okb add notes.md`):
```
  1. Copy notes.md → raw/
  2. Check hash
  3. Scan for relative image paths (e.g., ![fig](./images/fig1.png))
     Copy referenced images → wiki/sources/images/notes/
     Rewrite paths in the copied markdown
  4. Save → wiki/sources/notes.md
  5—7. Single LLM agent session
```

**Long documents** (PDF >= 50 pages, threshold configurable):
```
okb add textbook.pdf
  1. Copy textbook.pdf → raw/
  2. Check hash — if already indexed, skip (same dedup as short docs)
  3. PageIndex col.add("textbook.pdf") with if_add_node_text=True
     → tree index with summaries + node text
  4. Generate wiki/sources/textbook.md from PageIndex tree:
     Map tree hierarchy to markdown heading levels, include node text
     and page references. (Images TBD — pending PageIndex image
     extraction support; for now, long doc sources are text-only.)

     Example output:
     ```markdown
     ---
     source: textbook.pdf
     type: pageindex
     doc_id: abc123
     ---
     # Chapter 1: Introduction
     (pages 1–30)

     Deep learning is a subset of machine learning...

     ## 1.1 History
     (pages 1–5)

     The field of artificial intelligence...
     ```

  5. Generate wiki/summaries/textbook.md from PageIndex tree:
     Same heading hierarchy but with summaries instead of full text.
     doc_description from PageIndex used as one-liner in index.md.

     Example output:
     ```markdown
     ---
     source: textbook.pdf
     type: pageindex
     doc_id: abc123
     ---
     # Chapter 1: Introduction
     (pages 1–30)
     Summary: Overview of deep learning history, definitions, and scope.

     ## 1.1 History
     (pages 1–5)
     Summary: Traces AI from 1950s symbolic systems to modern deep learning.
     ```

  6. LLM reads summaries/textbook.md (tree summaries, NOT full text)
     → creates/updates concept pages from summaries
     → avoids blowing up LLM context
  7. LLM updates wiki/index.md (using PageIndex doc_description)
```

The 50-page threshold is configurable in `.okb/config.yaml` (`pageindex_threshold`).
Future optimization: also consider token count as a threshold (a 49-page PDF with dense
text may still benefit from PageIndex). For MVP, page count is sufficient.

### Image Handling

**markitdown base64 extraction:**

markitdown outputs images as base64 data URIs. We post-process to extract them:

```python
# Pseudocode
pattern = r'!\[([^\]]*)\]\(data:image/([^;]+);base64,([^)]+)\)'
# For each match:
#   1. Decode base64 → save to wiki/sources/images/{doc_name}/img_NNN.{ext}
#   2. Replace data URI with relative path: ![alt](images/{doc_name}/img_NNN.{ext})
```

**Markdown input with relative images:**

When the input is a .md file with relative image references:
1. Parse all `![alt](path)` references where path is relative
2. Resolve against the source .md file's directory
3. Copy referenced images → `wiki/sources/images/{doc_name}/`
4. Rewrite paths in the copied markdown

Images are stored under `wiki/sources/images/{doc_name}/` so Obsidian renders them natively.

### Wiki Compilation — LLM Agent

**Key design: single agent session per `okb add`.** Summary generation and concept
updates happen in one session so the document content is only sent to the LLM once.
This leverages LLM provider prompt caching — the document content in context is reused
across the summary and concept steps, saving ~50% of tokens compared to split calls.

**Short doc flow (single agent session):**
```
Agent receives in system prompt: SCHEMA.md content
Agent receives in user message: source document content (from sources/{name}.md)

Agent steps (autonomous):
  1. Generate summary → write to summaries/{name}.md
  2. Read index.md → understand existing knowledge structure
  3. Read relevant concept pages (agent decides which)
  4. Create/update concept pages as needed
  5. Update index.md with new document entry
```

**Long doc flow (single agent session):**
```
Agent receives in system prompt: SCHEMA.md content
Agent receives in user message: summary tree (from summaries/{name}.md, already generated)

Agent steps (autonomous):
  1. Summary already generated (step 5 of indexing path) — skip
  2. Read index.md → understand existing knowledge structure
  3. Read relevant concept pages
  4. Create/update concept pages from tree summaries
     (use get_page_content tool if more detail needed from original)
  5. Update index.md with new document entry
```

**Agent tools:**
```
- list_wiki_files(dir)             → List filenames in a wiki/ subdirectory
- read_wiki_file(path)             → Read a .md file from wiki/
- write_wiki_file(path, content)   → Write/update a .md file in wiki/
- get_page_content(doc_id, pages)  → PageIndex page-level retrieval (long docs only)
```

### Q&A

```
okb query "How does multi-head attention differ from self-attention?"
```

**Q&A agent tools:**
```
- list_wiki_files(dir)             → Browse wiki/ directory structure
- read_wiki_file(path)             → Read any .md file from wiki/
- pageindex_retrieve(doc_id, question) → PageIndex retrieval for long docs
```

**Q&A agent flow:**

1. Read `wiki/index.md` to get an overview of all documents and concepts.
   index.md includes metadata per document:
   ```
   ## Documents
   - [[summaries/paper]] — Proposes Transformer architecture (short | [[sources/paper]])
   - [[summaries/textbook]] — Deep learning textbook (pageindex | doc_id: abc123)

   ## Concepts
   - [[concepts/attention]] — Evolution of attention mechanisms across 5 papers
   ```

2. The agent locates relevant summaries/ and concepts/ pages based on the question.

3. The agent reads relevant pages to compose an answer.

4. When more detail is needed:
   - **Short documents** (marked `short` in index.md): read `wiki/sources/{name}.md` directly.
   - **Long documents** (marked `pageindex` in index.md): call `pageindex_retrieve(doc_id, question)`.

5. The agent synthesizes the answer from all gathered information.

**`pageindex_retrieve(doc_id, question)` implementation:**
This is a wrapper function (not a nested agent). It:
  1. Calls `get_document_structure(doc_id)` to get the tree index
  2. Sends the tree structure + question to LLM in a single call:
     "Given this document structure, which sections are relevant to: {question}?"
  3. LLM returns relevant node_ids / page ranges
  4. Calls `get_page_content(doc_id, pages)` for those pages
  5. Returns the retrieved text to the Q&A agent

This avoids agent-in-agent complexity while still leveraging PageIndex's tree search.

**Implementation:** Built with OpenAI Agents SDK (supports non-OpenAI models via LiteLLM).
The agent is given SCHEMA.md in system prompt and function tools for wiki access and
PageIndex retrieval.

### Watch Mode

```
okb watch

Monitors raw/ directory using filesystem watcher (watchdog).
  → New file detected → triggers okb add flow
  → Logs to terminal:
    [12:03] Detected new-paper.pdf
    [12:03] Converting with markitdown...
    [12:03] Extracting images...
    [12:03] Compiling to wiki...
    [12:04] Created wiki/summaries/new-paper.md
    [12:04] Updated wiki/concepts/attention.md (added references)
    [12:04] Updated wiki/index.md
    [12:04] Done
```

**Robustness:**
- **Debounce:** Wait 2 seconds after last file system event before triggering, to handle
  partial writes and batch file drops.
- **Batch processing:** Multiple files detected in one debounce window are processed
  sequentially (one `okb add` at a time to avoid wiki write conflicts).
- **Graceful shutdown:** Ctrl+C finishes current file processing before exiting.
- **Error isolation:** If one file fails, log the error and continue with the next file.

Incremental by design:
- The LLM agent decides which pages to update based on existing wiki state
- PageIndex SHA-256 dedup prevents re-indexing unchanged files
- `.okb/hashes.json` tracks processed file hashes to skip unmodified files

### Linting / Health Check

```
okb lint
```

**Two categories of checks:**

**Structural checks (no LLM needed, code-based):**
1. Broken links — scan all .md for [[wikilinks]], verify targets exist
2. Orphans — pages with no incoming or outgoing links
3. Missing entries — file in raw/ but no corresponding sources/ or summaries/ entry
4. Index sync — index.md entries match actual files in sources/, summaries/, concepts/

**Knowledge checks (LLM-based):**
5. Contradictions — conflicting information across related pages
6. Gaps — concepts referenced multiple times but lacking a dedicated page
7. Staleness — newer documents exist but wiki doesn't reflect updates
8. Quality — concept pages insufficiently synthesizing related documents

**Large wiki handling:** Structural checks scan all files directly (no context limit issue). Knowledge checks are batched per concept — each check loads one concept page + its referenced summaries, keeping context manageable.

**Output:**

```markdown
# Lint Report — 2026-04-04

## Structural Issues
- ❌ Broken link: concepts/attention.md → [[concepts/softmax]] (not found)
- ⚠️ Orphan: summaries/old-paper.md (no links to/from any page)
- ⚠️ Missing entries: raw/new-paper.pdf has no sources/ or summaries/ entry

## Knowledge Issues
- ⚠️ Contradiction: concepts/scaling.md says "linear scaling" but summaries/paper3.md says "sub-linear"
- 💡 Gap: "beam search" mentioned in 4 summaries but no concept page exists
- 💡 Stale: concepts/transformer.md doesn't reference new document paper5.pdf
```

Output → `wiki/reports/lint-YYYY-MM-DD.md`

**`okb lint --fix`:**
- Structural issues: auto-fix (remove broken links, create missing summaries, sync index)
- Knowledge gaps/staleness: auto-fix (create missing concept pages, update stale pages)
- Contradictions: report only — user decides which version is correct

### Error Handling

**Strategy: skip + log + continue.** No single file failure should abort the entire operation.

| Operation | On failure | Action |
|---|---|---|
| markitdown conversion | File format unsupported or corrupt | Log warning, skip file |
| Image extraction | Malformed base64 or write error | Log warning, leave data URI in place |
| PageIndex indexing | LLM call fails | Retry once, then skip file with error |
| Wiki compilation (LLM) | LLM call fails or times out | Retry once, then skip compilation (sources/ still written) |
| Q&A query | LLM call fails | Return error message to user |
| File copy to raw/ | Permission or disk error | Abort with clear error message |

LLM call retries: 1 retry with exponential backoff (2s). If still failing, skip and log.

## CLI Commands

```
okb init                        # Initialize knowledge base (create dirs + SCHEMA.md)
okb add <file_or_dir>          # Add document(s) and compile to wiki
okb query "question"            # Q&A (wiki-first, sources/PageIndex for detail)
okb watch                       # Watch raw/ and auto-compile
okb lint                        # Health check (report only)
okb lint --fix                  # Health check + auto-fix
okb list                        # List indexed documents
okb status                      # KB status (doc count, wiki pages, last compile time)
```

### `okb add <file_or_dir>`

When a directory is passed, recursively scan for supported files and add each one.
Duplicate detection: hash each file (SHA-256) and store in `.okb/hashes.json`. If a file
with the same hash has already been indexed, skip it with a message.

### `okb list`

```
$ okb list
Documents (4):
  paper.pdf              short     12 pages    2026-04-01
  notes.md               short     —           2026-04-02
  slides.pptx            short     15 slides   2026-04-02
  textbook.pdf           pageindex 520 pages   2026-04-03

Concepts (3):
  attention.md           sources: paper.pdf, textbook.pdf
  transformer.md         sources: paper.pdf, textbook.pdf, notes.md
  pretraining.md         sources: textbook.pdf
```

### `okb status`

```
$ okb status
Knowledge Base: my-knowledge-base/
  Documents:    4 (3 short, 1 pageindex)
  Sources:      4 files
  Summaries:    4 files
  Concepts:     3 files
  Wiki pages:   12 total
  Last compile: 2026-04-03 14:30
  Last lint:    2026-04-03 15:00 (2 issues found)
```

## Configuration

Generated interactively by `okb init`, stored in `.okb/config.yaml`:

```yaml
model: gpt-4o                   # LLM model (all providers via LiteLLM)
api_key_env: OPENAI_API_KEY     # Environment variable name for API key
language: en                     # Wiki output language (default: en)
pageindex_threshold: 50          # PDF pages threshold for PageIndex (default: 50)
```

## SCHEMA.md

Auto-generated by `okb init`, user-customizable. This is the LLM agent's instruction
for how to organize the wiki. Delivered to the agent via system prompt (not read from
file at runtime) to ensure it is always available.

```markdown
# Wiki Schema

## Directory Structure
- sources/ — Full-text converted from raw documents. Do not modify directly.
- sources/images/ — Extracted images from documents, referenced by sources.
- summaries/ — One per source document. Summary of key content.
- concepts/ — Cross-document topic synthesis. Created when a theme spans multiple documents.
- reports/ — Lint health check reports. Auto-generated.

## Page Types
- **Summary Page** (summaries/): Key content of a single source document.
- **Concept Page** (concepts/): Cross-document topic synthesis with [[wikilinks]].
- **Index Page** (index.md): One-liner summary of every page in the wiki. Auto-maintained.

## Index Page Format
index.md lists all documents and concepts with metadata:
- Documents: name, one-liner description, type (short|pageindex), detail access path
- Concepts: name, one-liner description

## Format
- Use [[wikilink]] to link other wiki pages (e.g., [[concepts/attention]])
- Summary pages header: `sources: [paper.pdf]`
- Concept pages header: `sources: [paper1.pdf, paper2.pdf, ...]`
- Standard Markdown heading hierarchy
- Keep each page focused on a single topic
```

## Tech Stack

```
pageindex          # Long document indexing + retrieval (git dep: feat/sdk branch)
markitdown         # Universal file-to-markdown conversion
click              # CLI framework
watchdog           # Filesystem watching
litellm            # LLM calls (already a PageIndex dependency)
openai-agents      # Agent framework (supports non-OpenAI models via LiteLLM)
pyyaml             # Config files
```

### PageIndex Configuration

PageIndex uses the default collection (no collection name needed).
When indexing long documents, PageIndex is configured with:
- `if_add_node_text=True` — include original page text in tree structure nodes
- `if_add_node_summary=True` — include LLM-generated summaries per node
- `if_add_doc_description=True` — generate document-level description

The `doc_description` is used as the one-liner in `index.md`.
The tree with summaries (without text) is stored in `summaries/`.
The tree with text (structured as readable markdown) is stored in `sources/`.

### PageIndex Dependency

During development:
```toml
dependencies = [
    "pageindex @ git+https://github.com/KylinMountain/PageIndex.git@feat/sdk",
    "markitdown",
]
```

Once PageIndex SDK is published to PyPI, switch to `pageindex>=x.x.x`.

### Distribution

Distributed as a pip package:
```bash
pip install open-knowledge-base
```

CLI entry point `okb` registered via pyproject.toml `[project.scripts]`.

Requires Python >= 3.10.

## Target Users

Developers and researchers who want to manage personal knowledge bases of papers, technical documents, books, and notes — with LLM-automated organization and retrieval.

## MVP Scope

The MVP delivers the full CLI workflow:
1. `okb init` — scaffold a knowledge base
2. `okb add` — convert + index + compile to wiki (short and long documents, with image extraction)
3. `okb query` — Q&A over wiki + original documents via PageIndex
4. `okb watch` — auto-compile on file changes
5. `okb lint` — health check (structural + knowledge) with optional auto-fix
6. `okb list` / `okb status` — basic management

Out of scope for MVP:
- Obsidian plugin
- Team/multi-user support
- Slide/chart output
- Web UI
- PageIndex image extraction (pending upstream support; long doc images are text-only for now)
