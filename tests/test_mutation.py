from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from openkb.mutation import publish_staged_tree, recover_pending_journals, snapshot_paths


def test_recover_pending_add_journal_rolls_back_files(tmp_path):
    kb_dir = tmp_path
    openkb_dir = kb_dir / ".openkb"
    openkb_dir.mkdir()
    target = kb_dir / "wiki" / "summaries" / "doc.md"
    target.parent.mkdir(parents=True)
    target.write_text("before", encoding="utf-8")
    new_file = kb_dir / "wiki" / "sources" / "doc.md"

    snapshot_paths(
        kb_dir,
        [target, new_file],
        operation="add",
        details={"doc_name": "doc"},
    )
    target.write_text("after", encoding="utf-8")
    new_file.parent.mkdir(parents=True)
    new_file.write_text("new", encoding="utf-8")

    messages = recover_pending_journals(kb_dir)

    assert any("Rolled back interrupted add journal" in message for message in messages)
    assert target.read_text(encoding="utf-8") == "before"
    assert not new_file.exists()
    assert not any((openkb_dir / "journal").glob("*.json"))


def test_mark_committed_prevents_recovery_rollback(tmp_path):
    """A snapshot marked committed must be discarded (not rolled back) by
    recovery — the commit signal that protects a completed mutation from
    being undone when post-commit cleanup fails.
    """
    kb_dir = tmp_path
    openkb_dir = kb_dir / ".openkb"
    openkb_dir.mkdir()
    target = kb_dir / "wiki" / "summaries" / "doc.md"
    target.parent.mkdir(parents=True)
    target.write_text("before", encoding="utf-8")

    snapshot = snapshot_paths(
        kb_dir, [target], operation="add", details={"doc_name": "doc"}
    )
    target.write_text("after", encoding="utf-8")  # the "committed" mutation
    snapshot.mark_committed()

    messages = recover_pending_journals(kb_dir)

    assert any("Cleaned terminal mutation journal" in m for m in messages)
    assert target.read_text(encoding="utf-8") == "after"  # NOT rolled back
    assert not any((openkb_dir / "journal").glob("*.json"))


def test_snapshot_paths_cleans_backup_dir_on_failure(tmp_path):
    """A partially-created snapshot must not leak its backup dir: on any
    failure before the journal is written, snapshot_paths removes the
    rollback dir it created (recover_pending_journals only scans journals
    and could never reach it otherwise).
    """
    kb_dir = tmp_path / "kb"
    kb_dir.mkdir()
    # A target that resolves OUTSIDE kb_dir makes relative_to(kb_dir) raise
    # mid-loop, after backup_dir was already mkdir'd.
    outside = tmp_path / "outside.txt"
    outside.write_text("hi", encoding="utf-8")

    with pytest.raises(ValueError):
        snapshot_paths(kb_dir, [outside], operation="add", details={})

    staging = kb_dir / ".openkb" / "staging"
    if staging.exists():
        assert not any(staging.iterdir())  # no orphan rollback-<uuid> dir


def test_exclusive_lock_drains_active_journal_before_yielding(tmp_path):
    """Recovery runs on every exclusive-lock acquisition, not just the add path.

    ``recover_pending_journals`` is wired into ``kb_lock``'s first exclusive
    acquisition, so any mutation command — ``remove``/``recompile``/``lint``/
    ``chat``, all of which take ``kb_ingest_lock`` directly — drains a crashed
    predecessor's active journal before it mutates. This is the regression
    guard for the bug where an ``add`` crash left an active journal that an
    intervening ``remove`` ignored and a later ``add`` then rolled back over
    the remove's edits.
    """
    from openkb.locks import kb_ingest_lock

    kb_dir = tmp_path
    openkb_dir = kb_dir / ".openkb"
    openkb_dir.mkdir()
    target = kb_dir / "wiki" / "summaries" / "doc.md"
    target.parent.mkdir(parents=True)
    target.write_text("before", encoding="utf-8")

    # Simulate a crashed add: snapshot taken, file mutated, but mark_committed
    # never ran — an ACTIVE journal is left on disk.
    snapshot_paths(kb_dir, [target], operation="add", details={"doc_name": "doc"})
    target.write_text("after", encoding="utf-8")

    # Any exclusive-lock holder drains before its body runs.
    with kb_ingest_lock(openkb_dir):
        assert target.read_text(encoding="utf-8") == "before"

    assert target.read_text(encoding="utf-8") == "before"
    assert not any((openkb_dir / "journal").glob("*.json"))


