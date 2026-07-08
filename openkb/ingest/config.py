"""Configuration parsing for bundle ingest."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class IngestOptions:
    link_discovery: bool = False
    max_depth: int = 0
    max_documents: int = 50
    allow_domains: tuple[str, ...] = ()
    enabled_importers: tuple[str, ...] = ("file",)
    enabled_normalizers: tuple[str, ...] = ()
    enabled_enrichers: tuple[str, ...] = ()


def resolve_ingest_options(config: dict[str, Any]) -> IngestOptions:
    raw = config.get("ingest")
    ingest = raw if isinstance(raw, dict) else {}

    enabled_enrichers = _enabled_names(ingest.get("enrichers"), default=())
    link_discovery = bool(ingest.get("link_discovery", False)) or (
        "link_discovery" in enabled_enrichers
    )

    return IngestOptions(
        link_discovery=link_discovery,
        max_depth=_int_at_least(ingest.get("max_depth"), default=0, minimum=0),
        max_documents=_int_at_least(ingest.get("max_documents"), default=50, minimum=1),
        allow_domains=tuple(_string_list(ingest.get("allow_domains"))),
        enabled_importers=tuple(_enabled_names(ingest.get("importers"), default=("file",))),
        enabled_normalizers=tuple(_enabled_names(ingest.get("normalizers"), default=())),
        enabled_enrichers=tuple(enabled_enrichers),
    )


def _int_at_least(value: object, *, default: int, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, parsed)


def _enabled_names(value: object, *, default: tuple[str, ...]) -> list[str]:
    if value is None:
        return list(default)
    if not isinstance(value, dict):
        return list(default)
    raw = value.get("enabled")
    if raw is None:
        return list(default)
    return _string_list(raw)


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        cleaned = item.strip()
        if cleaned and cleaned not in names:
            names.append(cleaned)
    return names
