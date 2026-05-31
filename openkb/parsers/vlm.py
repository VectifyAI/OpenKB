from __future__ import annotations

from pathlib import Path
from typing import Any

from openkb.parsers.base import ParseResult, Parser
from openkb.parsers.vlm_client import transcribe_to_markdown

_SUPPORTED = {".pdf"}


class VLMParser(Parser):
    """Parse via a vision-capable LLM (litellm). Covers Gemini, GPT-4o, Claude, etc."""

    name = "vlm"

    def __init__(self, opts: dict[str, Any] | None = None, model: str | None = None):
        opts = opts or {}
        # parsers.vlm.model overrides the global model; else use the global model.
        self.model = opts.get("model") or model

    def supports(self, suffix: str) -> bool:
        return suffix.lower() in _SUPPORTED

    def parse(self, src: Path) -> ParseResult:
        markdown = transcribe_to_markdown(src, model=self.model)
        return ParseResult(markdown=markdown)
