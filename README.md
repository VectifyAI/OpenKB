<div align="center">

<a href="https://openkb.ai">
  <img src="https://docs.pageindex.ai/images/openkb.png" alt="OpenKB (by PageIndex)" />
</a>

# OpenKB — Open LLM Knowledge Base

<p align="center"><i>Scale to long documents&nbsp; • &nbsp;Reasoning-based retrieval&nbsp; • &nbsp;Native multi-modality&nbsp; • &nbsp;No Vector DB</i></p>

</div>

---

# 📑 What is OpenKB

**OpenKB (Open Knowledge Base)** is an open-source system (in CLI) that compiles raw documents into a structured, interlinked wiki-style knowledge base using LLMs, powered by [**PageIndex**](https://github.com/VectifyAI/PageIndex)'s vectorless, reasoning-based retrieval for long documents.

The idea is based on a [concept](https://x.com/karpathy/status/2039805659525644595) described by Andrej Karpathy: LLMs generate summaries, concept pages, and cross-references, all maintained automatically. Knowledge compounds over time instead of being re-derived on every query.

### Why not traditional RAG?

Traditional RAG rediscovers knowledge from scratch on every query. Nothing accumulates. OpenKB compiles knowledge once into a persistent wiki, then keeps it current. Cross-references already exist. Contradictions are flagged. Synthesis reflects everything consumed.

OpenKB has two layers: a **wiki foundation** that compiles and maintains your knowledge, and **generators** (query / chat / Skill Factory) that turn it into useful output. See [Usage](#️-usage) for the full command list.

### Features

- **Broad format support** — PDF, Word, Markdown, PowerPoint, HTML, Excel, CSV, text, URLs, and more
- **Scale to long documents** — Long and complex documents are handled via [PageIndex](https://github.com/VectifyAI/PageIndex) tree indexing, enabling accurate, vectorless context-aware retrieval
- **Native multi-modality** — Retrieves and understands figures, tables, and images, not just text
- **Compiled Wiki** — LLM compiles your documents into summaries, concept pages, entity pages, and cross-links, all kept in sync
- **Query & Chat** — One-off questions or multi-turn conversations over your wiki, with persisted sessions you can resume
- **Skill Factory** — Distill redistributable agent skills from your wiki
- **Obsidian compatible** — Wiki is plain `.md` files with `[[wikilinks]]`. Open in Obsidian for graph view and browsing

# 🚀 Getting Started

### Install

```bash
pip install openkb
```

<details>
<summary><i>Other install options:</i></summary>

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

# (Optional) Distill a redistributable agent skill from your wiki
openkb skill new my-expert "Reason like an expert on <your-topic>"
```

### Set up your LLM

OpenKB comes with [multi-LLM support](https://docs.litellm.ai/docs/providers) (e.g., OpenAI, Claude, Gemini) via [LiteLLM](https://github.com/BerriAI/litellm) (pinned to a [safe version](https://docs.litellm.ai/blog/security-update-march-2026)).

Set your model during `openkb init`, or in [`.openkb/config.yaml`](#configuration), using the `provider/model` LiteLLM format (like `anthropic/claude-sonnet-4-6`). OpenAI models can omit the prefix (like `gpt-5.4`).

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

### Short vs. Long Document Handling

| | Short documents | Long documents (PDF ≥ 20 pages) |
|---|---|---|
| **Convert** | markitdown → Markdown | PageIndex → tree index + summaries |
| **Images** | Extracted inline (pymupdf) | Extracted by PageIndex |
| **LLM reads** | Full text | Document trees |
| **Result** | summary + concepts | summary + concepts |

Short docs are read in full by the LLM. Long PDFs are indexed by PageIndex into a hierarchical tree with summaries. The LLM reads the tree instead of the full text, enabling better retrieval from long documents.

### Knowledge Compilation

When you add a document, the LLM:

1. Generates a **summary** page
2. Reads existing **concept** and **entity** pages
3. Creates or updates concepts with cross-document synthesis
4. Creates or updates **entity** pages (people, orgs, places, products)
5. Updates the **index** and **log**

A single source might touch 10-15 wiki pages. Knowledge accumulates: each document enriches the existing wiki rather than sitting in isolation.

# ⚙️ Usage

OpenKB commands fall into two layers: the **wiki foundation** (compile + manage your knowledge) and **generators** (turn that wiki into useful output).

## 🧱 Wiki Foundation — compile and maintain

| Command | Description |
|---|---|
| `openkb init` | Initialize a new knowledge base (interactive) |
| <code>openkb&nbsp;add&nbsp;&lt;file_or_dir_or_URL&gt;</code> | Add files, directories, or URLs and compile to wiki. URL content type (PDF or HTML) is auto-detected |
| `openkb list` | List indexed documents and concepts |
| `openkb status` | Show knowledge base stats |
| `openkb watch` | Watch `raw/` and auto-compile new files |
| `openkb lint` | Run structural + knowledge health checks |

<details>
<summary><b><i>More wiki commands:</i></b></summary>

| Command | Description |
|---|---|
| <code>openkb&nbsp;remove&nbsp;&lt;doc&gt;</code> | Remove a document and clean up its wiki pages, images, registry, and PageIndex state (use `--dry-run` to preview, `--keep-raw` / `--keep-empty` to retain artifacts) |
| <code>openkb&nbsp;recompile&nbsp;[&lt;doc&gt;]&nbsp;[--all]</code> | Re-run the current compile pipeline on already-indexed docs without re-indexing. Regenerates summaries and rewrites concept pages; manual edits are overwritten. Use `--dry-run` to preview, `--refresh-schema` to also update `wiki/AGENTS.md` |
| <code>openkb&nbsp;feedback&nbsp;["msg"]</code> | File feedback by opening a prefilled GitHub issue (use `--type bug/feature/question` to tag the issue) |

<!-- | `openkb lint --fix` | Auto-fix what it can | -->

</details>

## ✨ Generators — turn the wiki into output

A "generator" reads from the compiled wiki and produces something usable: an answer, a conversation, a skill folder. The wiki is the substrate; generators are the surfaces.

| Command | Output |
|---|---|
| <code>openkb&nbsp;query&nbsp;"question"</code> | A grounded answer with citations (use `--save` to persist to `wiki/explorations/`) |
| <code>openkb&nbsp;chat</code> | Interactive multi-turn session over the wiki (use `--resume`, `--list`, `--delete` to manage sessions) |
| | |
| <code>openkb&nbsp;skill&nbsp;new&nbsp;&lt;name&gt;&nbsp;"&lt;intent&gt;"</code> | A redistributable agent skill — see [Skill Factory](#-skill-factory--drop-in-a-book-out-comes-a-digital-expert) below |

<details>
<summary><b><i>More skill commands:</i></b></summary>

| Command | Output |
|---|---|
| <code>openkb&nbsp;skill&nbsp;validate&nbsp;[name]</code> | Validate compiled skills (YAML frontmatter, file sizes, wikilinks, scripts). Auto-runs at end of `skill new`. Use `--strict` to treat warnings as failures |
| <code>openkb&nbsp;skill&nbsp;eval&nbsp;&lt;name&gt;</code> | Trigger-accuracy evaluation: does the `description:` field actually fire? LLM generates eval prompts; grader LLM scores activation. `--save` persists the eval set |
| <code>openkb&nbsp;skill&nbsp;history&nbsp;&lt;name&gt;</code> / <code>openkb&nbsp;skill&nbsp;rollback&nbsp;&lt;name&gt;</code> | Version history for skills. Each overwrite saves the previous version to `iteration-N/` with a diff. Rollback restores any iteration |

</details>

### 💬 Query & Chat — *Ask the wiki*

`openkb query "..."` answers a single question. `openkb chat` is interactive; each turn carries history, so you can dig into a topic without re-typing context. Both use the same underlying wiki and retrieval primitives.

```bash
openkb query "What does the literature say about attention scaling?"

openkb chat                       # start a new session
openkb chat --resume              # resume the most recent session
openkb chat --resume 20260411     # resume by id (unique prefix works)
openkb chat --list                # list all sessions
openkb chat --delete <id>         # delete a session
```

Inside a chat, type `/` to access slash commands (Tab to complete).

<details>
<summary><i>Slash commands:</i></summary>

- `/help` — list available commands
- `/status` — show knowledge base status
- `/list` — list all documents
- `/add <path>` — add a document or directory without leaving the chat
- `/skill new <name> "<intent>"` — compile a skill from this chat (see below)
- `/save [name]` — export the transcript to `wiki/explorations/`
- `/clear` — start a fresh session (the current one stays on disk)
- `/lint` — run knowledge base lint
- `/exit` — exit (Ctrl-D also works)

</details>

### 🛠 Skill Factory — *Drop in a book; out comes a digital expert.*

The newest generator. `openkb skill new` distills an [agent skill](https://docs.claude.com/en/docs/build-with-claude/skills) from any subset of your wiki, a portable folder that major agents (Claude Code, Codex, etc.) install and load natively. Drop in a book's worth of papers; out comes a specialist that other agents can call on.

```bash
openkb skill new karpathy-thinking \
  "Reason about transformers and attention in Karpathy's style"
```

<details>
<summary><i>Output:</i></summary>

```
<kb>/output/skills/karpathy-thinking/
├── SKILL.md                   # YAML frontmatter + when-to-use + approach
├── references/                # depth material the agent loads on demand
│   ├── methodology.md
│   └── key-quotes.md
└── (scripts/)                 # optional, only if intent implies computation
```

…plus an auto-updated `<kb>/.claude-plugin/marketplace.json` so the whole KB is one-line installable.

</details>

<details>
<summary><i>Install locally:</i></summary>

```bash
cp -r output/skills/karpathy-thinking ~/.claude/skills/
```

</details>

<details>
<summary><i>Share with others:</i></summary>

Push your KB to GitHub, then anyone runs:

```bash
npx skills@latest add <your-org>/<your-repo>
```

</details>

<details>
<summary><i>Iterate from chat:</i></summary>

Compilation is one-shot, but follow-up edits aren't. Inside `openkb chat`, you can refine without re-running the whole pipeline:

```
/skill new karpathy-thinking "Reason about transformers like Karpathy"
[generation streams]
> description is too generic, make it about transformer implementations specifically
[agent edits SKILL.md frontmatter in place]
```

</details>

<details>
<summary><i>Quality gates:</i></summary>

Structural validation, trigger-accuracy + body-coverage evaluation, and full history/rollback:

```bash
# Lint structure (auto-runs at end of `skill new`)
openkb skill validate karpathy-thinking
openkb skill validate --strict          # treat warnings as failures

# Does the description actually fire when it should?
openkb skill eval karpathy-thinking --save

# History + rollback if a new iteration regresses
openkb skill history karpathy-thinking
openkb skill rollback karpathy-thinking --to 2
```

</details>

# 🔧 Configuration

### Settings

OpenKB settings are initialized by `openkb init` and are stored in `.openkb/config.yaml`:

```yaml
model: gpt-5.4                   # LLM model (any LiteLLM-supported provider)
language: en                     # Wiki output language
pageindex_threshold: 20          # PDF pages threshold for PageIndex
```

Model names use `provider/model` LiteLLM [format](https://docs.litellm.ai/docs/providers) (OpenAI models can omit the prefix):

| Provider | Model example |
|---|---|
| OpenAI | `gpt-5.4` |
| Anthropic | `anthropic/claude-sonnet-4-6` |
| Gemini | `gemini/gemini-3.1-pro-preview` |

<details>
<summary><i>Advanced options:</i></summary>

`entity_types` (optional): a YAML list overriding the entity-type vocabulary used for entity pages; omit it to use the default `person`, `organization`, `place`, `product`, `work`, `event`, `other`.

`extra_headers` (optional): a YAML mapping of extra HTTP headers sent with every LLM request (forwarded to LiteLLM's `extra_headers`). Useful for providers that expect custom headers, e.g. GitHub Copilot IDE-auth headers:

```yaml
extra_headers:
  Editor-Version: vscode/1.95.0
  Copilot-Integration-Id: vscode-chat
```

Subscription-based providers that authenticate via OAuth device flow (e.g. `chatgpt/*`, `github_copilot/*`) need no API key; OpenKB skips the missing-key warning for them.

</details>

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

### AGENTS.md

The `wiki/AGENTS.md` file defines wiki structure and conventions. It's the LLM's instruction manual for maintaining the wiki. Customize it to change how your wiki is organized.

At runtime, the LLM reads `AGENTS.md` from disk, so your edits take effect immediately.

# 🔌 Integrations

### Using with Obsidian

OpenKB's wiki is a directory of Markdown files with `[[wikilinks]]`. Obsidian renders it natively.

1. Open `wiki/` as an Obsidian vault
2. Browse summaries, concepts, and explorations
3. Use graph view to see knowledge connections
4. Use Obsidian Web Clipper to add web articles to `raw/`

### Using with Claude Code / Codex / Gemini CLI

OpenKB ships a `SKILL.md` so any agent CLI can read your compiled wiki. No extra runtime, no MCP setup, just install the skill once.

<details>
<summary><i>Claude Code:</i></summary>

```
/plugin marketplace add VectifyAI/OpenKB
/plugin install openkb@vectify
```

</details>

<details>
<summary><i>OpenAI Codex CLI:</i></summary>

*(no marketplace command yet; manual symlink)*

```bash
git clone https://github.com/VectifyAI/OpenKB.git ~/openkb-src
mkdir -p ~/.agents/skills
ln -s ~/openkb-src/skills/openkb ~/.agents/skills/openkb
```

</details>

<details>
<summary><i>Gemini CLI:</i></summary>

```bash
gemini skills install https://github.com/VectifyAI/OpenKB.git --path skills/openkb --consent
```

</details>

The skill is read-only; it won't run `openkb add`, `remove`, or `lint --fix` without you asking. See [`skills/openkb/SKILL.md`](skills/openkb/SKILL.md) for the full instruction set.

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

Contributions are welcome! Please submit a pull request, or open an [issue](https://github.com/VectifyAI/OpenKB/issues) for bugs or feature requests. For larger changes, consider opening an issue first to discuss the approach.

### License

Apache 2.0. See [LICENSE](LICENSE).

### 🌐 Open-Source Ecosystem

Other [open-source projects](https://docs.pageindex.ai/open-source) from the PageIndex ecosystem:

- [PageIndex](https://github.com/VectifyAI/PageIndex) — Vectorless, reasoning-based RAG framework for long documents
- [ChatIndex](https://github.com/VectifyAI/ChatIndex) — Tree indexing and retrieval for long conversational histories and memory
- [ConDB](https://github.com/VectifyAI/ConDB) — A KV-cache native context database for tree-based retrieval at scale
- [PageIndex MCP](https://github.com/VectifyAI/pageindex-mcp) — The MCP server for PageIndex

### Support Us

If you find OpenKB useful, please give us a star 🌟 — and check out [**PageIndex**](https://github.com/VectifyAI/PageIndex) too!  

<div>

[![Twitter](https://img.shields.io/badge/Twitter-000000?style=for-the-badge&logo=x&logoColor=white)](https://x.com/PageIndexAI)&ensp;
[![LinkedIn](https://img.shields.io/badge/LinkedIn-0077B5?style=for-the-badge&logo=linkedin&logoColor=white)](https://www.linkedin.com/company/vectify-ai/)&ensp;
[![Contact Us](https://img.shields.io/badge/Contact_Us-3B82F6?style=for-the-badge&logo=envelope&logoColor=white)](https://ii2abc2jejf.typeform.com/to/tK3AXl8T)

</div>
