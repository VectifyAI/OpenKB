from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

import litellm

_DEFAULT_MODEL = "gemini/gemini-2.5-pro"

_PROMPT = (
    "Transcribe this document to clean GitHub-flavored Markdown. Preserve headings, "
    "lists, tables (as Markdown or HTML tables), and math (as LaTeX). Output only the "
    "Markdown content, no commentary."
)


def transcribe_to_markdown(src: Path, model: str | None = None, prompt: str | None = None) -> str:
    """Send ``src`` (PDF or image) to a vision-capable LLM via litellm; return Markdown."""
    model = model or _DEFAULT_MODEL
    mime = mimetypes.guess_type(src.name)[0] or "application/octet-stream"
    b64 = base64.b64encode(src.read_bytes()).decode()
    data_uri = f"data:{mime};base64,{b64}"
    content = [
        {"type": "text", "text": prompt or _PROMPT},
        {"type": "image_url", "image_url": {"url": data_uri}},
    ]
    resp = litellm.completion(model=model, messages=[{"role": "user", "content": content}])
    return resp.choices[0].message.content or ""
