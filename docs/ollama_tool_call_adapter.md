# Ollama Tool-Call Retry Adapter

## Problem

When using Ollama models with OpenKB's `query` and `chat` commands, the
openai-agents SDK tool-calling loop frequently fails because small local
models hallucinate tool names instead of calling the registered tools.

For example, when the only registered tool is `read_file`, a model may
attempt to call `get_topics`, `search_strategy`, or `system` — none of
which exist. The SDK then raises:

```
ModelBehaviorError: Tool search_strategy not found in agent wiki-query
```

and the entire query aborts with no recovery.

This affects all Ollama models tested (issue #205):
| Model | Symptom |
|---|---|
| `llama3.1:8b` | raw tool-call JSON; no final answer |
| `qwen3:14b` | `{}` |
| `qwen3.5:9b` | empty output |
| `gemma4:12b` | timed out |
| `deepseek-r1:14b` | `{}` |
| `qwen2.5-coder:14b` | raw/incomplete response |
| `llama3.2:1b` | raw function-call JSON |

## Solution

OpenKB now includes a **retry adapter** (`openkb/agent/ollama_adapter.py`)
that intercepts `ModelBehaviorError` and retries the query with a
corrective system message:

```
[SYSTEM CORRECTION 1] You tried to call a tool named 'get_topics',
but that tool does not exist. The only available tools are:
read_file, get_page_content, get_image. Please answer the original
question using ONLY these tools. Do not invent tool names.
```

The retry adapter is **Ollama-only** — all other providers (OpenAI,
Anthropic, Gemini, etc.) keep the original bare-Runner behaviour
unchanged.

## How It Works

### Detection

`is_ollama_backend(model)` returns `True` for model strings matching:
- `ollama/llama3.2:1b` (LiteLLM prefix)
- `litellm/ollama/llama3.2:1b` (Agent-layer prefix used by OpenKB)

### Retry Flow

1. The agent runs normally via `Runner.run()` / `Runner.run_streamed()`.
2. If `ModelBehaviorError` is raised, the bad tool name is extracted
   from the error message.
3. A corrective user message is appended to the input.
4. The run is retried (up to `max_retries` times, default 3).
5. If all retries are exhausted, the original error is re-raised.

### Streaming

For streamed queries, `_RetryableStreamResult` wraps the `RunResult` and
intercepts `stream_events()`. If a `ModelBehaviorError` occurs mid-stream,
the wrapper re-creates the stream with a corrective message and continues
yielding events seamlessly.

## Configuration

The retry behaviour is automatic for Ollama models. No configuration is
required. The default retry count is 3.

Future config options (not yet implemented):

```yaml
ollama:
  tool_call_retries: 3    # max retry attempts (default 3)
  correct_tool_names: true # enable tool-name correction (default true)
```

## Files

| File | Description |
|---|---|
| `openkb/agent/ollama_adapter.py` | Retry adapter module |
| `openkb/agent/query.py` | Patched to use adapter for Ollama models |
| `tests/test_ollama_adapter.py` | 26 unit tests |

## Testing

```bash
# Run adapter tests
python -m pytest tests/test_ollama_adapter.py -v

# Run full test suite (non-Ollama path unchanged)
python -m pytest tests/ -q
```

## Limitations

- Very small models (e.g. `llama3.2:1b`, 1B params) may fail even after
  retries — they are simply too small to follow tool-use instructions
  reliably. The adapter recovers the crash but cannot fix the model's
  fundamental inability.
- The adapter does not modify the model's output format. If a model
  returns tool-call JSON in the `content` field instead of `tool_calls`,
  the SDK will still not execute the tool. A future enhancement could
  add content-to-tool-call extraction.
- Timeout handling is not modified. Models that time out will still
  time out; the retry adapter only handles `ModelBehaviorError`.