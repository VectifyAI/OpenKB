from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG: dict[str, Any] = {
    "model": "gpt-4o",
    "api_key_env": "OPENAI_API_KEY",
    "language": "en",
    "pageindex_threshold": 50,
}


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
