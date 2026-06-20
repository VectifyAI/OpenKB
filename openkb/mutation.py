"""Transactional helpers for KB mutation paths."""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from openkb.locks import atomic_write_json

logger = logging.getLogger(__name__)


def _copy_file_atomic(src: Path, dest: Path) -> None:
    """Stream ``src`` to ``dest`` through a temp file, then atomically replace.

    Streams (never buffers the whole file) so publishing a large raw PDF
    does not spike peak memory. The temp-file + ``os.replace`` means a torn
    intermediate state can never be observed at ``dest``. Used by both
    publish and rollback, so every file copy in this module shares one
    atomic, streaming semantic.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{dest.name}.", suffix=".tmp", dir=dest.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as out, src.open("rb") as inp:
            shutil.copyfileobj(inp, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp_path, dest)
    finally:
        tmp_path.unlink(missing_ok=True)


@dataclass
class MutationSnapshot:
    """Snapshot of final KB paths touched by a mutation attempt."""

    kb_dir: Path
    backup_dir: Path
    journal_path: Path
    operation: str
    details: dict = field(default_factory=dict)
    entries: dict[Path, Path | None] = field(default_factory=dict)

    def _journal_data(self, status: str) -> dict:
        return {
            "version": 1,
            "operation": self.operation,
            "status": status,
            "kb_dir": str(self.kb_dir),
            "backup_dir": str(self.backup_dir),
            "details": self.details,
            "entries": [
                {
                    "target": str(target),
                    "backup": str(backup) if backup is not None else None,
                }
                for target, backup in self.entries.items()
            ],
        }

    def write_journal(self, status: str) -> None:
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.journal_path, self._journal_data(status))

    def mark_committed(self) -> None:
        """Mark the journal committed without removing the backup.

        Call this the instant the mutation is durably applied (e.g. the
        registry write has landed) so a subsequent
        :func:`recover_pending_journals` discards the journal instead of
        rolling it back. This is the commit signal; :meth:`discard` is the
        post-commit cleanup that also removes the backup dir and journal
        file and must itself be best-effort — it runs *after* the commit
        point and its failure must never trigger a rollback.
        """
        self.write_journal("committed")

    def rollback(self) -> None:
        # Restore children before parents so directory deletes cannot remove
        # paths that still need to be restored from a more specific backup.
        for target, backup in sorted(
            self.entries.items(),
            key=lambda item: len(item[0].parts),
            reverse=True,
        ):
            # Removal is unconditional; the backup (if any) is then restored
            # in its place.
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)
            if backup is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if backup.is_dir():
                shutil.copytree(backup, target)
            else:
                _copy_file_atomic(backup, target)
        self.write_journal("rolled_back")

    def rollback_best_effort(self) -> Exception | None:
        try:
            self.rollback()
        except Exception as exc:
            logger.warning("Mutation rollback failed: %s", exc)
            return exc
        return None

    def discard(self) -> None:
        # Best-effort post-commit/post-rollback cleanup: callers have already
        # written a terminal status (mark_committed or rollback), so there is
        # nothing to re-write here — doing so would be dead work and would
        # silently downgrade a "rolled_back" journal to "committed" moments
        # before it is deleted.
        shutil.rmtree(self.backup_dir, ignore_errors=True)
        self.journal_path.unlink(missing_ok=True)

    def discard_best_effort(self) -> Exception | None:
        try:
            self.discard()
        except Exception as exc:
            logger.warning("Mutation journal cleanup failed: %s", exc)
            return exc
        return None


def snapshot_paths(
    kb_dir: Path,
    paths: list[Path],
    *,
    operation: str,
    details: dict | None = None,
) -> MutationSnapshot:
    """Snapshot final KB paths before a mutation starts."""
    kb_dir = kb_dir.resolve()
    journal_id = uuid.uuid4().hex
    backup_dir = kb_dir / ".openkb" / "staging" / f"rollback-{journal_id}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    snapshot = MutationSnapshot(
        kb_dir=kb_dir,
        backup_dir=backup_dir,
        journal_path=kb_dir / ".openkb" / "journal" / f"{journal_id}.json",
        operation=operation,
        details=details or {},
    )
    try:
        for path in paths:
            target = path.resolve()
            if target in snapshot.entries:
                continue
            if not target.exists():
                snapshot.entries[target] = None
                continue
            rel = target.relative_to(kb_dir)
            backup = backup_dir / rel
            backup.parent.mkdir(parents=True, exist_ok=True)
            if target.is_dir():
                shutil.copytree(target, backup)
            else:
                _copy_file_atomic(target, backup)
            snapshot.entries[target] = backup
        # The active journal is the recovery signal: once this exists, a future
        # process can restore every recorded target even if the current one exits.
        snapshot.write_journal("active")
    except Exception:
        # Partial snapshot: backup_dir exists on disk but no journal was
        # written. recover_pending_journals only scans journals, so remove the
        # orphan backup here — otherwise it leaks forever with nothing able to
        # reach or clean it.
        shutil.rmtree(backup_dir, ignore_errors=True)
        raise
    return snapshot


def _snapshot_from_journal(path: Path, data: dict) -> MutationSnapshot:
    snapshot = MutationSnapshot(
        kb_dir=Path(data["kb_dir"]),
        backup_dir=Path(data["backup_dir"]),
        journal_path=path,
        operation=data.get("operation", "mutation"),
        details=data.get("details") or {},
    )
    snapshot.entries = {
        Path(item["target"]): Path(item["backup"]) if item.get("backup") else None
        for item in data.get("entries", [])
    }
    return snapshot


def recover_pending_journals(kb_dir: Path) -> list[str]:
    """Rollback active journals left by an interrupted process."""
    journal_dir = kb_dir / ".openkb" / "journal"
    if not journal_dir.is_dir():
        return []
    messages: list[str] = []
    for journal_path in sorted(journal_dir.glob("*.json")):
        try:
            data = json.loads(journal_path.read_text(encoding="utf-8"))
            snapshot = _snapshot_from_journal(journal_path, data)
            status = data.get("status", "active")
            if status in {"committed", "rolled_back"}:
                snapshot.discard()
                messages.append(f"Cleaned terminal mutation journal {journal_path.name}.")
                continue
            snapshot.rollback()
            snapshot.discard()
            messages.append(
                f"Rolled back interrupted {snapshot.operation} journal {journal_path.name}."
            )
        except Exception as exc:
            messages.append(
                f"Could not recover journal {journal_path.name}: {type(exc).__name__}: {exc}"
            )
    return messages


def publish_staged_tree(staging_dir: Path | None, kb_dir: Path) -> None:
    """Copy staged raw/source artifacts into their final KB locations."""
    if staging_dir is None or not staging_dir.exists():
        return
    for rel in ("raw", "wiki/sources"):
        src_root = staging_dir / rel
        if not src_root.exists():
            continue
        for src in src_root.rglob("*"):
            if not src.is_file():
                continue
            _copy_file_atomic(src, kb_dir / rel / src.relative_to(src_root))