# --- publish_staged_tree: O(1) rename + durability (review #2) -------------

def _staged_raw(staging: Path, name: str, payload: bytes) -> Path:
    src = staging / "raw" / name
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(payload)
    return src


def test_publish_moves_staged_files_on_same_filesystem(tmp_path):
    """Publish must rename staged files into place (O(1)) when staging and
    the live KB share a filesystem, not stream-copy them. The surest
    observable signal: after publish the staged source is GONE (moved),
    whereas a copy leaves it behind.
    """
    kb_dir = tmp_path / "kb"
    staging = kb_dir / ".openkb" / "staging" / "add-x"
    src = _staged_raw(staging, "doc.pdf", b"%PDF-1.4 payload")

    publish_staged_tree(staging, kb_dir)

    published = kb_dir / "raw" / "doc.pdf"
    assert published.read_bytes() == b"%PDF-1.4 payload"
    assert not src.exists()  # moved, not copied


def test_published_files_keep_umask_mode_not_0600(tmp_path):
    """Published artifacts must be created at the process umask mode, not
    inherit tempfile.mkstemp's 0600. 0600 would make the KB's published
    files owner-only and inconsistent with atomic_write_bytes.
    """
    prev_umask = os.umask(0o022)
    try:
        kb_dir = tmp_path / "kb"
        staging = kb_dir / ".openkb" / "staging" / "add-y"
        _staged_raw(staging, "doc.pdf", b"data")

        publish_staged_tree(staging, kb_dir)

        from openkb.locks import _default_file_mode

        published = kb_dir / "raw" / "doc.pdf"
        assert (published.stat().st_mode & 0o777) == _default_file_mode()
    finally:
        os.umask(prev_umask)


