<div align="center">

<a href="https://openkb.ai">
  <img src="https://docs.pageindex.ai/images/general/openkb.png" alt="OpenKB (by PageIndex)" />
</a>

# OpenKB (Open Knowledge Base)

<h3 align="center">LLM-Powered Wiki Knowledge Base</h3>

<p align="center"><i>Scale to long documents&nbsp; ◦ &nbsp;Reasoning-based retrieval&nbsp; ◦ &nbsp;Native multimodality support&nbsp; ◦ &nbsp;No Vector DB</i></p>

</div>

---

# 📑 Introduction to OpenKB

Andrej Karpathy [described](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) a workflow where LLMs compile raw documents into a structured, interlinked markdown wiki — summaries, concept pages, cross-references — all maintained automatically. Knowledge compounds over time instead of being re-derived on every query.

**OpenKB** (Open Knowledge Base) is an open-source CLI that implements this workflow, powered by [PageIndex](https://github.com/VectifyAI/PageIndex) for long document understanding and [markitdown](https://github.com/microsoft/markitdown) for broad format support.

### Why not just RAG?

RAG rediscovers knowledge from scratch on every query. Nothing accumulates. OpenKB compiles knowledge once into a persistent wiki, then keeps it current. Cross-references already exist. Contradictions are flagged. Synthesis reflects everything consumed.

### Features

- **Any format** — PDF, Word, PowerPoint, Excel, HTML, Markdown, text, CSV, and more via markitdown
- **Long documents** — Books and reports that exceed LLM context windows are handled via [PageIndex](https://github.com/VectifyAI/PageIndex) tree indexing
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

Create a `.env` file with your API key:

```bash
OPENAI_API_KEY=sk-...
```

OpenKB uses [LiteLLM](https://docs.litellm.ai/docs/providers) — any provider works. Set the model during `okb init` or edit `.okb/config.yaml`.

# 🧩 How It Works

```
raw/                              You drop files here
 │
 ├─ Short docs ──→ markitdown ──→ LLM reads full text
 │                                     │
 ├─ Long PDFs ──→ PageIndex ────→ LLM reads tree summaries
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

| | Short documents | Long documents (PDF ≥ 50 pages) |
|---|---|---|
| **Convert** | markitdown → Markdown | PageIndex → tree index + summaries |
| **Images** | Extracted inline (pymupdf) | Extracted by PageIndex |
| **LLM reads** | Full text | Tree summaries only |
| **Result** | summary + concepts | summary + concepts |

Short docs are read in full by the LLM. Long PDFs are indexed by PageIndex into a hierarchical tree with summaries — the LLM reads the tree instead of the full text, avoiding context window limits while retaining structural understanding.

> **⚡ PageIndex Cloud API** — By default, PageIndex runs locally. Set `PAGEINDEX_API_KEY` in your `.env` to use [PageIndex Cloud](https://pageindex.ai/) for faster indexing. Get an API key at [pageindex.dev](https://pageindex.dev).

### The wiki compiles knowledge

When you add a document, the LLM:

1. Generates a **summary** page
2. Reads existing **concept** pages
3. Creates or updates concepts with cross-document synthesis
4. Updates the **index** and **log**

A single source might touch 10-15 wiki pages. Knowledge accumulates — each document enriches the existing wiki rather than sitting in isolation.

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
api_key_env: OPENAI_API_KEY      # Environment variable for API key
language: en                      # Wiki output language
pageindex_threshold: 50           # PDF pages threshold for PageIndex
pageindex_api_key_env: ""                # Env var name for PageIndex Cloud API key (default: auto-detect PAGEINDEX_API_KEY)
```

### AGENTS.md

The `wiki/AGENTS.md` file defines wiki structure and conventions. It's the LLM's instruction manual for maintaining the wiki. Customize it to change how your wiki is organized.

At runtime, the LLM reads `AGENTS.md` from disk — your edits take effect immediately.

### Using with Obsidian

OpenKB's wiki is a directory of Markdown files with `[[wikilinks]]` — Obsidian renders it natively.

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

Apache 2.0 — see [LICENSE](LICENSE)

### Acknowledgments

Inspired by [Andrej Karpathy's LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f). Powered by [PageIndex](https://pageindex.ai/).
