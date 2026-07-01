"""Transactional helpers for KB mutation paths."""

from __future__ import annotations

import errno
import json
import logging
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from openkb.locks import _fsync_directory, _target_mode, atomic_write_json

logger = logging.getLogger(__name__)

# Cap how many times recover_pending_journals retries an active journal whose
# rollback keeps failing. Without a cap, a deterministically-failing rollback
# (e.g. persistent ENOSPC) is retried on every lock acquisition forever,
# re-doing the failed work and never releasing the backup dir + journal.
MAX_ROLLBACK_ATTEMPTS = 5


def _apply_mode(path: Path, mode: int) -> None:
    """Set ``path``'s permission bits (no-op where ``os.chmod`` is absent)."""
    if hasattr(os, "chmod"):
        os.chmod(path, mode)


def _fsync_file(path: Path) -> None:
    """Best-effort fsync of a file's data, for durability after a rename.

    Opens read+write so ``FlushFileBuffers`` works on Windows (a read-only
    handle can be denied). Best-effort: a failure here only weakens durability
    of already-written bytes (the OS write-back still flushes them); it must
    not fail the publish.
    """
    try:
        with open(path, "r+b") as fh:
            os.fsync(fh.fileno())
    except OSError:
        pass


def _hardlink_or_copy(src: str, dst: str) -> None:
    """``copytree`` copy_function that hardlinks (O(1), shares the inode).

    Used for directory backups the caller has marked hardlink-safe — trees
    whose writers all go through atomic temp+replace (so the live file moves
    to a new inode) or that are append-only across documents. The hardlink
    backup then keeps pointing at the old inode while the live tree is
    mutated, so rollback restores the pre-mutation bytes without copying them
    up front. Falls back to a real copy on EXDEV/EPERM/EACCES — cross-device,
    a filesystem that forbids hardlinks, or (Windows) an ACL / cloud-sync
    folder (OneDrive/Dropbox) that blocks CREATE_HARD_LINK. If the copy also
    fails it surfaces the real error.
    """
    src_path = Path(src)
    dst_path = Path(dst)
    try:
        os.link(src_path, dst_path)
    except OSError as exc:
        if exc.errno not in (errno.EXDEV, errno.EPERM, errno.EACCES):
            raise
        shutil.copy2(src_path, dst_path)


def _copy_file_atomic(src: Path, dest: Path) -> None:
    """Stream ``src`` to ``dest`` through a temp file, then atomically replace.

    Streams (never buffers the whole file) so copying a large raw PDF does
    not spike peak memory. The temp-file + ``os.replace`` means a torn
    intermediate state can never be observed at ``dest``. Used by snapshot
    backup creation, rollback restore, and the cross-filesystem fallback of
    :func:`_publish_staged_file` — so every byte copy in this module shares
    one atomic, streaming, durable semantic: the parent directory is fsynced
    and the result carries the umask mode (not ``mkstemp``'s 0600).
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Capture the destination mode before the temp file shadows it: a brand-
    # new file gets the process umask mode (0o666 & ~umask), an existing file
    # keeps its current mode — the same rule ``atomic_write_bytes`` applies.
    mode = _target_mode(dest)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{dest.name}.", suffix=".tmp", dir=dest.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as out, src.open("rb") as inp:
            shutil.copyfileobj(inp, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp_path, dest)
        _apply_mode(dest, mode)
        _fsync_directory(dest.parent)
    finally:
        tmp_path.unlink(missing_ok=True)


def _publish_staged_file(src: Path, dest: Path) -> None:
    """Publish one staged file into its live-KB location.

    Staging sits on the same filesystem as ``raw/`` and ``wiki/sources/``, so
    an O(1) atomic ``os.replace`` (rename) is used instead of streaming the
    bytes — a full copy + fsync per published file was the old per-file cost.
    Only on ``EXDEV`` (staging and the live KB genuinely on different devices)
    does it fall back to :func:`_copy_file_atomic`. Both branches leave the
    result durable (file data + parent dir fsynced) and at the umask mode.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    mode = _target_mode(dest)
    try:
        os.replace(src, dest)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        _copy_file_atomic(src, dest)  # already fsyncs data + dir + sets mode
        return
    _apply_mode(dest, mode)
    # Parity with _copy_file_atomic: the renamed inode's data may still be in
    # the page cache. Without this, a crash right after publish can leave a
    # 0-byte / stale raw or source file that committed metadata points at,
    # even though the directory entry (fsynced below) survived.
    _fsync_file(dest)
    _fsync_directory(dest.parent)


