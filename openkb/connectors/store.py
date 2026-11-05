"""The ``connectors`` list in ``.openkb/config.yaml``.

This is desired state. Sync status lives in :mod:`openkb.connectors.state`.
Config and state are updated in one ingest-lock hold so a replace cannot
leave the previous location's counts on the new binding.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import portalocker
import yaml

from openkb.config import save_config
from openkb.connectors.model import (
    GDriveBinding,
    OneDriveBinding,
    S3Binding,
    parse_gdrive_binding,
    parse_onedrive_binding,
    parse_s3_binding,
)
from openkb.connectors.state import (
    BUSY_MESSAGE,
    LOCK_TIMEOUT_SECONDS,
    KbBusyError,
    read_state,
    state_path,
)
from openkb.locks import atomic_write_json, kb_lock
from openkb.state import HashRegistry


def config_path(kb_dir: Path) -> Path:
    return kb_dir / ".openkb" / "config.yaml"


def _read_raw(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("config.yaml must be a mapping.")
    return data


Binding = S3Binding | GDriveBinding | OneDriveBinding


def _parse_item(item: object) -> Binding | None:
    if not isinstance(item, dict):
        return None
    provider = item.get("provider")
    try:
        if provider == "s3":
            return parse_s3_binding(
                name=str(item.get("name", "")),
                region=str(item.get("region", "")),
                bucket=str(item.get("bucket", "")),
                prefix=str(item.get("prefix", "") or ""),
            )
        if provider == "gdrive":
            return parse_gdrive_binding(
                name=str(item.get("name", "")),
                folder=str(item.get("folder_id", "") or ""),
            )
        if provider == "onedrive":
            return parse_onedrive_binding(
                name=str(item.get("name", "")),
                path=str(item.get("path", "") or ""),
            )
    except ValueError:
        return None
    return None


def load_bindings(kb_dir: Path) -> list[Binding]:
    raw = _read_raw(config_path(kb_dir))
    items = raw.get("connectors") or []
    if not isinstance(items, list):
        return []
    bindings: list[Binding] = []
    for item in items:
        parsed = _parse_item(item)
        if parsed is not None:
            bindings.append(parsed)
    return bindings


def _count_remote_pages(kb_dir: Path, binding: Binding) -> int:
    registry = HashRegistry(kb_dir / ".openkb" / "hashes.json")
    needle = binding.page_prefix()
    count = 0
    for meta in registry.all_entries().values():
        path = meta.get("path")
        if isinstance(path, str) and path.startswith(needle):
            count += 1
    return count


def upsert_binding(kb_dir: Path, binding: Binding) -> str:
    """Insert or replace ``binding``. Returns ``created`` or ``replaced``.

    A replaced binding's sync status is cleared. Compiled pages stay.
    """
    message = "created"
    try:
        with kb_lock(kb_dir / ".openkb", exclusive=True, timeout=LOCK_TIMEOUT_SECONDS):
            path = config_path(kb_dir)
            raw = _read_raw(path)
            items = raw.get("connectors")
            if not isinstance(items, list):
                items = []
            kept: list[object] = []
            previous: Binding | None = None
            for item in items:
                parsed = _parse_item(item)
                if parsed is not None and parsed.name == binding.name:
                    previous = parsed
                    continue
                if isinstance(item, dict) and item.get("name") == binding.name:
                    previous = parsed
                    continue
                kept.append(item)
            kept.append({"name": binding.name, "provider": binding.provider, **binding.location()})
            raw["connectors"] = kept
            save_config(path, raw)
            if previous is not None:
                data = read_state(kb_dir)
                data.pop(binding.name, None)
                atomic_write_json(state_path(kb_dir), data)
                count = _count_remote_pages(kb_dir, previous)
                message = (
                    f"Binding `{binding.name}` now points to {binding.describe()}. "
                    f"{count} documents compiled from the previous location remain in the wiki."
                )
            else:
                message = f"Binding `{binding.name}` saved ({binding.describe()})."
    except TimeoutError as exc:
        raise KbBusyError(BUSY_MESSAGE) from exc
    except portalocker.exceptions.LockException as exc:
        raise KbBusyError(BUSY_MESSAGE) from exc
    return message


def remove_binding(kb_dir: Path, name: str) -> str:
    """Delete the binding record and its status. Wiki pages and ``raw/`` stay."""
    try:
        with kb_lock(kb_dir / ".openkb", exclusive=True, timeout=LOCK_TIMEOUT_SECONDS):
            path = config_path(kb_dir)
            raw = _read_raw(path)
            items = raw.get("connectors")
            if not isinstance(items, list):
                items = []
            kept = [
                item for item in items if not (isinstance(item, dict) and item.get("name") == name)
            ]
            if len(kept) == len(items):
                raise KeyError(name)
            raw["connectors"] = kept
            save_config(path, raw)
            data = read_state(kb_dir)
            data.pop(name, None)
            atomic_write_json(state_path(kb_dir), data)
    except TimeoutError as exc:
        raise KbBusyError(BUSY_MESSAGE) from exc
    except portalocker.exceptions.LockException as exc:
        raise KbBusyError(BUSY_MESSAGE) from exc
    return f"Removed binding `{name}`. Documents compiled from it remain in the wiki."
