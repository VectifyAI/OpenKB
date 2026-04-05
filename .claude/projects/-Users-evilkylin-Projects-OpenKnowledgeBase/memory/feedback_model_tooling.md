---
name: Feedback - Model and Tooling
description: Use gpt-5.4 as default model, uv for Python project management
type: feedback
---

Use gpt-5.4 as the default LLM model (not gpt-4o).
**Why:** User explicitly requested it, has OpenAI API key in .env.
**How to apply:** Set DEFAULT_CONFIG model to gpt-5.4, use gpt-5.4 in all LLM calls.

Use uv (not pip/venv) for Python project management.
**Why:** User preference for modern tooling.
**How to apply:** Use `uv` for venv creation, dependency installation, running scripts.
