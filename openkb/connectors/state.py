"""Derived sync status in ``.openkb/connector-state.json``.

The file is not the wiki. Writes go through :func:`atomic_write_json` while
the exclusive ingest lock is held, and they do not open a mutation journal
of their own (taking the lock does run the existing journal recovery).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import portalocker

from openkb.locks import atomic_write_json, kb_lock

LOCK_TIMEOUT_SECONDS = 30
BUSY_MESSAGE = (
    "knowledge base is busy (another `openkb add` or sync is running); retry when it finishes"
)

_COUNT_KEYS = ("added", "skipped", "updated", "failed")


class SecretInStateError(ValueError):
    """Refused to persist a state document that contains a credential."""


class KbBusyError(TimeoutError):
    """The ingest lock was not free within the connector wait."""


def state_path(kb_dir: Path) -> Path:
    return kb_dir / ".openkb" / "connector-state.json"


def read_state(kb_dir: Path) -> dict[str, Any]:
    path = state_path(kb_dir)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def is_stale(provider: str, location: dict[str, str], entry: dict[str, Any] | None) -> bool:
    """True when the saved result describes a different binding than ``location``."""
    if not entry or not entry.get("last_sync_at"):
        return True
    return entry.get("provider") != provider or entry.get("location") != location


def _reject_secrets(document: dict[str, Any], secrets: list[str]) -> None:
    payload = json.dumps(document)
    for secret in secrets:
        if secret and secret in payload:
            raise SecretInStateError("refusing to write connector state that contains a credential")


def update_state(
    kb_dir: Path,
    name: str,
    entry: dict[str, Any] | None,
    *,
    secrets: list[str] | None = None,
    timeout: float | None = None,
) -> None:
    """Insert, replace, or delete one binding's status under the ingest lock.

    ``entry is None`` deletes the key. ``timeout`` defaults to 30 seconds.
    """

    def mutate(data: dict[str, Any]) -> None:
        if entry is None:
            data.pop(name, None)
        else:
            data[name] = entry

    _mutate(kb_dir, mutate, secrets=secrets or [], timeout=timeout)


def _mutate(
    kb_dir: Path,
    mutate,
    *,
    secrets: list[str],
    timeout: float | None,
) -> None:
    wait = LOCK_TIMEOUT_SECONDS if timeout is None else timeout
    try:
        with kb_lock(kb_dir / ".openkb", exclusive=True, timeout=wait):
            data = read_state(kb_dir)
            mutate(data)
            _reject_secrets(data, secrets)
            atomic_write_json(state_path(kb_dir), data)
    except TimeoutError as exc:
        raise KbBusyError(BUSY_MESSAGE) from exc
    except portalocker.exceptions.LockException as exc:
        raise KbBusyError(BUSY_MESSAGE) from exc


def fresh_entry(provider: str, location: dict[str, str]) -> dict[str, Any]:
    """A status row with zero counts, used when a sync produced no file results."""
    entry: dict[str, Any] = {
        "provider": provider,
        "location": location,
        "last_sync_at": None,
        "error": None,
    }
    for key in _COUNT_KEYS:
        entry[key] = 0
    return entry
