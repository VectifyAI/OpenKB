<div align="center">

<a href="https://openkb.ai">
  <img src="https://docs.pageindex.ai/images/openkb.png" alt="OpenKB (by PageIndex)" />
</a>

<br />
<br />

<p align="center">
<a href="https://trendshift.io/repositories/26145" target="_blank"><img src="https://trendshift.io/api/badge/repositories/26145" alt="VectifyAI%2FOpenKB | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/></a>
</p>

# OpenKB: Open LLM Knowledge Base

<p align="center"><i>Scale to long documents&nbsp; • &nbsp;Reasoning-based retrieval&nbsp; • &nbsp;Native multi-modality&nbsp; • &nbsp;No Vector DB</i></p>

</div>

<details open>
<summary><h2>📢 Recent Updates</h2></summary>

- *Google Open Knowledge Format (OKF)*: Wiki pages follow the [Google OKF](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing) specification for knowledge sharing.
- *Entity Pages*: People, orgs, places, and products as dedicated wiki pages, auto-extracted and kept in sync.

</details>

---

# 📑 What is OpenKB

**OpenKB (Open Knowledge Base)** is an open-source system (in CLI) that compiles raw documents into a structured, interlinked wiki-style knowledge base using LLMs, powered by [**PageIndex**](https://github.com/VectifyAI/PageIndex)'s vectorless, reasoning-based retrieval for long documents.

The idea is based on a [concept](https://x.com/karpathy/status/2039805659525644595) described by Andrej Karpathy: LLMs generate summaries, concept pages, and cross-references, all maintained automatically. Knowledge compounds over time instead of being re-derived on every query.

### Why not traditional RAG?

Traditional RAG rediscovers knowledge from scratch on every query. Nothing accumulates. OpenKB compiles knowledge once into a persistent wiki, then keeps it current. Cross-references already exist, contradictions are flagged, and synthesis reflects everything consumed.

OpenKB has two layers: a **wiki foundation** that compiles and maintains your knowledge, and **generators** (query / chat / Skill Factory) that turn it into useful output. See [Usage](#️-usage) for the full command list.

### Features

- **Broad format support:** PDF, Word, Markdown, PowerPoint, HTML, Excel, CSV, text, URLs, and more.
- **Scales to long documents:** Long and complex documents are handled via [PageIndex](https://github.com/VectifyAI/PageIndex) tree indexing, enabling accurate, vectorless, context-aware retrieval.
- **Native multi-modality:** Retrieves and understands figures, tables, and images, not just text.
- **Compiled wiki:** The LLM compiles your documents into summaries, concept pages, entity pages, and cross-links, all kept in sync.
- **Query & chat:** One-off questions or multi-turn conversations over your wiki, with persisted sessions to resume.
- **Skill Factory:** Distills redistributable agent skills from your wiki.
- **OKF-ready:** Wiki pages follow the [Google OKF](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing) specification for knowledge sharing.
- **Obsidian-compatible:** The wiki is plain `.md` files with cross-links. Opens in Obsidian for graph view.

# 🚀 Getting Started

### Install

```bash
pip install openkb
```

<details>
<summary><b><i>Other install options:</i></b></summary>

- **Latest from GitHub:**

  ```bash
  pip install git+https://github.com/VectifyAI/OpenKB.git
  ```

- **Install from source** (editable, for development):

  ```bash
  git clone https://github.com/VectifyAI/OpenKB.git
  cd OpenKB
  pip install -e .
  ```

</details>

### Quick Start

```bash
# 1. Create a directory for your knowledge base
mkdir my-kb && cd my-kb

# 2. Initialize the knowledge base
openkb init

# 3. Add documents
openkb add paper.pdf
openkb add ~/papers/                            # Add a whole directory
openkb add https://arxiv.org/pdf/2509.11420     # Or fetch from a URL

# 4. Ask a question
openkb query "What are the main findings?"

# 5. Or chat interactively
openkb chat

# (Optional) Turn the wiki into other outputs
openkb skill new my-expert "Reason like an expert on <your-topic>"   # a portable agent skill
openkb visualize                                                     # an interactive knowledge graph
openkb deck new my-deck "An intro deck on <your-topic>"              # slides — a single-file HTML deck
```

### Set up your LLM

OpenKB supports [multiple LLM providers](https://docs.litellm.ai/docs/providers) (OpenAI, Claude, Gemini, etc.) via [LiteLLM](https://github.com/BerriAI/litellm) (pinned to a [safe version](https://docs.litellm.ai/blog/security-update-march-2026)).

Set your model during `openkb init` or in [`.openkb/config.yaml`](#configuration) using the `provider/model` LiteLLM format (e.g. `anthropic/claude-sonnet-4-6`). OpenAI models can omit the prefix (e.g. `gpt-5.4`).

Create a `.env` file with your LLM API key:

```bash
LLM_API_KEY=your_llm_api_key
```

# 🧩 How OpenKB Works

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
wiki/                                  │            ← the foundation
 ├── index.md            Knowledge base overview
 ├── log.md              Operations timeline
 ├── AGENTS.md           Wiki schema (LLM instructions)
 ├── sources/            Full-text conversions
 ├── summaries/          Per-document summaries
 ├── concepts/           Cross-document synthesis
 ├── entities/           Specific named things (people, orgs, places, products)
 ├── explorations/       Saved query results
 └── reports/            Lint reports
                                       │
                ┌──────────────────────┼──────────────────────┐
                ▼                      ▼                      ▼
            query / chat         Skill Factory          (future)
          (LLM answers from    (redistributable       ppt / podcast /
            the wiki)           agent skills)           report / …
```

### Short vs Long Document Handling

| | Short documents | Long documents (PDF ≥ 20 pages) |
|---|---|---|
| **Convert** | markitdown → Markdown | PageIndex → tree index + summaries |
| **Images** | Extracted inline (pymupdf) | Extracted by PageIndex |
| **LLM reads** | Full text | Document trees |
| **Result** | summary + concepts | summary + concepts |

Short documents are read in full by the LLM. Long PDFs are processed by [PageIndex](https://github.com/VectifyAI/PageIndex) into a hierarchical tree index. The LLM reads the tree instead of the full text, enabling accurate and scalable retrieval for long documents.

### Knowledge Compilation

When you add a document, the LLM:

1. Generates a **summary** page
2. Reads existing **concept** and **entity** pages
3. Creates or updates concepts with cross-document synthesis
4. Creates or updates **entity** pages (people, orgs, places, products)
5. Updates the **index** and **log**

A single source might touch 10--15 wiki pages. Knowledge accumulates: each document enriches the existing wiki rather than sitting in isolation.

# ⚙️ Usage

OpenKB commands fall into two layers: the **wiki foundation** (compile + manage your knowledge) and **generators** (turn that wiki into useful output). Each links to a concrete walkthrough — a real artifact OpenKB generated from one sample paper (browse them all in [`examples/`](examples/)).

## Layer 1: 🧱 Wiki Foundation — compile and maintain

| Command | Description |
|---|---|
| `openkb init` | Initialize a new knowledge base (interactive) |
| <code>openkb&nbsp;add&nbsp;&lt;file_or_dir_or_URL&gt;</code> | Add files, directories, or URLs and compile to wiki (URL content type is auto-detected) |
| `openkb list` | List indexed documents and concepts |
| `openkb status` | Show knowledge base stats |
| `openkb watch` | Watch `raw/` and auto-compile new files |
| `openkb lint` | Run structural and knowledge health checks |

<details>
<summary><i>More wiki commands:</i></summary>
<br>

| Command | Description |
|---|---|
| <code>openkb&nbsp;remove&nbsp;&lt;doc&gt;</code> | Remove a document and clean up its wiki pages, images, registry, and PageIndex state (`--dry-run` to preview, `--keep-raw` / `--keep-empty` to retain artifacts) |
| <code>openkb&nbsp;recompile&nbsp;[&lt;doc&gt;]&nbsp;[--all]</code> | Re-run the compile pipeline on already-indexed docs without re-indexing. Regenerates summaries and rewrites concept pages; manual edits are overwritten (`--dry-run` to preview, `--refresh-schema` to also update `wiki/AGENTS.md`) |
| <code>openkb&nbsp;feedback&nbsp;["msg"]</code> | File feedback by opening a prefilled GitHub issue (`--type bug/feature/question` to tag it) |

</details>

→ **Example:** the everyday loop walked through end to end — [`examples/commands/`](examples/commands/).

## Layer 2: 💡 Generators — turn the wiki into output

A "generator" reads from the compiled wiki and produces something usable: an answer, a conversation, a skill folder. The wiki is the substrate; generators are the surfaces.

| Command | Output | Example |
|---|---|---|
| <code>openkb&nbsp;query&nbsp;"question"</code> | A grounded answer with citations (`--save` to persist to `wiki/explorations/`) | [query & save](examples/commands/) |
| <code>openkb&nbsp;chat</code> | Interactive multi-turn session over the wiki (`--resume`, `--list`, `--delete` to manage sessions) | [chat](examples/chat/) |
| <code>openkb&nbsp;visualize</code> | A self-contained interactive knowledge graph at `output/visualize/graph.html` — 3D, mind-map, and radial views | [visualize](examples/visualize/) |
| <code>openkb&nbsp;skill&nbsp;new&nbsp;&lt;skill-name&gt;&nbsp;"&lt;intent&gt;"</code> | Distill a redistributable agent skill from your wiki (see [Skill Factory](#skill-factory) below) | [skills](examples/skills/) |
| <code>openkb&nbsp;deck&nbsp;new&nbsp;&lt;name&gt;&nbsp;"&lt;intent&gt;"</code> | Generate a single-file HTML slide deck (`--skill` picks a theme, `--critique` runs a quality pass) | [slides](examples/slides/) |

<details>
<summary><i>More skill commands:</i></summary>
<br>

| Command | Output |
|---|---|
| <code>openkb&nbsp;skill&nbsp;validate&nbsp;[name]</code> | Validate compiled skills (auto-runs after `skill new`) |
| <code>openkb&nbsp;skill&nbsp;eval&nbsp;&lt;name&gt;</code> | Check the skill triggers on the right prompts |
| <code>openkb&nbsp;skill&nbsp;history&nbsp;&lt;name&gt;</code> / <code>openkb&nbsp;skill&nbsp;rollback&nbsp;&lt;name&gt;</code> | Version history + rollback for skills |

</details>

<a id="skill-factory"></a>

### 🛠 Skill Factory — *drop in a book; out comes a digital expert.*

The flagship generator: `openkb skill new` distills a portable [agent skill](https://docs.claude.com/en/docs/build-with-claude/skills) from your wiki that Claude Code, Codex, and Gemini can install and load natively. Drop in a book's worth of papers; out comes a specialist other agents can call on. → A real generated skill, plus install / share / `eval` / rollback, is walked through in **[`examples/skills/`](examples/skills/)**.

# 🔧 Configuration

### Settings

`openkb init` writes `.openkb/config.yaml`:

```yaml
model: gpt-5.4                   # LLM model (any LiteLLM-supported provider)
language: en                     # Wiki output language
pageindex_threshold: 20          # PDF pages threshold for PageIndex
```

The full settings reference — `entity_types`, OAuth providers (`chatgpt/*`, `github_copilot/*`), and LiteLLM tuning (timeouts for slow local runtimes like Ollama / LM Studio, `drop_params`, GitHub Copilot headers, install notes) — is in **[`examples/configuration/`](examples/configuration/)**.

### PageIndex Setup

Long-document retrieval is a [known challenge](https://x.com/karpathy/status/2039823314982744522) for LLMs. [PageIndex](https://github.com/VectifyAI/PageIndex) solves this with vectorless, reasoning-based retrieval, by building a hierarchical tree index that lets LLMs reason over the index for context-aware retrieval.

PageIndex runs locally by default using the [open-source version](https://github.com/VectifyAI/PageIndex), with no external dependencies required.

***Cloud Support*** *(Optional)*:

For large or complex PDFs, [PageIndex Cloud](https://docs.pageindex.ai/) can be used to access additional capabilities, including:

- OCR support for scanned PDFs (via hosted VLM models)
- Faster structure generation
- Scalable indexing for large documents

Set `PAGEINDEX_API_KEY` in your `.env` to enable cloud features:

```
PAGEINDEX_API_KEY=your_pageindex_api_key
```

→ **Example:** local vs. cloud indexing, and importing a cloud-indexed doc — [`examples/pageindex-cloud/`](examples/pageindex-cloud/).

### AGENTS.md

The `wiki/AGENTS.md` file defines wiki structure and conventions. It's the LLM's instruction manual for maintaining the wiki. Customize it to change how your wiki is organized.

The LLM reads `AGENTS.md` from disk at runtime, so your edits take effect immediately.

# 🔌 Integrations

### Using with Obsidian

The wiki is a directory of Markdown files with `[[wikilinks]]`. Obsidian renders it natively.

1. Open `wiki/` as an Obsidian vault
2. Browse summaries, concepts, and explorations
3. Use graph view to see knowledge connections
4. Use Obsidian Web Clipper to add web articles to `raw/`

### Using with Claude Code / Codex / Gemini CLI

OpenKB ships a `SKILL.md` so any agent can read your compiled wiki. No extra runtime, no MCP setup, just install the skill once.

<details>
<summary><i>Claude Code:</i></summary>
<br>

```
/plugin marketplace add VectifyAI/OpenKB
/plugin install openkb@vectify
```

</details>

<details>
<summary><i>OpenAI Codex CLI:</i></summary>
<br>

*(no marketplace command yet; manual symlink)*

```bash
git clone https://github.com/VectifyAI/OpenKB.git ~/openkb-src
mkdir -p ~/.agents/skills
ln -s ~/openkb-src/skills/openkb ~/.agents/skills/openkb
```

</details>

<details>
<summary><i>Gemini CLI:</i></summary>
<br>

```bash
gemini skills install https://github.com/VectifyAI/OpenKB.git --path skills/openkb --consent
```

</details>

The skill is read-only. It won't run `openkb add`, `remove`, or `lint --fix` without you asking. See [`skills/openkb/SKILL.md`](skills/openkb/SKILL.md) for the full instruction set.

# 🧭 Learn More

### Compared to Karpathy's Approach

| | Karpathy's workflow | OpenKB |
|---|---|---|
| Short documents | LLM reads directly | markitdown → LLM reads |
| Long documents | Context limits, context rot | PageIndex tree index |
| Input sources | Web clipper → .md | PDF, Word, PPT, Excel, HTML, text, CSV, .md, URLs |
| Wiki compilation | LLM agent | LLM agent (same) |
| Entity extraction | Manual | Automatic (people, orgs, places, products) |
| Q&A | Query over wiki | Wiki + PageIndex retrieval |
| Output | Wiki only | Wiki + Skill Factory + agent CLI integration |

### The Stack

- [PageIndex](https://github.com/VectifyAI/PageIndex) — Vectorless, reasoning-based document indexing and retrieval
- [markitdown](https://github.com/microsoft/markitdown) — Universal file-to-markdown conversion
- [OpenAI Agents SDK](https://github.com/openai/openai-agents-python) — Agent framework (supports non-OpenAI models via LiteLLM)
- [LiteLLM](https://github.com/BerriAI/litellm) — Multi-provider LLM gateway
- [Click](https://click.palletsprojects.com/) — CLI framework
- [watchdog](https://github.com/gorakhargosh/watchdog) — Filesystem monitoring

### Roadmap

- [ ] Extend long document handling to non-PDF formats
- [ ] Scale to large document collections with nested folder support
- [ ] Hierarchical concept (topic) indexing for massive knowledge bases
- [ ] Database-backed storage engine
- [ ] Web UI for browsing and managing wikis

### Contributing

Contributions are welcome! Submit a pull request or open an [issue](https://github.com/VectifyAI/OpenKB/issues) for bugs and feature requests. For larger changes, consider opening an issue first to discuss the approach.

### License

Apache 2.0. See [LICENSE](LICENSE).

### 🌐 Open-Source Ecosystem

Other [open-source projects](https://docs.pageindex.ai/open-source) from the PageIndex ecosystem:

- [PageIndex](https://github.com/VectifyAI/PageIndex): Vectorless, reasoning-based RAG framework for long documents
- [ChatIndex](https://github.com/VectifyAI/ChatIndex): Tree indexing and retrieval for long conversational histories and memory
- [ConDB](https://github.com/VectifyAI/ConDB): A KV-cache native context database for tree-based retrieval at scale
- [PageIndex MCP](https://github.com/VectifyAI/pageindex-mcp): MCP server for PageIndex

### Support Us

If you find OpenKB useful, please give us a star 🌟 — and check out [**PageIndex**](https://github.com/VectifyAI/PageIndex) too!  

<div>

[![Twitter](https://img.shields.io/badge/Twitter-000000?style=for-the-badge&logo=x&logoColor=white)](https://x.com/PageIndexAI)&ensp;
[![LinkedIn](https://img.shields.io/badge/LinkedIn-0077B5?style=for-the-badge&logo=linkedin&logoColor=white)](https://www.linkedin.com/company/vectify-ai/)&ensp;
[![Contact Us](https://img.shields.io/badge/Contact_Us-3B82F6?style=for-the-badge&logo=envelope&logoColor=white)](https://ii2abc2jejf.typeform.com/to/tK3AXl8T)

</div>