@dataclass
class MutationSnapshot:
    """Snapshot of final KB paths touched by a mutation attempt."""

    kb_dir: Path
    backup_dir: Path
    journal_path: Path
    operation: str
    details: dict = field(default_factory=dict)
    entries: dict[Path, Path | None] = field(default_factory=dict)
    attempts: int = 0
    # Dirs whose backup was hardlinked (in-process only; not persisted, so a
    # crash-rebuilt snapshot leaves this empty and rollback falls back to the
    # safe full-copy path). Drives O(touched) rollback via inode-diff restore.
    hardlinked_dirs: set[Path] = field(default_factory=set)

    def _journal_data(self, status: str) -> dict:
        return {
            "version": 1,
            "operation": self.operation,
            "status": status,
            "kb_dir": str(self.kb_dir),
            "backup_dir": str(self.backup_dir),
            "details": self.details,
            "attempts": self.attempts,
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

    def track_new(self, paths: list[Path]) -> None:
        """Register paths created *after* the snapshot for removal on rollback.

        Some artifacts get their final name only once the mutation runs — the
        PageIndex ``{doc_id}`` blob under ``.openkb/files`` is named by indexing,
        which happens after :func:`snapshot_paths`. Rather than eagerly
        snapshotting the whole append-only blob store up front (an ``os.link``
        per existing blob on *every* add — O(total blobs), not O(this doc)),
        the caller invokes this once the new artifacts exist. Each is recorded
        with no backup, so both :meth:`rollback` and a crash-recovery replay
        delete exactly the new paths and nothing else. The active journal is
        rewritten so a crash after the artifacts land but before commit still
        cleans them up. Paths already tracked are ignored; missing paths are a
        no-op (nothing was created).
        """
        changed = False
        for path in paths:
            target = path.resolve()
            if target not in self.entries:
                self.entries[target] = None
                changed = True
        if changed:
            self.write_journal("active")

    def rollback(self) -> None:
        # Restore children before parents so directory deletes cannot remove
        # paths that still need to be restored from a more specific backup.
        for target, backup in sorted(
            self.entries.items(),
            key=lambda item: len(item[0].parts),
            reverse=True,
        ):
            # A hardlinked dir backup supports an O(touched) inode-diff restore
            # (leave untouched shared-inode files, only touch changed ones) —
            # do NOT rmtree it first, which would discard those shared inodes.
            if target.is_dir() and target in self.hardlinked_dirs:
                if backup is not None and backup.is_dir():
                    _restore_hardlinked_dir(backup, target)
                else:
                    shutil.rmtree(target, ignore_errors=True)  # new dir, no backup
                continue
            # Non-hardlinked (file, or copied dir): unconditional remove + restore.
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


def _restore_hardlinked_dir(backup: Path, target: Path) -> None:
    """O(touched) restore for a hardlinked directory backup.

    The backup was built with ``os.link``, so live files the mutation never
    touched still share the backup's inode — leave them. Only files the
    mutation changed need work: new files (no backup counterpart) are removed,
    modified files (atomic temp+replace → new inode) and deleted files are
    restored from the backup's pre-mutation bytes. This avoids recopying the
    whole tree on rollback — the cost that bit ``.openkb/files`` (the blob
    store) and large concept/entity trees on every failed add.

    Degrades gracefully to a full copy if the backup isn't actually hardlinked
    (e.g. the EXDEV/EACCES fallback fired at snapshot time): every file then has
    a different inode, so every file is treated as modified and recopied.
    """

    def _file_key(path: Path) -> tuple[int, int]:
        st = path.stat()  # follows symlinks; these trees hold regular files only
        return (st.st_dev, st.st_ino)

    backup_files = {p.relative_to(backup): p for p in backup.rglob("*") if p.is_file()}

    # Pass 1: remove new + modified live regular files; leave untouched ones
    # (they share the backup inode) in place.
    if target.exists():
        for live in list(target.rglob("*")):
            if not live.is_file():
                continue
            counterpart = backup_files.get(live.relative_to(target))
            if counterpart is None or _file_key(live) != _file_key(counterpart):
                live.unlink()

    # Pass 2: restore modified + deleted files from backup.
    for rel, src in backup_files.items():
        dest = target / rel
        if not dest.exists() or _file_key(dest) != _file_key(src):
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)

    # Pass 3: prune directories the mutation created that are now empty.
    if target.exists():
        for d in sorted(
            (p for p in target.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True
        ):
            if not (backup / d.relative_to(target)).exists() and not any(d.iterdir()):
                d.rmdir()


def snapshot_paths(
    kb_dir: Path,
    paths: list[Path],
    *,
    operation: str,
    details: dict | None = None,
    hardlink_dirs: set[Path] | None = None,
) -> MutationSnapshot:
    """Snapshot final KB paths before a mutation starts.

    ``hardlink_dirs`` marks directories whose backup may be hardlinks instead
    of copies (O(1), no per-file byte copy). A directory is only safe to list
    here if every writer into it is either atomic temp+replace (new inode, so
    the hardlink backup keeps the old bytes) or append-only. This is the
    required caller contract for hardlinked dirs; any in-place writer into one
    of those trees would silently corrupt the backup and make rollback a no-op
    for that file.
    """
    kb_dir = kb_dir.resolve()
    hardlink_resolved = {p.resolve() for p in (hardlink_dirs or ())}
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
                if target in hardlink_resolved:
                    shutil.copytree(target, backup, copy_function=_hardlink_or_copy)
                    snapshot.hardlinked_dirs.add(target)
                else:
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
    snapshot.attempts = int(data.get("attempts", 0) or 0)
    return snapshot


def recover_pending_journals(kb_dir: Path) -> list[str]:
    """Rollback active journals left by an interrupted process."""
    journal_dir = kb_dir / ".openkb" / "journal"
    if not journal_dir.is_dir():
        return []
    messages: list[str] = []
    for journal_path in sorted(journal_dir.glob("*.json")):
        snapshot: MutationSnapshot | None = None
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
            if snapshot is None:
                # The journal couldn't be read or reconstructed (corrupt/empty/
                # stray .json, or missing the kb_dir/backup_dir keys recovery
                # needs). There is nothing to roll back or retry — and leaving
                # it in place would re-trigger this failure on every future lock
                # acquisition (draining runs on first exclusive acquisition),
                # bricking add/remove/recompile/chat for the whole KB. Best-effort
                # remove the unrecoverable journal and log loudly; any backup_dir
                # it referenced is unreachable now and may leak.
                journal_path.unlink(missing_ok=True)
                messages.append(
                    f"Unrecoverable mutation journal {journal_path.name} "
                    f"({type(exc).__name__}: {exc}); removed so it can't block "
                    f"recovery. The KB may need manual review."
                )
                continue
            # Rollback failed. Retry a bounded number of times across recovery
            # runs (a later attempt may succeed once the cause clears, e.g. disk
            # space freed), then give up: discard the journal + backup and log
            # loudly so it can't leak forever re-doing the same failing rollback.
            snapshot.attempts += 1
            if snapshot.attempts >= MAX_ROLLBACK_ATTEMPTS:
                snapshot.discard()
                messages.append(
                    f"GAVE UP on {snapshot.operation} journal {journal_path.name} after "
                    f"{snapshot.attempts} failed rollback(s): {type(exc).__name__}: {exc}. "
                    f"The KB may be in a partially-rolled-back state — manual review needed."
                )
            else:
                snapshot.write_journal("active")  # persist incremented attempts
                messages.append(
                    f"Rollback of {snapshot.operation} journal {journal_path.name} failed "
                    f"(attempt {snapshot.attempts}/{MAX_ROLLBACK_ATTEMPTS}): "
                    f"{type(exc).__name__}: {exc}; retained for retry."
                )
    return messages


def publish_staged_tree(staging_dir: Path | None, kb_dir: Path) -> None:
    """Move staged raw/source artifacts into their final KB locations."""
    if staging_dir is None or not staging_dir.exists():
        return
    for rel in ("raw", "wiki/sources"):
        src_root = staging_dir / rel
        if not src_root.exists():
            continue
        for src in src_root.rglob("*"):
            if not src.is_file():
                continue
            _publish_staged_file(src, kb_dir / rel / src.relative_to(src_root))
