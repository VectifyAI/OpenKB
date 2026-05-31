from __future__ import annotations

from pathlib import Path
from typing import Any

from openkb.parsers.base import Parser
from openkb.parsers.local import LocalParser

VALID_PARSERS = ("local", "mineru", "mistral", "vlm")


def get_parser(
    config: dict[str, Any],
    override: str | None = None,
    *,
    doc_name: str = "",
    images_dir: Path | None = None,
    source_dir: Path | None = None,
) -> Parser:
    """Resolve the configured parser. ``override`` (e.g. CLI ``--parser``) wins."""
    name = (override or config.get("parser") or "local").lower()
    if name == "local":
        return LocalParser(doc_name=doc_name, images_dir=images_dir, source_dir=source_dir)

    parsers_cfg = config.get("parsers", {}) or {}
    opts = parsers_cfg.get(name, {}) or {}
    if name == "mistral":
        from openkb.parsers.mistral import MistralParser
        return MistralParser(opts)
    if name == "vlm":
        from openkb.parsers.vlm import VLMParser
        return VLMParser(opts, model=config.get("model"))
    if name == "mineru":
        from openkb.parsers.mineru import MineruParser
        return MineruParser(opts)
    raise ValueError(
        f"Unknown parser {name!r}. Valid options: {', '.join(VALID_PARSERS)}."
    )
