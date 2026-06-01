from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DEFAULT_CONFIG: dict[str, Any] = {
    "model": "gpt-5.4-mini",
    "language": "en",
    "pageindex_threshold": 20,
}

# Default entity-type vocabulary. Overridable per-KB via the optional
# ``entity_types:`` config key (see ``resolve_entity_types``).
DEFAULT_ENTITY_TYPES: tuple[str, ...] = (
    "person", "organization", "place", "product", "work", "event", "other",
)

GLOBAL_CONFIG_DIR = Path.home() / ".config" / "openkb"
GLOBAL_CONFIG_PATH = GLOBAL_CONFIG_DIR / "global.yaml"


def resolve_entity_types(config: dict) -> list[str]:
    """Resolve the effective entity-type list from a loaded config dict.

    If ``config["entity_types"]`` is a non-empty list, each string item is
    cleaned (lowercased, trimmed, restricted to ``[a-z0-9 _-]`` so a stray
    brace/punctuation can't leak into a prompt template or frontmatter value);
    non-string items (YAML nulls, numbers) are skipped. The cleaned list is
    de-duped (order preserving) and ``"other"`` is always appended when missing
    (it is the coercion fallback). Otherwise — key absent, not a list, empty,
    or fully malformed — :data:`DEFAULT_ENTITY_TYPES` is returned, so behavior
    is byte-identical to the default. A warning is logged only when
    ``entity_types`` was present-but-malformed.
    """
    raw = config.get("entity_types")
    if raw is None:
        return list(DEFAULT_ENTITY_TYPES)
    if not isinstance(raw, list):
        logger.warning(
            "config: 'entity_types' must be a list of strings, got %s — "
            "falling back to the default entity types.",
            type(raw).__name__,
        )
        return list(DEFAULT_ENTITY_TYPES)
    cleaned: list[str] = []
    for x in raw:
        if not isinstance(x, str):
            continue  # skip YAML nulls/numbers (str(None) would become "none")
        s = re.sub(r"[^a-z0-9 _-]+", "", x.strip().lower()).strip()
        if s and s not in cleaned:
            cleaned.append(s)
    if not cleaned:
        logger.warning(
            "config: 'entity_types' was present but yielded no usable values — "
            "falling back to the default entity types.",
        )
        return list(DEFAULT_ENTITY_TYPES)
    if "other" not in cleaned:
        cleaned.append("other")
    return cleaned


def load_config(config_path: Path) -> dict[str, Any]:
    """Load YAML config from config_path, merged with DEFAULT_CONFIG.

    If the file does not exist, returns a copy of the defaults.
    """
    config = dict(DEFAULT_CONFIG)
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        config.update(data)
    return config


def save_config(config_path: Path, config: dict) -> None:
    """Persist config dict to YAML, creating parent directories as needed."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(config, fh, allow_unicode=True, sort_keys=True)


def load_global_config() -> dict[str, Any]:
    """Load the global config from ~/.config/openkb/global.yaml."""
    if GLOBAL_CONFIG_PATH.exists():
        with GLOBAL_CONFIG_PATH.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    return {}


def save_global_config(config: dict[str, Any]) -> None:
    """Save the global config to ~/.config/openkb/global.yaml."""
    GLOBAL_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with GLOBAL_CONFIG_PATH.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(config, fh, allow_unicode=True, sort_keys=True)


def register_kb(kb_path: Path) -> None:
    """Register a KB path in the global config's known_kbs list."""
    gc = load_global_config()
    known = gc.get("known_kbs", [])
    resolved = str(kb_path.resolve())
    if resolved not in known:
        known.append(resolved)
        gc["known_kbs"] = known
    gc["default_kb"] = resolved
    save_global_config(gc)
