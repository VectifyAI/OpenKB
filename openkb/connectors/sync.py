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
from openkb.connectors.store import Binding, load_bindings
from openkb.state import HashRegistry

RETRY_ATTEMPTS = 3

AddFn = Callable[..., str]
SignInFn = Callable[[], object]


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


def recover_auth(*, non_interactive: bool, sign_in: SignInFn | None) -> object:
    """Run loopback sign-in, or fail the provider when that is not allowed.

    S3 does not call this. Drive does, when a refresh token is missing or
    rejected. ``--non-interactive`` never invokes ``sign_in``.
    """
    if non_interactive or sign_in is None:
        raise ProviderError(
            "Sign-in is required. Re-run without --non-interactive to open a browser."
        )
    return sign_in()


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
    binding: Binding,
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
    binding: Binding,
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


def _fail_group(kb_dir: Path, bindings: list[Binding], message: str) -> list[BindingReport]:
    reports: list[BindingReport] = []
    for binding in bindings:
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
                provider=binding.provider,
                ok=False,
                error=message,
            )
        )
    return reports


def _sync_s3_group(
    kb_dir: Path,
    bindings: list[S3Binding],
    *,
    add: AddFn | None,
    backend_for: Callable[[S3Binding, str, str], ObjectBackend] | None,
) -> list[BindingReport]:
    creds = s3_credentials(kb_dir)
    if creds is None:
        return _fail_group(
            kb_dir,
            list(bindings),
            "S3 credentials are missing. Set OPENKB_S3_ACCESS_KEY_ID and "
            "OPENKB_S3_SECRET_ACCESS_KEY in the environment or <kb>/.env.",
        )
    access, secret = creds
    add_fn = add or _add_file
    reports: list[BindingReport] = []
    for binding in bindings:
        backend = (
            backend_for(binding, access, secret)
            if backend_for is not None
            else S3Backend(binding, access, secret)
        )
        reports.append(_sync_one(kb_dir, binding, backend, add_fn))
    return reports


def _sync_gdrive_group(
    kb_dir: Path,
    bindings: list[Binding],
    *,
    non_interactive: bool,
    sign_in: SignInFn | None,
    announce: Callable[[str], None] | None,
    add: AddFn | None,
    backend_for: Callable[[Binding, str], ObjectBackend] | None,
) -> list[BindingReport]:
    from openkb.connectors.gdrive import GDriveBackend, access_token_for
    from openkb.connectors.model import GDriveBinding

    try:
        token = access_token_for(
            kb_dir,
            non_interactive=non_interactive,
            sign_in_hook=sign_in if sign_in is not None else None,
            announce=announce,
        )
    except ProviderError as exc:
        return _fail_group(kb_dir, bindings, str(exc))
    add_fn = add or _add_file
    reports: list[BindingReport] = []
    for binding in bindings:
        if not isinstance(binding, GDriveBinding):
            continue
        backend = (
            backend_for(binding, token)
            if backend_for is not None
            else GDriveBackend(binding, token)
        )
        reports.append(_sync_one(kb_dir, binding, backend, add_fn))
    return reports


def _grouped(bindings: list[Binding]) -> list[tuple[str, list[Binding]]]:
    order: list[str] = []
    groups: dict[str, list[Binding]] = {}
    for binding in bindings:
        if binding.provider not in groups:
            order.append(binding.provider)
            groups[binding.provider] = []
        groups[binding.provider].append(binding)
    return [(provider, groups[provider]) for provider in order]


def sync_bindings(
    kb_dir: Path,
    *,
    name: str | None = None,
    non_interactive: bool = False,
    sign_in: SignInFn | None = None,
    announce: Callable[[str], None] | None = None,
    add: AddFn | None = None,
    backend_for: Callable[[S3Binding, str, str], ObjectBackend] | None = None,
    gdrive_backend_for: Callable[[Binding, str], ObjectBackend] | None = None,
) -> SyncOutcome:
    """Sync every binding, or the one named ``name``.

    ``sign_in`` and ``announce`` are for Drive. ``--non-interactive`` drops
    both, so a missing or rejected token fails that provider and no browser
    opens. ``add`` and the backend hooks exist so tests skip the network and
    the compiler. S3 never calls ``sign_in``.
    """
    if non_interactive:
        sign_in = None
        announce = None
    bindings = load_bindings(kb_dir)
    if name is not None:
        bindings = [item for item in bindings if item.name == name]
        if not bindings:
            return SyncOutcome(
                reports=[
                    BindingReport(
                        name=name,
                        provider="",
                        ok=False,
                        error=f"No binding named {name!r}.",
                    )
                ]
            )
    reports: list[BindingReport] = []
    for provider, group in _grouped(bindings):
        if provider == "s3":
            s3_group = [item for item in group if isinstance(item, S3Binding)]
            reports.extend(_sync_s3_group(kb_dir, s3_group, add=add, backend_for=backend_for))
        elif provider == "gdrive":
            reports.extend(
                _sync_gdrive_group(
                    kb_dir,
                    group,
                    non_interactive=non_interactive,
                    sign_in=sign_in,
                    announce=announce,
                    add=add,
                    backend_for=gdrive_backend_for,
                )
            )
        else:
            reports.extend(_fail_group(kb_dir, group, f"Provider {provider} is not available."))
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
