"""Tests for :mod:`openkb.skill.workspace` — iteration save/restore + diff."""

from __future__ import annotations

from pathlib import Path

import pytest

from openkb.skill.workspace import (
    list_iterations,
    restore_iteration,
    save_iteration,
    write_diff,
)


def _make_skill(
    kb_dir: Path,
    name: str,
    *,
    description: str = "demo desc",
    refs: list[str] | None = None,
    skill_md_lines: int = 5,
) -> Path:
    target = kb_dir / "output" / "skills" / name
    target.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"line {i}" for i in range(1, max(1, skill_md_lines) + 1))
    (target / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    if refs:
        refs_dir = target / "references"
        refs_dir.mkdir(parents=True, exist_ok=True)
        for r in refs:
            (refs_dir / r).write_text("# ref\n", encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# save_iteration
# ---------------------------------------------------------------------------


def test_save_iteration_returns_none_when_no_current_skill(tmp_path):
    assert save_iteration(tmp_path, "missing") is None


def test_save_iteration_writes_iteration_1_then_2(tmp_path):
    _make_skill(tmp_path, "demo", description="v1")
    first = save_iteration(tmp_path, "demo")
    assert first is not None
    assert first.name == "iteration-1"
    assert (first / "SKILL.md").exists()
    assert "v1" in (first / "SKILL.md").read_text()

    # Mutate, then save again
    _make_skill(tmp_path, "demo", description="v2")
    second = save_iteration(tmp_path, "demo")
    assert second is not None
    assert second.name == "iteration-2"
    assert "v2" in (second / "SKILL.md").read_text()
    # First survives untouched
    assert "v1" in (first / "SKILL.md").read_text()


# ---------------------------------------------------------------------------
# list_iterations
# ---------------------------------------------------------------------------


def test_list_iterations_returns_empty_when_no_workspace(tmp_path):
    assert list_iterations(tmp_path, "nothing") == []


def test_list_iterations_returns_sorted(tmp_path):
    _make_skill(tmp_path, "demo")
    save_iteration(tmp_path, "demo")
    _make_skill(tmp_path, "demo", description="v2")
    save_iteration(tmp_path, "demo")
    iters = list_iterations(tmp_path, "demo")
    assert [p.name for p in iters] == ["iteration-1", "iteration-2"]


# ---------------------------------------------------------------------------
# restore_iteration
# ---------------------------------------------------------------------------


def test_restore_iteration_latest_when_n_is_none(tmp_path):
    _make_skill(tmp_path, "demo", description="v1")
    save_iteration(tmp_path, "demo")
    _make_skill(tmp_path, "demo", description="v2")
    save_iteration(tmp_path, "demo")
    # Simulate current skill being something else / broken
    _make_skill(tmp_path, "demo", description="broken")

    restored = restore_iteration(tmp_path, "demo", n=None)
    text = (restored / "SKILL.md").read_text()
    assert "v2" in text
    assert "broken" not in text


def test_restore_iteration_specific_n(tmp_path):
    _make_skill(tmp_path, "demo", description="v1")
    save_iteration(tmp_path, "demo")
    _make_skill(tmp_path, "demo", description="v2")
    save_iteration(tmp_path, "demo")

    restored = restore_iteration(tmp_path, "demo", n=1)
    assert "v1" in (restored / "SKILL.md").read_text()


def test_restore_iteration_missing_raises(tmp_path):
    _make_skill(tmp_path, "demo")
    save_iteration(tmp_path, "demo")
    with pytest.raises(FileNotFoundError):
        restore_iteration(tmp_path, "demo", n=99)


def test_restore_iteration_no_workspace_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        restore_iteration(tmp_path, "demo")


def test_restore_iteration_preserves_current_state(tmp_path):
    """Rollback is reversible: the pre-rollback skill is saved before overwrite,
    so a user who edited files in chat then rolled back can recover those edits.
    """
    _make_skill(tmp_path, "demo", description="v1")
    save_iteration(tmp_path, "demo")  # iteration-1
    # Simulate an in-place edit (e.g. via /skill or write_kb_file) — no save call.
    _make_skill(tmp_path, "demo", description="unsaved-edit")

    restore_iteration(tmp_path, "demo", n=1)

    # Pre-rollback state should be preserved as a new iteration, not lost.
    iters = list_iterations(tmp_path, "demo")
    assert len(iters) == 2
    preserved = iters[-1] / "SKILL.md"
    assert "unsaved-edit" in preserved.read_text()


# ---------------------------------------------------------------------------
# write_diff
# ---------------------------------------------------------------------------


def test_write_diff_records_description_change(tmp_path):
    prev = _make_skill(tmp_path / "prev", "demo", description="old description")
    curr = _make_skill(tmp_path / "curr", "demo", description="new description")
    out = tmp_path / "diff.md"
    write_diff(prev, curr, out)
    content = out.read_text()
    assert "description" in content.lower()
    assert "old description" in content
    assert "new description" in content


def test_write_diff_records_added_reference(tmp_path):
    prev = _make_skill(tmp_path / "prev", "demo", refs=[])
    curr = _make_skill(tmp_path / "curr", "demo", refs=["new.md"])
    out = tmp_path / "diff.md"
    write_diff(prev, curr, out)
    content = out.read_text()
    assert "added:" in content
    assert "references/new.md" in content


def test_write_diff_records_removed_reference(tmp_path):
    prev = _make_skill(tmp_path / "prev", "demo", refs=["old.md"])
    curr = _make_skill(tmp_path / "curr", "demo", refs=[])
    out = tmp_path / "diff.md"
    write_diff(prev, curr, out)
    content = out.read_text()
    assert "removed:" in content
    assert "references/old.md" in content