def test_publish_falls_back_to_copy_on_cross_filesystem(tmp_path, monkeypatch):
    """When staging and the live KB are on different filesystems, the publish
    rename raises EXDEV; publish must fall back to a durable copy and still
    land the file with correct content at the destination.

    Only the cross-device publish rename raises EXDEV — the fallback copy's
    own temp-file rename is on the destination's filesystem and must succeed,
    so the fake raises exactly once then delegates to the real ``os.replace``.
    """
    import openkb.mutation as mut

    kb_dir = tmp_path / "kb"
    staging = kb_dir / ".openkb" / "staging" / "add-z"
    _staged_raw(staging, "doc.pdf", b"cross-fs payload")

    real_replace = os.replace
    calls = {"n": 0}

    def fake_replace(src, dst, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(errno.EXDEV, "cross-device link")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(mut.os, "replace", fake_replace)

    publish_staged_tree(staging, kb_dir)

    assert calls["n"] >= 2  # publish rename failed, fallback copy renamed
    assert (kb_dir / "raw" / "doc.pdf").read_bytes() == b"cross-fs payload"


# --- snapshot_paths: hardlinked dir backups (review #1) --------------------

def test_snapshot_hardlinks_marked_directory_trees(tmp_path):
    """Directory snapshots the caller marks hardlink-safe must hardlink the
    live files into the backup (shared inode) — O(1), no per-file byte copy —
    instead of streaming a fresh copy. This is what makes per-file concept /
    entity / PageIndex-blob snapshots cheap on a large KB.
    """
    kb_dir = tmp_path
    concepts = kb_dir / "wiki" / "concepts"
    concepts.mkdir(parents=True)
    existing = concepts / "old.md"
    existing.write_text("old", encoding="utf-8")
    live_inode = existing.stat().st_ino

    snapshot = snapshot_paths(
        kb_dir,
        [concepts],
        operation="add",
        details={},
        hardlink_dirs={concepts},
    )
    try:
        backup_file = snapshot.backup_dir / "wiki" / "concepts" / "old.md"
        assert backup_file.exists()
        assert backup_file.stat().st_ino == live_inode  # hardlink, not copy
    finally:
        snapshot.discard_best_effort()


def test_hardlinked_dir_rollback_correct_after_atomic_writes(tmp_path):
    """With a hardlinked dir backup, an atomic (temp+replace) rewrite of an
    existing page and creation of a new page must still roll back correctly:
    existing page restored to its pre-snapshot content, new page removed.

    This is the correctness invariant hardlinking relies on — the wiki
    writers must go through atomic temp+replace so the hardlink backup keeps
    pointing at the old inode while the live file moves to a new one.
    """
    from openkb.locks import atomic_write_text

    kb_dir = tmp_path
    concepts = kb_dir / "wiki" / "concepts"
    concepts.mkdir(parents=True)
    existing = concepts / "old.md"
    existing.write_text("old-content", encoding="utf-8")

    snapshot = snapshot_paths(
        kb_dir, [concepts], operation="add", details={}, hardlink_dirs={concepts},
    )
    # Mirror the (now atomic) compiler writers: rewrite the existing page via
    # atomic temp+replace, and add a brand-new page the doc creates.
    atomic_write_text(existing, "rewritten-content")
    (concepts / "new.md").write_text("new", encoding="utf-8")

    snapshot.rollback()
    snapshot.discard_best_effort()

    assert existing.read_text(encoding="utf-8") == "old-content"
    assert not (concepts / "new.md").exists()


def test_openkb_files_tree_is_hardlinked(tmp_path):
    """The PageIndex blob store (.openkb/files) is append-only across docs —
    each add creates new {doc_id} blobs and never modifies existing ones — so
    it is hardlink-safe and must be snapshotted via hardlinks, not copied.
    """
    kb_dir = tmp_path
    blobs = kb_dir / ".openkb" / "files" / "col"
    blobs.mkdir(parents=True)
    existing = blobs / "an-existing-doc.pdf"
    existing.write_bytes(b"existing-blob")
    live_inode = existing.stat().st_ino

    snapshot = snapshot_paths(
        kb_dir, [kb_dir / ".openkb" / "files"], operation="add",
        details={}, hardlink_dirs={kb_dir / ".openkb" / "files"},
    )
    try:
        backup = (
            snapshot.backup_dir / ".openkb" / "files" / "col" / "an-existing-doc.pdf"
        )
        assert backup.stat().st_ino == live_inode
    finally:
        snapshot.discard_best_effort()


def test_concept_writer_is_atomic_so_hardlink_rollback_restores(tmp_path):
    """Regression guard for the hardlink invariant: the wiki page writers must
    go through atomic temp+replace (new inode). If any regresses to in-place
    ``write_text`` (same inode), the hardlinked snapshot backup aliases that
    inode and rollback restores the MUTATED content instead of the original.

    Exercises _write_concept's update path — the canonical in-place modify —
    through a real hardlinked snapshot + rollback.
    """
    from openkb.agent.compiler import _write_concept

    kb_dir = tmp_path
    concepts = kb_dir / "wiki" / "concepts"
    concepts.mkdir(parents=True)
    existing = concepts / "topic.md"
    existing.write_text("---\nsources: []\n---\n\noriginal body", encoding="utf-8")

    snapshot = snapshot_paths(
        kb_dir, [concepts], operation="add", details={}, hardlink_dirs={concepts},
    )
    # The compiler rewrites the concept page as part of the doc ingest. If this
    # write is in-place, the hardlink backup is corrupted and rollback fails.
    _write_concept(kb_dir / "wiki", "topic", "rewritten body", "summaries/doc.md", is_update=True)

    snapshot.rollback()
    snapshot.discard_best_effort()

    restored = existing.read_text(encoding="utf-8")
    assert "original body" in restored
    assert "rewritten body" not in restored


def test_fix_broken_links_is_atomic_so_hardlink_rollback_restores(tmp_path):
    """Regression guard for lint --fix/remove cleanup writers.

    ``fix_broken_links`` rewrites concept/entity pages outside the add path. If
    it writes in place, a hardlinked snapshot aliases the live inode and rollback
    restores the cleaned content instead of the original page.
    """
    from openkb.lint import fix_broken_links

    kb_dir = tmp_path
    wiki = kb_dir / "wiki"
    concepts = wiki / "concepts"
    concepts.mkdir(parents=True)
    page = concepts / "topic.md"
    page.write_text("# Topic\n\nGhost [[concepts/missing]] link.\n", encoding="utf-8")

    snapshot = snapshot_paths(
        kb_dir, [concepts], operation="add", details={}, hardlink_dirs={concepts},
    )
    fix_broken_links(wiki, restrict_to=[page])

    snapshot.rollback()
    snapshot.discard_best_effort()

    restored = page.read_text(encoding="utf-8")
    assert "[[concepts/missing]]" in restored
    assert "Ghost  link" not in restored


def test_hardlink_falls_back_to_copy_on_eacces(tmp_path, monkeypatch):
    """A hardlink blocked by a Windows ACL / OneDrive sync folder surfaces as
    EACCES, not EXDEV/EPERM. _hardlink_or_copy must fall back to a real copy so
    the snapshot still succeeds — otherwise the POSIX-oriented errno set aborts
    the whole add on Windows where a plain copy would have worked.
    """
    import openkb.mutation as mut

    kb_dir = tmp_path
    concepts = kb_dir / "wiki" / "concepts"
    concepts.mkdir(parents=True)
    (concepts / "page.md").write_text("content", encoding="utf-8")

    def link_eacces(src, dst, *args, **kwargs):
        raise OSError(errno.EACCES, "simulated Windows ACL hardlink block")
    monkeypatch.setattr(mut.os, "link", link_eacces)

    snapshot = snapshot_paths(
        kb_dir, [concepts], operation="add", details={}, hardlink_dirs={concepts},
    )
    try:
        backup = snapshot.backup_dir / "wiki" / "concepts" / "page.md"
        assert backup.read_text(encoding="utf-8") == "content"  # copy fallback landed
        # It is a real copy, not a hardlink (distinct inode).
        assert backup.stat().st_ino != (concepts / "page.md").stat().st_ino
    finally:
        snapshot.discard_best_effort()


# --- recover_pending_journals: bounded retry (pre-existing issue) ----------

def test_recovery_gives_up_on_persistently_failing_journal(tmp_path, monkeypatch):
    """A journal whose rollback keeps failing (e.g. persistent ENOSPC) must
    not be retried forever — otherwise the backup dir + journal leak and every
    future lock acquisition re-attempts the same failing rollback. After
    MAX_ROLLBACK_ATTEMPTS failed attempts recovery discards it with a loud
    message so a human can intervene, bounding the on-disk retention.
    """
    import openkb.mutation as mut

    kb_dir = tmp_path
    (kb_dir / ".openkb").mkdir()
    target = kb_dir / "wiki" / "summaries" / "doc.md"
    target.parent.mkdir(parents=True)
    target.write_text("before", encoding="utf-8")
    # Leave an ACTIVE journal (simulating a crashed add).
    snapshot_paths(kb_dir, [target], operation="add", details={})
    target.write_text("after", encoding="utf-8")

    # Make rollback deterministically fail.
    def boom(self):
        raise OSError("persistent rollback failure")
    monkeypatch.setattr(mut.MutationSnapshot, "rollback", boom)

    for _ in range(mut.MAX_ROLLBACK_ATTEMPTS + 1):
        recover_pending_journals(kb_dir)

    # Given up + discarded, not retained forever.
    journal_dir = kb_dir / ".openkb" / "journal"
    assert not any(journal_dir.glob("*.json"))


@pytest.mark.parametrize(
    "payload",
    [
        "",                         # empty file -> JSONDecodeError
        "{not json",                # truncated/invalid -> JSONDecodeError
        '{"status": "active"}',     # valid JSON missing kb_dir/backup_dir -> KeyError
        '{"not": "a journal"}',     # valid JSON, wrong shape -> KeyError
    ],
)
def test_recover_skips_malformed_journal_without_bricking_lock(tmp_path, payload):
    """A corrupt/empty/stray .json in journal/ must not crash recovery.

    ``snapshot`` is assigned inside the try (after json.loads /
    _snapshot_from_journal), but the except block referenced it unconditionally
    — so a single malformed journal raised NameError out of recovery, and thus
    out of every exclusive kb_lock acquisition (draining runs on first
    acquisition), bricking add/remove/recompile/chat for the whole KB. Recovery
    must instead drop the unrecoverable journal, log loudly, and keep going so
    the lock still acquires.
    """
    from openkb.locks import kb_ingest_lock

    kb_dir = tmp_path
    journal_dir = kb_dir / ".openkb" / "journal"
    journal_dir.mkdir(parents=True)
    (journal_dir / "deadbeef.json").write_text(payload, encoding="utf-8")

    messages = recover_pending_journals(kb_dir)  # must not raise NameError
    assert any("Unrecoverable mutation journal" in m for m in messages)
    assert not any(journal_dir.glob("*.json"))  # bad journal removed, not retained

    # The whole point: the KB's mutation lock still acquires afterwards.
    with kb_ingest_lock(kb_dir / ".openkb"):
        pass


# --- O(touched) rollback for hardlinked dirs (pre-existing issue) ----------

def test_hardlinked_dir_rollback_leaves_untouched_files_in_place(tmp_path):
    """O(touched) rollback: an untouched file in a hardlinked dir shares the
    backup's inode, so rollback must leave it in place (same inode) instead
    of delete + recopy. A full-copy rollback would give it a new inode — this
    is the regression driver for the inode-aware restore.
    """
    kb_dir = tmp_path
    concepts = kb_dir / "wiki" / "concepts"
    concepts.mkdir(parents=True)
    keep = concepts / "keep.md"
    keep.write_text("keep", encoding="utf-8")
    keep_inode = keep.stat().st_ino

    snapshot = snapshot_paths(
        kb_dir, [concepts], operation="add", details={}, hardlink_dirs={concepts},
    )
    # keep.md is not mutated — it stays shared-inode with the backup.
    snapshot.rollback()
    snapshot.discard_best_effort()

    assert keep.exists()
    assert keep.read_text(encoding="utf-8") == "keep"
    assert keep.stat().st_ino == keep_inode  # NOT recopied


def test_hardlinked_dir_rollback_removes_new_and_restores_modified(tmp_path):
    from openkb.locks import atomic_write_text

    kb_dir = tmp_path
    concepts = kb_dir / "wiki" / "concepts"
    concepts.mkdir(parents=True)
    (concepts / "old.md").write_text("old", encoding="utf-8")
    page = concepts / "page.md"
    page.write_text("original", encoding="utf-8")

    snapshot = snapshot_paths(
        kb_dir, [concepts], operation="add", details={}, hardlink_dirs={concepts},
    )
    # Commit created a new page and atomically rewrote an existing one.
    (concepts / "new.md").write_text("new", encoding="utf-8")
    atomic_write_text(page, "rewritten")

    snapshot.rollback()
    snapshot.discard_best_effort()

    assert (concepts / "old.md").read_text(encoding="utf-8") == "old"
    assert page.read_text(encoding="utf-8") == "original"
    assert not (concepts / "new.md").exists()


def test_hardlinked_dir_rollback_prunes_new_nested_blob_dirs(tmp_path):
    """PageIndex blob-store scenario: an existing blob is untouched (shared
    inode, left in place), while a new doc's blob + its nested images subdir
    are removed on rollback — including the now-empty newdoc/ directory.
    """
    kb_dir = tmp_path
    files = kb_dir / ".openkb" / "files"
    (files / "col").mkdir(parents=True)
    existing = files / "col" / "existing.pdf"
    existing.write_bytes(b"existing")
    existing_inode = existing.stat().st_ino

    snapshot = snapshot_paths(
        kb_dir, [files], operation="add", details={}, hardlink_dirs={files},
    )
    (files / "col" / "newdoc.pdf").write_bytes(b"new")
    (files / "col" / "newdoc" / "images").mkdir(parents=True)
    (files / "col" / "newdoc" / "images" / "p1.png").write_bytes(b"png")

    snapshot.rollback()
    snapshot.discard_best_effort()

    assert existing.read_bytes() == b"existing"
    assert existing.stat().st_ino == existing_inode  # untouched, not recopied
    assert not (files / "col" / "newdoc.pdf").exists()
    assert not (files / "col" / "newdoc").exists()  # empty new dir pruned


# --- track_new: cheap blob-store rollback without whole-tree snapshot -------

def test_track_new_removes_new_blob_on_rollback(tmp_path):
    """The PageIndex blob under .openkb/files gets its {doc_id} name only once
    indexing runs — after snapshot_paths. Instead of snapshotting the whole
    (append-only) blob store up front, the add path calls track_new() with the
    new artifacts; rollback must then remove exactly those and leave every
    pre-existing blob untouched.
    """
    kb_dir = tmp_path
    blobs = kb_dir / ".openkb" / "files" / "col"
    blobs.mkdir(parents=True)
    existing = blobs / "old-doc.pdf"
    existing.write_bytes(b"keep-me")
    existing_inode = existing.stat().st_ino

    # Snapshot does NOT include .openkb/files at all.
    snapshot = snapshot_paths(kb_dir, [kb_dir / "wiki"], operation="add", details={})

    # Indexing creates the new blob + its images subtree (doc_id now known).
    new_blob = blobs / "new-doc.pdf"
    new_blob.write_bytes(b"remove-me")
    new_images = blobs / "new-doc"
    (new_images / "images").mkdir(parents=True)
    (new_images / "images" / "p1.png").write_bytes(b"img")

    snapshot.track_new([new_blob, new_images])
    snapshot.rollback()
    snapshot.discard_best_effort()

    assert not new_blob.exists()            # new blob removed
    assert not new_images.exists()          # new images subtree removed
    assert existing.read_bytes() == b"keep-me"   # pre-existing untouched
    assert existing.stat().st_ino == existing_inode  # not recopied/relinked


def test_track_new_persists_to_journal_for_crash_recovery(tmp_path):
    """track_new() must rewrite the active journal so a crash *after* indexing
    but before commit still rolls back the new blob on the next exclusive-lock
    acquisition (recover_pending_journals), not just via in-process rollback.
    """
    kb_dir = tmp_path
    blobs = kb_dir / ".openkb" / "files" / "col"
    blobs.mkdir(parents=True)

    snapshot = snapshot_paths(kb_dir, [kb_dir / "wiki"], operation="add", details={})
    new_blob = blobs / "new-doc.pdf"
    new_blob.write_bytes(b"remove-me")
    snapshot.track_new([new_blob])
    # Simulate a crash: journal left "active", no rollback()/mark_committed().

    messages = recover_pending_journals(kb_dir)

    assert not new_blob.exists()
    assert any("Rolled back" in m for m in messages)


def test_snapshot_add_paths_excludes_blob_store(tmp_path):
    """The blob store is registered lazily via track_new(), so it must NOT be
    in the eager add snapshot path list (that was the O(total blobs)-per-add
    cost this change removes)."""
    from openkb.cli import _snapshot_add_paths

    paths = _snapshot_add_paths(tmp_path, "doc", None, None)
    assert (tmp_path / ".openkb" / "files") not in paths
    # hashes.json / pageindex.db are still snapshotted eagerly.
    assert (tmp_path / ".openkb" / "hashes.json") in paths
