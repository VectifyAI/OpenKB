from __future__ import annotations

import pytest

from openkb.mutation import recover_pending_journals, snapshot_paths


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
