<div align="center">

<a href="https://openkb.ai">
  <img src="https://docs.pageindex.ai/images/openkb.png" alt="OpenKB (by PageIndex)" />
</a>

# OpenKB — Open LLM Knowledge Base

<p align="center"><i>Scale to long documents&nbsp; • &nbsp;Reasoning-based retrieval&nbsp; • &nbsp;Native multi-modality&nbsp; • &nbsp;No Vector DB</i></p>

</div>

---

# 📑 What is OpenKB

**OpenKB (Open Knowledge Base)** is an open-source system (in CLI) that compiles raw documents into a structured, interlinked wiki-style knowledge base using LLMs, powered by [**PageIndex**](https://github.com/VectifyAI/PageIndex) for vectorless long document retrieval.

The idea is based on a [concept](https://x.com/karpathy/status/2039805659525644595) described by Andrej Karpathy: LLMs generate summaries, concept pages, and cross-references, all maintained automatically. Knowledge compounds over time instead of being re-derived on every query.

### Why not just traditional RAG?

Traditional RAG rediscovers knowledge from scratch on every query. Nothing accumulates. OpenKB compiles knowledge once into a persistent wiki, then keeps it current. Cross-references already exist. Contradictions are flagged. Synthesis reflects everything consumed.

### Features

- **Any format** — PDF, Word, PowerPoint, Excel, HTML, Markdown, text, CSV, and more via markitdown
- **Scale to long documents** — Long and complex documents are handled via [PageIndex](https://github.com/VectifyAI/PageIndex) tree indexing, enabling accurate, vectorless long-context retrieval
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
# 1. Create a directory for your knowledge base
mkdir my-kb && cd my-kb

# 2. Initialize the knowledge base
openkb init

# 3. Add documents
openkb add paper.pdf
openkb add ~/papers/                   # Add a whole directory
openkb add article.html

# 4. Ask questions
openkb query "What are the main findings?"

# 5. Check wiki health
openkb lint
```

### Set up your LLM

OpenKB comes with [multi-LLM support](https://docs.litellm.ai/docs/providers) (e.g., OpenAI, Claude, Gemini) via [LiteLLM](https://github.com/BerriAI/litellm) (pinned to a [safe version](https://docs.litellm.ai/blog/security-update-march-2026)).

Set your model during `openkb init`, or in [`.openkb/config.yaml`](#configuration), using `provider/model` LiteLLM format (like `anthropic/claude-sonnet-4-6`). OpenAI models can omit the prefix (like `gpt-5.4`).

Create a `.env` file with your LLM API key:

```bash
LLM_API_KEY=your_llm_api_key
```

# 🧩 How It Works

### Architecture

```
raw/                              You drop files here
 │
 ├─ Short docs ──→ markitdown ──→ LLM reads full text
 │                                     │
 ├─ Long PDFs ──→ PageIndex ────→ LLM reads document trees
 │                                     │
 │                                     ▼
 │                         Wiki Compilation (using LLM)
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

### Short vs. long document handling

| | Short documents | Long documents (PDF ≥ 20 pages) |
|---|---|---|
| **Convert** | markitdown → Markdown | PageIndex → tree index + summaries |
| **Images** | Extracted inline (pymupdf) | Extracted by PageIndex |
| **LLM reads** | Full text | Document trees |
| **Result** | summary + concepts | summary + concepts |

Short docs are read in full by the LLM. Long PDFs are indexed by PageIndex into a hierarchical tree with summaries. The LLM reads the tree instead of the full text, enabling better retrieval from long documents.

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
| `openkb init` | Initialize a new knowledge base (interactive) |
| `openkb add <file_or_dir>` | Add documents and compile to wiki |
| `openkb query "question"` | Ask a question against the knowledge base |
| `openkb query "question" --save` | Ask and save the answer to `wiki/explorations/` |
| `openkb watch` | Watch `raw/` and auto-compile new files |
| `openkb lint` | Run structural + knowledge health checks |
| `openkb list` | List indexed documents and concepts |
| `openkb status` | Show knowledge base stats |

<!-- | `openkb lint --fix` | Auto-fix what it can | -->

### Configuration

Settings are initialized by `openkb init`, and stored in `.openkb/config.yaml`:

```yaml
model: gpt-5.4                   # LLM model (any LiteLLM-supported provider)
language: en                     # Wiki output language
pageindex_threshold: 20          # PDF pages threshold for PageIndex
pageindex_api_key_env: ""        # (Optional) Environment variable for PageIndex Cloud API key
```

Model names use `provider/model` LiteLLM [format](https://docs.litellm.ai/docs/providers) (OpenAI models can omit the prefix):

| Provider | Model example |
|---|---|
| OpenAI | `gpt-5.4` |
| Anthropic | `anthropic/claude-sonnet-4-6` |
| Gemini | `gemini/gemini-3.1-pro-preview` |

### PageIndex integration

Long documents are challenging for LLMs due to context limits, context rot, and summarization loss.
[PageIndex](https://github.com/VectifyAI/PageIndex) solves this with vectorless, reasoning-based retrieval — building a hierarchical tree index that lets LLMs reason over the index for context-aware retrieval.

PageIndex runs locally by default using the [open-source version](https://github.com/VectifyAI/PageIndex), with no external dependencies required.

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

# 🧭 Learn More

### Compared to Karpathy's Approach

| | Karpathy's workflow | OpenKB |
|---|---|---|
| Short documents | LLM reads directly | markitdown → LLM reads |
| Long documents | Context limits, context rot | PageIndex tree index |
| Supported formats | Web clipper → .md | PDF, Word, PPT, Excel, HTML, text, CSV, .md |
| Wiki compilation | LLM agent | LLM agent (same) |
| Q&A | Query over wiki | Wiki + PageIndex retrieval |

### Tech Stack

- [PageIndex](https://github.com/VectifyAI/PageIndex) — Vectorless, reasoning-based document indexing
- [markitdown](https://github.com/microsoft/markitdown) — Universal file-to-markdown conversion
- [OpenAI Agents SDK](https://github.com/openai/openai-agents-python) — Agent framework (supports non-OpenAI models via LiteLLM)
- [LiteLLM](https://github.com/BerriAI/litellm) — Multi-provider LLM gateway
- [Click](https://click.palletsprojects.com/) — CLI framework
- [watchdog](https://github.com/gorakhargosh/watchdog) — Filesystem monitoring

### Contributing

Contributions are welcome! Please submit a pull request, or open an [issue](https://github.com/VectifyAI/OpenKB/issues) for bugs or feature requests. For larger changes, consider opening an issue first to discuss the approach.

### License

Apache 2.0. See [LICENSE](LICENSE).

### Support Us

If you find OpenKB useful, give us a star 🌟 — and check out [PageIndex](https://github.com/VectifyAI/PageIndex) too!  

<div>

[![Twitter](https://img.shields.io/badge/Twitter-000000?style=for-the-badge&logo=x&logoColor=white)](https://x.com/PageIndexAI)&ensp;
[![LinkedIn](https://img.shields.io/badge/LinkedIn-0077B5?style=for-the-badge&logo=linkedin&logoColor=white)](https://www.linkedin.com/company/vectify-ai/)&ensp;
[![Contact Us](https://img.shields.io/badge/Contact_Us-3B82F6?style=for-the-badge&logo=envelope&logoColor=white)](https://ii2abc2jejf.typeform.com/to/tK3AXl8T)

</div>
