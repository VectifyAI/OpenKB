"""Sync saved bindings into the knowledge base through ``add_single_file``."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from openkb.connectors.backend import ObjectBackend, ProviderError, RemoteObject, TransientError
from openkb.connectors.credentials import resolved_secrets, s3_credentials
from openkb.connectors.model import S3Binding
from openkb.connectors.s3 import S3Backend
from openkb.connectors.state import (
    SecretInStateError,
    fresh_entry,
    is_stale,
    read_state,
    update_state,
)
from openkb.connectors.store import load_bindings
from openkb.state import HashRegistry

RETRY_ATTEMPTS = 3

AddFn = Callable[..., str]
SignInFn = Callable[[], None]


@dataclass
class BindingReport:
    name: str
    provider: str
    ok: bool
    added: int = 0
    skipped: int = 0
    updated: int = 0
    failed: int = 0
    error: str | None = None
    stale: bool = False


@dataclass
class SyncOutcome:
    reports: list[BindingReport] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        for report in self.reports:
            if not report.ok or report.failed:
                return 1
        return 0


def recover_auth(*, non_interactive: bool, sign_in: SignInFn | None) -> None:
    """Run loopback sign-in, or fail the provider when that is not allowed.

    S3 does not call this. Drive and OneDrive will. ``--non-interactive`` never
    invokes ``sign_in``.
    """
    if non_interactive or sign_in is None:
        raise ProviderError(
            "Sign-in is required. Re-run without --non-interactive to open a browser."
        )
    sign_in()


def _extensions() -> set[str]:
    from openkb.cli import SUPPORTED_EXTENSIONS

    return SUPPORTED_EXTENSIONS


def _add_file(path: Path, kb_dir: Path, identity: str) -> str:
    from openkb.cli import add_single_file
    from openkb.locks import kb_ingest_lock_held

    if kb_ingest_lock_held(kb_dir / ".openkb"):
        raise RuntimeError("sync called add_single_file while holding the ingest lock")
    return add_single_file(path, kb_dir, identity=identity)


def _retry(action: Callable[[], object]) -> object:
    last: TransientError | None = None
    for _attempt in range(RETRY_ATTEMPTS):
        try:
            return action()
        except TransientError as exc:
            last = exc
    assert last is not None
    raise last


def _record(
    kb_dir: Path,
    binding: S3Binding,
    *,
    added: int,
    skipped: int,
    updated: int,
    failed: int,
    error: str | None,
    synced: bool,
) -> None:
    entry = fresh_entry(binding.provider, binding.location())
    entry["added"] = added
    entry["skipped"] = skipped
    entry["updated"] = updated
    entry["failed"] = failed
    entry["error"] = error
    if synced:
        entry["last_sync_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    secrets = resolved_secrets(kb_dir)
    try:
        update_state(kb_dir, binding.name, entry, secrets=secrets)
    except SecretInStateError:
        entry["error"] = "sync failed"
        update_state(kb_dir, binding.name, entry, secrets=secrets)


def _sync_one(
    kb_dir: Path,
    binding: S3Binding,
    backend: ObjectBackend,
    add: AddFn,
) -> BindingReport:
    added = skipped = updated = failed = 0
    error: str | None = None
    try:
        objects = _retry(backend.list_objects)
    except (TransientError, ProviderError) as exc:
        _record(
            kb_dir,
            binding,
            added=0,
            skipped=0,
            updated=0,
            failed=0,
            error=str(exc),
            synced=False,
        )
        return BindingReport(
            name=binding.name,
            provider=binding.provider,
            ok=False,
            error=str(exc),
        )
    assert isinstance(objects, list)
    registry = HashRegistry(kb_dir / ".openkb" / "hashes.json")
    allowed = _extensions()
    for obj in objects:
        if not isinstance(obj, RemoteObject):
            continue
        suffix = Path(obj.name).suffix.lower()
        if suffix not in allowed:
            skipped += 1
            continue
        prior = registry.get_by_path(obj.identity)
        try:
            with tempfile.TemporaryDirectory(prefix="openkb-src-") as tmp:
                dest = Path(tmp) / (Path(obj.name).name or "object")

                def download(obj: RemoteObject = obj, dest: Path = dest) -> None:
                    backend.download(obj, dest)

                _retry(download)
                status = add(dest, kb_dir, identity=obj.identity)
        except TransientError as exc:
            failed += 1
            error = str(exc)
            continue
        except Exception as exc:
            failed += 1
            error = str(exc)
            continue
        if status == "skipped":
            skipped += 1
        elif status == "failed":
            failed += 1
            error = f"Failed to compile {obj.name}."
        elif prior is not None:
            updated += 1
        else:
            added += 1
    ok = failed == 0
    _record(
        kb_dir,
        binding,
        added=added,
        skipped=skipped,
        updated=updated,
        failed=failed,
        error=error,
        synced=True,
    )
    return BindingReport(
        name=binding.name,
        provider=binding.provider,
        ok=ok,
        added=added,
        skipped=skipped,
        updated=updated,
        failed=failed,
        error=error,
    )


def sync_bindings(
    kb_dir: Path,
    *,
    name: str | None = None,
    non_interactive: bool = False,
    sign_in: SignInFn | None = None,
    add: AddFn | None = None,
    backend_for: Callable[[S3Binding, str, str], ObjectBackend] | None = None,
) -> SyncOutcome:
    """Sync every S3 binding, or the one named ``name``.

    ``sign_in`` is accepted so ``--non-interactive`` can be tested. S3 never
    calls it. ``add`` and ``backend_for`` exist so tests can avoid the network
    and the compiler.
    """
    # S3 has no browser sign-in. The flag stays on the command so Drive can
    # reuse it. A rejected token must not open a browser in non-interactive mode.
    if non_interactive:
        sign_in = None
    del sign_in
    bindings = load_bindings(kb_dir)
    if name is not None:
        bindings = [item for item in bindings if item.name == name]
        if not bindings:
            return SyncOutcome(
                reports=[
                    BindingReport(
                        name=name,
                        provider="s3",
                        ok=False,
                        error=f"No binding named {name!r}.",
                    )
                ]
            )
    creds = s3_credentials(kb_dir)
    reports: list[BindingReport] = []
    if bindings and creds is None:
        for binding in bindings:
            message = (
                "S3 credentials are missing. Set OPENKB_S3_ACCESS_KEY_ID and "
                "OPENKB_S3_SECRET_ACCESS_KEY in the environment or <kb>/.env."
            )
            _record(
                kb_dir,
                binding,
                added=0,
                skipped=0,
                updated=0,
                failed=0,
                error=message,
                synced=False,
            )
            reports.append(
                BindingReport(
                    name=binding.name,
                    provider="s3",
                    ok=False,
                    error=message,
                )
            )
        return SyncOutcome(reports=reports)
    assert creds is not None or not bindings
    add_fn = add or _add_file
    for binding in bindings:
        assert creds is not None
        access, secret = creds
        backend = (
            backend_for(binding, access, secret)
            if backend_for is not None
            else S3Backend(binding, access, secret)
        )
        reports.append(_sync_one(kb_dir, binding, backend, add_fn))
    return SyncOutcome(reports=reports)


def binding_views(kb_dir: Path) -> list[dict[str, object]]:
    """Bindings plus the last sync row, for the CLI and the Workbench."""
    state = read_state(kb_dir)
    views: list[dict[str, object]] = []
    for binding in load_bindings(kb_dir):
        entry = state.get(binding.name)
        row = entry if isinstance(entry, dict) else None
        views.append(
            {
                "name": binding.name,
                "provider": binding.provider,
                "location": binding.location(),
                "description": binding.describe(),
                "stale": is_stale(binding.provider, binding.location(), row),
                "last_sync_at": None if row is None else row.get("last_sync_at"),
                "added": 0 if row is None else int(row.get("added") or 0),
                "skipped": 0 if row is None else int(row.get("skipped") or 0),
                "updated": 0 if row is None else int(row.get("updated") or 0),
                "failed": 0 if row is None else int(row.get("failed") or 0),
                "error": None if row is None else row.get("error"),
            }
        )
    return views
