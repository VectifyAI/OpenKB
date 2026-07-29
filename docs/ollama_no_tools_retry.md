# No-Tools-Called Retry

## Problem

When using local Ollama models (12-14B) with OpenKB's `query` and `chat`
commands, the model may return a final answer **without calling any tools**.
The answer is typically "I cannot find relevant information" or empty —
the model didn't attempt to read wiki files, even though tools are
available and the `add` (ingestion) path works correctly.

This is a **model capability limitation**, not a transport bug — the
adapter from PR #210 correctly rewrites `ollama/` → `ollama_chat/` and
handles `ModelBehaviorError`, but it cannot force a model to *initiate*
tool calls. The model simply answers from its own knowledge (or says "not
found") without reading the wiki.

## Solution

The adapter now includes a **no-tools-called retry** mechanism. After
`Runner.run()` completes successfully, the adapter checks whether any
`ToolCallItem` exists in `result.new_items`. If the agent has tools but
none were called, the adapter retries with an escalating **nudge message**:

### Attempt 1 (gentle)
```
You answered without reading any wiki files. You have tools available.
Call read_file with path 'summaries/index.md' first to see what
documents exist, then answer the question.
```

### Attempt 2 (firm)
```
You MUST use the read_file tool. Do NOT answer without reading files
first. Start with read_file(path='summaries/index.md').
Available tools: read_file, get_page_content.
```

### Attempt 3 (final)
```
IMPORTANT: Your previous answer was not grounded in the wiki.
Call read_file now. The available tools are: read_file, get_page_content.
Do not answer until you have called a tool.
```

If all retries are exhausted, the adapter returns the last (ungrounded)
answer — it does not loop infinitely.

## How It Works

### Detection

`_has_tool_calls(result)` checks `result.new_items` for any instance of
`ToolCallItem` (from `agents.items`). If none found and the agent has
registered tools, the no-tools retry is triggered.

### Retry Flow

1. `Runner.run()` / `Runner.run_streamed()` completes successfully.
2. Adapter checks: did the model call any tools?
3. If yes → return result (normal path, no retry).
4. If no → append nudge message, retry.
5. Repeat up to `tool_call_retries` times (default 3, configurable).
6. If exhausted → return last result with a warning log.

### Interaction with ModelBehaviorError retry

The no-tools retry and the `ModelBehaviorError` retry share the same
`max_retries` budget. A run that first hallucinates a tool name (triggering
a `ModelBehaviorError` retry) and then answers without tools (triggering
a no-tools retry) consumes two retry attempts from the same pool.

### Configuration

Uses the same `ollama:` config block as PR #210:

```yaml
ollama:
  timeout: 300              # per-request timeout (s), default 300
  tool_call_retries: 3      # max retries (hallucination + no-tools), default 3
```

## Files

| File | Description |
|---|---|
| `openkb/agent/ollama_adapter.py` | Added `_has_tool_calls()`, `_build_nudge_message()`, `_append_nudge()`, and no-tools retry loop in `arun_with_retry()` / `run_with_retry()` |
| `tests/test_ollama_adapter.py` | 17 new tests (73 total, all passing) |

## Testing

```bash
python -m pytest tests/test_ollama_adapter.py -v  # 73 passed
python -m pytest tests/ -q                          # 1148 passed
```

## Sanitization of Ungrounded Output

When all retries are exhausted and the model never called any tools, the
adapter sanitizes the output before returning it to the user:

- **Raw JSON** (e.g. `{"name": "read_file", ...}`) → replaced with a clear
  message: *"I could not find relevant information in the knowledge base.
  The available tools were not used successfully. Try rephrasing your
  question or adding more documents."*
- **Empty output** (`""`) → same message
- **Reasonable text** (e.g. "I cannot find relevant information") →
  returned as-is (it's a real answer, not raw JSON)

This prevents users from seeing raw tool-call JSON in the query output.

## Minimum Model Requirements

Based on live testing with tg-collector:

| Model | Size | add (ingestion) | query (tool-loop) |
|---|---|---|---|
| qwen3.5 (cloud) | 397B | ✅ | ✅ grounded answer |
| gemma4 | 12B | ✅ | ✅ grounded answer (with nudge retry) |
| qwen2.5-coder | 14B | ✅ | ❌ model ignores nudges, cannot tool-loop |

**Recommendation**: for `query` and `chat` tool-loop, use ≥12B models with
good instruction-following capability. `qwen2.5-coder:14b` (code-focused)
struggles with tool-use despite the nudge retries. For `add` (ingestion),
12-14B models work fine — tool-calling is not required.

## Limitations

- The nudge cannot **force** a model to call tools — it can only
  encourage. Very small models (1B) may ignore the nudge entirely.
- Models that produce empty output (`""`) may not benefit from the nudge
  if the empty output is caused by a timeout or inference failure rather
  than a decision not to call tools.
- The retry budget is shared with `ModelBehaviorError` retries. If a model
  both hallucinates tool names AND answers without tools, the retries may
  be consumed by the hallucination loop before the no-tools check runs.