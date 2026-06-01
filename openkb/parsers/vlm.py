from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from openkb.parsers.base import ParseResult, Parser
from openkb.parsers.vlm_client import transcribe_to_markdown

logger = logging.getLogger(__name__)

_SUPPORTED = {".pdf"}


class VLMParser(Parser):
    """Parse via a vision-capable LLM (litellm). Covers Gemini, GPT-4o, Claude, etc."""

    name = "vlm"

    def __init__(self, opts: dict[str, Any] | None = None, model: str | None = None):
        opts = opts or {}
        # parsers.vlm.model overrides the global model; else use the global model.
        self.model = opts.get("model") or model
        if not opts.get("model"):
            logger.warning(
                "VLM parser: 'parsers.vlm.model' is not set; using the global model "
                "%r for vision parsing. If that model is not vision-capable, set "
                "'parsers.vlm.model' to one (e.g. gemini/gemini-2.5-pro).",
                self.model,
            )

    def supports(self, suffix: str) -> bool:
        return suffix.lower() in _SUPPORTED

    def parse(self, src: Path) -> ParseResult:
        markdown = transcribe_to_markdown(src, model=self.model)
        logger.warning(
            "VLM parser transcribes %s to text only; embedded figures/images are "
            "not extracted. Use a parser like 'mineru' if you need figure extraction.",
            src.name,
        )
        return ParseResult(markdown=markdown)
