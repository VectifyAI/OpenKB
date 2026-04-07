<div align="center">

<a href="https://openkb.ai">
  <img src="https://docs.pageindex.ai/images/openkb.png" alt="OpenKB (by PageIndex)" />
</a>

# OpenKB: Open LLM Knowledge Base

<p align="center"><i>Scale to long documents&nbsp; • &nbsp;Reasoning-based retrieval&nbsp; • &nbsp;Native multi-modality&nbsp; • &nbsp;No Vector DB</i></p>

</div>

---

# 📑 Introduction to OpenKB

Andrej Karpathy [described](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) a workflow where LLMs compile raw documents into a structured, interlinked markdown wiki; summaries, concept pages, cross-references, all maintained automatically. Knowledge compounds over time instead of being re-derived on every query.

**OpenKB (Open Knowledge Base)** is an open-source CLI that implements this workflow, powered by [**PageIndex**](https://github.com/VectifyAI/PageIndex) for vectorless long document retrieval.

### Why not just traditional RAG?

Traditional RAG rediscovers knowledge from scratch on every query. Nothing accumulates. OpenKB compiles knowledge once into a persistent wiki, then keeps it current. Cross-references already exist. Contradictions are flagged. Synthesis reflects everything consumed.

### Features

- **Any format** — PDF, Word, PowerPoint, Excel, HTML, Markdown, text, CSV, and more via markitdown
- **Scale to long documents** — Long and complex documents are handled via [PageIndex](https://github.com/VectifyAI/PageIndex) tree indexing, enabling better long-context retrieval
- **Native multi-modality** — Retrieves and understands figures, tables, and images, not just text
- **Auto wiki** — LLM generates summaries, concept pages, and cross-links. You curate sources; the LLM does the rest
- **Query** — Ask questions against your wiki. The LLM navigates your compiled knowledge to answer
- **Lint** — Health checks find contradictions, gaps, orphans, and stale content
- **Watch mode** — Drop files into `raw/`, wiki updates automatically
- **Obsidian compatible** — Wiki is plain `.md` files with `[[wikilinks]]`. Open in Obsidian for graph view and browsing

# 🚀 Getting Started

### Install

```bash
pip install openkb
```

### Quick start

```bash
# 1. Create a knowledge base
mkdir my-kb && cd my-kb
okb init

# 2. Add documents
okb add paper.pdf
okb add ~/papers/                   # Add a whole directory
okb add article.html

# 3. Ask questions
okb query "What are the main findings?"

# 4. Check wiki health
okb lint
```

### Set up your LLM

OpenKB comes with [multi-LLM support](https://docs.litellm.ai/docs/providers) via [LiteLLM](https://github.com/BerriAI/litellm) (pinned to a [safe version](https://docs.litellm.ai/blog/security-update-march-2026)). 

Create a `.env` file with your LLM API key. Choose your LLM during `okb init` or edit [`.okb/config.yaml`](#configuration).

```bash
LLM_API_KEY=your-llm-api-key-here
```

# 🧩 How It Works

```
raw/                              You drop files here
 │
 ├─ Short docs ──→ markitdown ──→ LLM reads full text
 │                                     │
 ├─ Long PDFs ──→ PageIndex ────→ LLM reads document trees
 │                                     │
 │                                     ▼
 │                              Wiki Compilation
 │                           (single LLM session)
 │                                     │
 ▼                                     ▼
wiki/
 ├── index.md            Knowledge base overview
 ├── log.md              Operations timeline
 ├── AGENTS.md           Wiki schema (LLM instructions)
 ├── sources/            Full-text conversions
 ├── summaries/          Per-document summaries
 ├── concepts/           Cross-document synthesis ← the good stuff
 ├── explorations/       Saved query results
 └── reports/            Lint reports
```

### Two paths, one wiki

| | Short documents | Long documents (PDF ≥ 20 pages) |
|---|---|---|
| **Convert** | markitdown → Markdown | PageIndex → tree index + summaries |
| **Images** | Extracted inline (pymupdf) | Extracted by PageIndex |
| **LLM reads** | Full text | Tree summaries only |
| **Result** | summary + concepts | summary + concepts |

Short docs are read in full by the LLM. Long PDFs are indexed by PageIndex into a hierarchical tree with summaries. The LLM reads the tree instead of the full text, avoiding context window limits while retaining structural understanding.

### The wiki compiles knowledge

When you add a document, the LLM:

1. Generates a **summary** page
2. Reads existing **concept** pages
3. Creates or updates concepts with cross-document synthesis
4. Updates the **index** and **log**

A single source might touch 10-15 wiki pages. Knowledge accumulates: each document enriches the existing wiki rather than sitting in isolation.

# 📦 Usage

### Commands

| Command | Description |
|---|---|
| `okb init` | Initialize a new knowledge base (interactive) |
| `okb add <file_or_dir>` | Add documents and compile to wiki |
| `okb query "question"` | Ask a question against the knowledge base |
| `okb query "question" --save` | Ask and save the answer to `wiki/explorations/` |
| `okb watch` | Watch `raw/` and auto-compile new files |
| `okb lint` | Run structural + knowledge health checks |
<!-- | `okb lint --fix` | Auto-fix what it can | -->
| `okb list` | List indexed documents and concepts |
| `okb status` | Show knowledge base stats |

### Configuration

Generated by `okb init`, stored in `.okb/config.yaml`:

```yaml
model: gpt-5.4                   # LLM model (any LiteLLM-supported provider)
api_key_env: LLM_API_KEY         # Environment variable for LLM API key
language: en                      # Wiki output language
pageindex_threshold: 20           # PDF pages threshold for PageIndex
pageindex_api_key_env: ""                # Env var name for PageIndex Cloud API key (default: auto-detect PAGEINDEX_API_KEY)
```

### PageIndex integration

For long documents, relying solely on summaries often leads to information loss.
We integrate [PageIndex](https://github.com/VectifyAI/PageIndex) into the knowledge base to provide structured, context-aware retrieval for long documents, avoiding the information loss common in summary-based approaches.

By default, PageIndex runs locally using the open-source version, with no external dependencies required.

#### Optional: Cloud Support

For large or complex PDFs, [PageIndex Cloud](https://docs.pageindex.ai/) can be used to access additional capabilities, including:

- OCR support for scanned PDFs (via hosted VLM models)
- Faster structure generation
- Scalable indexing for large documents

Set `PAGEINDEX_API_KEY` in your `.env` to enable cloud features:

```
PAGEINDEX_API_KEY=your_pageindex_api_key
```

### AGENTS.md

The `wiki/AGENTS.md` file defines wiki structure and conventions. It's the LLM's instruction manual for maintaining the wiki. Customize it to change how your wiki is organized.

At runtime, the LLM reads `AGENTS.md` from disk, so your edits take effect immediately.

### Using with Obsidian

OpenKB's wiki is a directory of Markdown files with `[[wikilinks]]`. Obsidian renders it natively.

1. Open `wiki/` as an Obsidian vault
2. Browse summaries, concepts, and explorations
3. Use graph view to see knowledge connections
4. Use Obsidian Web Clipper to add web articles to `raw/`

# 🔗 Learn More

### Compared to Karpathy's Approach

| | Karpathy's workflow | OpenKB |
|---|---|---|
| Short documents | LLM reads directly | markitdown → LLM reads |
| Long documents | Doesn't fit in context | PageIndex tree index |
| Supported formats | Web clipper → .md | PDF, Word, PPT, Excel, HTML, text, CSV, .md |
| Wiki compilation | LLM agent | LLM agent (same) |
| Q&A | Query over wiki | Wiki + PageIndex retrieval |
| Open source | No | Yes |

### Tech Stack

- [PageIndex](https://github.com/VectifyAI/PageIndex) — Vectorless, reasoning-based document indexing
- [markitdown](https://github.com/microsoft/markitdown) — Universal file-to-markdown conversion
- [OpenAI Agents SDK](https://github.com/openai/openai-agents-python) — Agent framework (supports non-OpenAI models via LiteLLM)
- [LiteLLM](https://github.com/BerriAI/litellm) — Multi-provider LLM gateway
- [Click](https://click.palletsprojects.com/) — CLI framework
- [watchdog](https://github.com/gorakhargosh/watchdog) — Filesystem monitoring

### License

Apache 2.0. See [LICENSE](LICENSE).

### Acknowledgments

Inspired by [Andrej Karpathy's LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f). Powered by [PageIndex](https://pageindex.ai/).
