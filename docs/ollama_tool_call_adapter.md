# Ollama Tool-Call Adapter

## Problem

When using Ollama models with OpenKB's `query` and `chat` commands, the
openai-agents SDK tool-calling loop fails in three ways (issue #205):

1. **LiteLLM prompt injection fallback** — models addressed as
   `ollama/<model>` are treated as "legacy" by LiteLLM. Tools are stripped
   from the API call and injected into the prompt text with
   `format: json`. The model returns tool-call JSON in `content` instead
   of `tool_calls`, and the SDK cannot execute the tool.

2. **Tool-name hallucination** — small models call non-existent tools
   (e.g. `get_topics` instead of `read_file`), causing
   `ModelBehaviorError: Tool X not found in agent wiki-query`.

3. **Timeouts** — local models on modest hardware can take 60-130s per
   tool-calling turn; LiteLLM's default timeout is too short.

## Solution

OpenKB includes an adapter (`openkb/agent/ollama_adapter.py`) with four
mechanisms. All are **Ollama-only** — other providers keep the original
behaviour unchanged.

### 1. Model rewrite: `ollama/` → `ollama_chat/`

`rewrite_ollama_model()` converts `ollama/<model>` to
`ollama_chat/<model>` so LiteLLM uses the native Ollama Chat API endpoint,
which supports `tools` natively (Ollama >= 0.4). This is the **primary
fix** — without it, LiteLLM falls back to prompt injection and tool calls
never work.

Applied in `build_query_agent()`, `build_run_config_from_bundle()`, and
all 12 model-resolution sites in `cli.py`.

### 2. Retry on tool-name hallucination

`arun_with_retry()` and `run_streamed_with_retry()` wrap the SDK Runner
in try/except for `ModelBehaviorError`. On error, a corrective message is
appended:

```
[SYSTEM CORRECTION 1] You tried to call a tool named 'get_topics',
but that tool does not exist. The only available tools are:
read_file, get_page_content, get_image. Please answer the original
question using ONLY these tools. Do not invent tool names.
```

Up to `tool_call_retries` times (configurable, default 3).

### 3. Configurable timeout

`_ensure_ollama_settings()` injects the resolved timeout into the agent's
`ModelSettings.extra_args` if none is already set.

### 4. `drop_params` and `api_base` propagation

For `ollama_chat`, LiteLLM rejects `parallel_tool_calls` and does not read
`OPENAI_API_BASE`. The adapter sets:
- `litellm.drop_params = True` (drop unsupported params)
- `OLLAMA_API_BASE` from `OPENAI_API_BASE` (env var + litellm module level)
- `api_base` in compiler LLM calls (for the `add` path)

## Configuration

All settings are in `config.yaml`, under the optional `ollama:` key:

```yaml
ollama:
  timeout: 300              # per-request timeout (s), default 300
  tool_call_retries: 3      # max retries on hallucinated tool names, default 3
```

### Timeout precedence (highest to lowest)

1. `ollama.timeout` in `config.yaml`
2. `litellm.timeout` in `config.yaml` (process-wide)
3. Top-level `timeout:` in `config.yaml`
4. Built-in default: **300s**

### Retry precedence

1. `ollama.tool_call_retries` in `config.yaml`
2. Built-in default: **3**

### Example config for slow hardware

```yaml
model: ollama_chat/gemma4:12b
language: en
parallel_tool_calls: false
ollama:
  timeout: 600              # 10 minutes per request
  tool_call_retries: 5      # more patience for small models
litellm:
  drop_params: true
```

### Environment variables

For Ollama, set in `.env`:

```
LLM_API_KEY=dummy
OPENAI_API_BASE=http://your-ollama-host:11434
```

The adapter automatically propagates `OPENAI_API_BASE` → `OLLAMA_API_BASE`.

## How It Works

### Detection

`is_ollama_backend(model)` returns `True` for any model string containing
`ollama/` or `ollama_chat/` (with or without `litellm/` prefix).

### Rewrite

`rewrite_ollama_model(model)` converts:
- `ollama/gemma4:12b` → `ollama_chat/gemma4:12b`
- `litellm/ollama/gemma4:12b` → `ollama_chat/gemma4:12b`
- `ollama_chat/gemma4:12b` → unchanged
- `openai/gpt-4o` → unchanged

### Retry Flow

1. The agent runs normally via `Runner.run()` / `Runner.run_streamed()`.
2. If `ModelBehaviorError` is raised, the bad tool name is extracted.
3. A corrective user message is appended to the input.
4. The run is retried (up to `tool_call_retries` times).
5. If all retries are exhausted, the original error is re-raised.

### Streaming

`_RetryableStreamResult` wraps `RunResult.stream_events()`. If a
`ModelBehaviorError` occurs mid-stream, the wrapper re-creates the stream
with a corrective message and continues yielding events seamlessly.

## Files

| File | Description |
|---|---|
| `openkb/agent/ollama_adapter.py` | Adapter module (rewrite + retry + timeout + config) |
| `openkb/agent/query.py` | Uses adapter for Ollama models in query/chat |
| `openkb/agent/compiler.py` | Passes `api_base` from env for CLI path |
| `openkb/cli.py` | `rewrite_ollama_model` on all model resolutions |
| `openkb/add_coordinator.py` | Imports adapter for side effects |
| `tests/test_ollama_adapter.py` | 56 unit tests |
| `docs/ollama_tool_call_adapter.md` | This documentation |

## Testing

```bash
# Run adapter tests
python -m pytest tests/test_ollama_adapter.py -v  # 80 passed

# Run full test suite (non-Ollama path unchanged)
python -m pytest tests/ -q  # 1148 passed
```

## Minimum Model Requirements

OpenKB uses two LLM workloads with different requirements:

### 1. Ingestion (`add`) — low requirements

The `add` command compiles documents into wiki pages (summary, concepts,
entities). It uses direct LLM completion — **no tool-calling needed**.
Models ≥12B work reliably.

### 2. Query / Chat — higher requirements

The `query` and `chat` commands use a multi-step tool-calling loop: the
model must call `read_file` to read wiki pages, process the results, and
produce a grounded answer. This requires **instruction-following** and
**tool-use capability**, which smaller models may lack.

### Tested models

| Model | Size | `add` | `query` | Notes |
|---|---|---|---|---|
| qwen3.5 (cloud) | 397B | ✅ | ✅ grounded | Stable across all runs |
| gemma4 | 12B | ✅ | ⚠️ unstable | Grounded answer on some runs; "could not retrieve" on others |
| qwen2.5-coder | 14B | ✅ | ❌ | Model ignores nudge retries; sanitize fallback returns clear message |

### Recommendations

- **For `add` (ingestion):** any Ollama model ≥12B works. Tool-calling
  is not required.
- **For `query` / `chat`:** use models with strong instruction-following
  capability. General-purpose models (e.g. gemma4, qwen3) perform better
  than code-focused models (e.g. qwen2.5-coder) for tool-use tasks.
- **For slow hardware:** set `ollama.timeout: 600` or higher. Local 12B
  models can take 60-730s per request depending on GPU and model size.
- **Instability:** 12B models may produce different results between runs
  (grounded answer on one run, "could not retrieve" on the next). This is
  model-level variance, not an adapter issue. Consider fixing
  temperature/seed if reproducibility is needed.

## Live Test Results

Tested with Ollama 0.32.5, LiteLLM 1.94.0, openai-agents 0.19.1:

| Model | Test | Result |
|---|---|---|
| gemma4:12b | LiteLLM raw tool call | `read_file` with correct name, 14s |
| gemma4:12b | Full SDK tool-loop | `index.md` → `messages.md` → final answer, 132s |
| qwen3.5:397b (cloud) | Full tool-loop | Grounded answer with time/equipment details |
| qwen2.5-coder:14b | Full tool-loop | 3 nudge retries → sanitize fallback (clear message) |
| Non-Ollama path | Full test suite | 1148 passed, 0 regressions |

## Limitations

- Very small models (1B params) may still fail after retries — the
  adapter prevents crashes but cannot fix a model's fundamental inability
  to follow tool-use instructions.
- The `add` path makes multiple sequential LLM calls (summary +
  concepts); on very slow hardware the second call may time out. Raise
  `ollama.timeout` or `litellm.timeout` to accommodate.
- The adapter does not extract tool-call JSON from `content` (the
  `ollama_chat/` rewrite makes this unnecessary for models that support
  native tool calling).