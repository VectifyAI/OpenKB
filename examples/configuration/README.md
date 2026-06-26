# Configuration & setup

Everything that controls how OpenKB talks to your LLM lives in two places:
`.openkb/config.yaml` (model, language, tuning) and a `.env` file (your API key).

---

## Install

```bash
pip install openkb
```

OpenKB pins a **pre-release** of its PageIndex dependency
(`pageindex==0.3.0.dev1`), which some installers skip by default. If an install
can't resolve `pageindex`, allow pre-releases:

```bash
uv tool install openkb --prerelease=allow   # uv
pip install --pre openkb                     # pip
```

If `openkb` isn't found *after* a successful install, the console-script directory
isn't on your `PATH` (e.g. `pip --user` installs to `~/.local/bin`) — add it to
`PATH`.

---

## 1. Initialize a knowledge base

```bash
mkdir my-kb && cd my-kb
openkb init
```

`init` is interactive in a terminal and prompts for three things:

- **Model** — in LiteLLM `provider/model` format. OpenAI models can drop the
  prefix (`gpt-5.4`); others need it (`anthropic/claude-sonnet-4-6`,
  `gemini/gemini-3-flash-preview`).
- **LLM API key** — hidden input; if you provide one it's written to `.env` with
  `0600` permissions. Press Enter to skip and set it later.
- **Language** — the output language for your wiki. Any language works; e.g. the
  six official UN languages: `en` (English), `zh` (Chinese), `es` (Spanish),
  `fr` (French), `ar` (Arabic), `ru` (Russian).

Skip the prompts entirely with flags — handy in scripts:

```bash
openkb init --model anthropic/claude-sonnet-4-6 --language en
openkb init -m gpt-5.4 -l zh
```

> **Non-interactive (pipes/CI):** prompts are gated on a TTY. When stdin isn't a
> terminal, `init` uses the defaults instead of hanging, so
> `printf 'gpt-5.4\n\nen\n' | openkb init` works in a script.

`init` creates: `raw/`, `wiki/{summaries,concepts,entities,sources/images}`,
`wiki/AGENTS.md`, `wiki/index.md`, `wiki/log.md`, and `.openkb/config.yaml`.

---

## 2. `.openkb/config.yaml` reference

The file `init` writes is small; everything else is optional. This is the shipped
[`config.yaml.example`](../../config.yaml.example), verbatim:

```yaml
model: gpt-5.4                   # LLM model (any LiteLLM-supported provider)
language: en                     # Wiki output language
pageindex_threshold: 20          # PDF pages threshold for PageIndex

# Optional: override the entity-type vocabulary used for entity pages.
# Omit this key to use the default 7 types
# (person, organization, place, product, work, event, other).
# entity_types:
#   - person
#   - organization
#   - dataset
#   - model

# Optional: LLM / LiteLLM tuning. Keys are forwarded to LiteLLM; `timeout` and
# `extra_headers` apply per request, the rest are set as litellm.<key>.
# litellm:
#   timeout: 1200          # per-request timeout (s); raise for slow local backends (Ollama)
#   drop_params: true      # let LiteLLM drop params a provider rejects (e.g. Ollama)
#   num_retries: 3
#   extra_headers:         # extra HTTP headers some providers need (e.g. GitHub Copilot)
#     Editor-Version: vscode/1.95.0
#     Copilot-Integration-Id: vscode-chat
```

| Key | Default | What it does |
| --- | --- | --- |
| `model` | `gpt-5.4` | LLM used for all compile/query/chat work. |
| `language` | `en` | Language the wiki is written in. |
| `pageindex_threshold` | `20` | PDFs with this many pages **or more** take the long-doc (PageIndex) path; shorter ones go through the short-doc path. See [`pageindex-cloud/`](../pageindex-cloud/). |
| `entity_types` | 7 defaults | Custom vocabulary for entity pages. `other` is always kept. |
| `litellm:` | – | A pass-through block for LiteLLM. See below. |

### The `litellm:` block

OpenKB forwards this block to LiteLLM so you can tune anything LiteLLM supports —
you set it, LiteLLM uses it. Two keys are special:

- `timeout` and `extra_headers` are applied **per request** (they're needed on
  every call).
- Every other key (`drop_params`, `num_retries`, `ssl_verify`, …) is set on the
  `litellm` module as a process-wide global.

#### Slow local runtimes (Ollama, LM Studio, llama.cpp)

Local inference can be slow — on a Mac running **LM Studio**, a single compile
call can take minutes, and the **default request timeout will abort it** (this is
the usual cause of failures with local runtimes). Raise `timeout` (in seconds).
Add `drop_params` for backends that reject OpenAI-only params (e.g. Ollama):

```yaml
model: ollama/llama3.1     # or your LM Studio / llama.cpp model id
language: en
litellm:
  drop_params: true
  timeout: 1200            # raise further (e.g. 3600) for large local models
```

#### GitHub Copilot / ChatGPT-subscription providers

These need extra headers and use OAuth (no API key):

```yaml
model: github_copilot/gpt-4o
language: en
litellm:
  extra_headers:
    Editor-Version: vscode/1.95.0
    Copilot-Integration-Id: vscode-chat
```

---

## 3. API keys & providers

Set one universal key and OpenKB routes it to the right provider based on your
`model`. The shipped [`.env.example`](../../.env.example):

```bash
# OpenAI:    LLM_API_KEY=sk-...
# Anthropic: LLM_API_KEY=sk-ant-...
# Gemini:    LLM_API_KEY=AIza...
LLM_API_KEY=your-key-here
```

- **Provider auto-detection:** `model: anthropic/claude-sonnet-4-6` → your
  `LLM_API_KEY` is exported as `ANTHROPIC_API_KEY` automatically.
- **OAuth providers** (`chatgpt/*`, `github_copilot/*`) need **no** key — OpenKB
  won't warn about a missing one.
- **PageIndex Cloud** uses a separate `PAGEINDEX_API_KEY` (see
  [`pageindex-cloud/`](../pageindex-cloud/)).

**Where keys are read from** (first match wins, existing env always respected):

1. your shell environment
2. `<kb>/.env`
3. `~/.config/openkb/.env` (a global key shared across all your KBs)

---

## 4. Where is "the KB"?

Most commands need to know which KB they act on. Resolution order:

1. `--kb-dir /path/to/kb` (or `OPENKB_DIR=/path/to/kb`) — explicit override.
2. Walk up from the current directory looking for a `.openkb/` folder.
3. The global default registered by `openkb use <path>` (stored in
   `~/.config/openkb/global.yaml`).

```bash
# Run a query against a specific KB from anywhere
openkb --kb-dir ~/research-kb query "what changed in v2?"

# Make one KB the default, then forget about paths
openkb use ~/research-kb
openkb status        # now resolves ~/research-kb from any directory
```

---

Next: [`commands/`](../commands/) — the everyday ingest-and-query loop.
