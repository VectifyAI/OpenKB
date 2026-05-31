from __future__ import annotations

from pathlib import Path
from typing import Any

from openkb.parsers.base import Parser
from openkb.parsers.local import LocalParser


def _make_mistral(opts, config):
    from openkb.parsers.mistral import MistralParser
    return MistralParser(opts)


def _make_vlm(opts, config):
    from openkb.parsers.vlm import VLMParser
    return VLMParser(opts, model=config.get("model"))


def _make_mineru(opts, config):
    from openkb.parsers.mineru import MineruParser
    return MineruParser(opts)


# Single source of truth: online-parser name -> lazy factory.
_ONLINE_PARSERS = {
    "mineru": _make_mineru,
    "mistral": _make_mistral,
    "vlm": _make_vlm,
}

# Valid parser names (drives the CLI --parser choice and error messages).
VALID_PARSERS = ("local", *_ONLINE_PARSERS)


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
    factory = _ONLINE_PARSERS.get(name)
    if factory is None:
        raise ValueError(
            f"Unknown parser {name!r}. Valid options: {', '.join(VALID_PARSERS)}."
        )
    opts = (config.get("parsers", {}) or {}).get(name, {}) or {}
    return factory(opts, config)
