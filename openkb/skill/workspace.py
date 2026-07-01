"""Skill iteration workspace — preserve history, enable rollback.

When ``openkb skill new <name> -y`` would overwrite an existing skill,
the CLI calls :func:`save_iteration` first to copy the current skill
directory into a parallel ``<kb>/output/skills/<name>-workspace/`` tree
under ``iteration-N/``. Iteration numbers monotonically increase across
overwrites, so no work is destroyed by a new compile.

After the new skill is generated, :func:`write_diff` drops a
``diff.md`` inside the saved iteration capturing the structural delta
(description change, ref/script add/remove, SKILL.md line-count delta).
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from openkb.skill import (
    extract_description,
)
from openkb.skill import (
    skill_dir as _skill_dir,
)
from openkb.skill import (
    skill_workspace_dir as _workspace_dir,
)

__all__ = [
    "save_iteration",
    "list_iterations",
    "restore_iteration",
    "write_diff",
]


_ITER_RE = re.compile(r"^iteration-(\d+)$")


def _iter_number(path: Path) -> int | None:
    m = _ITER_RE.match(path.name)
    return int(m.group(1)) if m else None


def list_iterations(kb_dir: Path, skill_name: str) -> list[Path]:
    """List existing iteration directories for a skill, sorted by N ascending.

    Returns an empty list if the workspace doesn't exist.
    """
    ws = _workspace_dir(kb_dir, skill_name)
    if not ws.is_dir():
        return []
    iters: list[tuple[int, Path]] = []
    for child in ws.iterdir():
        if not child.is_dir():
            continue
        n = _iter_number(child)
        if n is None:
            continue
        iters.append((n, child))
    iters.sort(key=lambda t: t[0])
    return [p for _, p in iters]


def save_iteration(kb_dir: Path, skill_name: str) -> Path | None:
    """Copy current ``<kb>/output/skills/<name>/`` to the next iteration slot.

    Returns the saved iteration path, or ``None`` if there's no current
    skill to save (first compile of a new name).
    """
    src = _skill_dir(kb_dir, skill_name)
    if not src.is_dir():
        return None

    existing = list_iterations(kb_dir, skill_name)
    next_n = (max((_iter_number(p) for p in existing), default=0) or 0) + 1

    ws = _workspace_dir(kb_dir, skill_name)
    ws.mkdir(parents=True, exist_ok=True)
    dest = ws / f"iteration-{next_n}"
    shutil.copytree(src, dest)
    return dest


def restore_iteration(kb_dir: Path, skill_name: str, n: int | None = None) -> Path:
    """Restore an iteration as the current skill.

    If ``n`` is ``None``, restore the highest-numbered iteration. Raises
    ``FileNotFoundError`` if the requested iteration doesn't exist.

    Returns the path to the restored skill directory.
    """
    iters = list_iterations(kb_dir, skill_name)
    if not iters:
        raise FileNotFoundError(f"No iterations exist for skill {skill_name!r}.")

    if n is None:
        src = iters[-1]
    else:
        match = next(
            (p for p in iters if _iter_number(p) == n),
            None,
        )
        if match is None:
            raise FileNotFoundError(f"Iteration {n} not found for skill {skill_name!r}.")
        src = match

    # Save the current state before overwriting it — rollback is a mutation
    # too, and the workspace promise ("no work is destroyed") has to hold
    # in both directions. A user who edits files in chat then rolls back
    # gets those edits preserved as the next iteration, not silently lost.
    dest = _skill_dir(kb_dir, skill_name)
    if dest.exists():
        save_iteration(kb_dir, skill_name)
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    return dest


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------


def _list_files(root: Path, subdir: str) -> set[str]:
    base = root / subdir
    if not base.is_dir():
        return set()
    return {str(p.relative_to(root)).replace("\\", "/") for p in base.rglob("*") if p.is_file()}


def _line_count(path: Path) -> int:
    if not path.is_file():
        return 0
    return len(path.read_text(encoding="utf-8", errors="replace").splitlines())


def write_diff(prev: Path, curr: Path, diff_path: Path) -> None:
    """Write a human-readable structural diff from ``prev`` to ``curr``.

    Covers:
      * description: line changes (from SKILL.md frontmatter)
      * files added/removed under ``references/`` and ``scripts/``
      * line-count delta on SKILL.md
    """
    prev_desc = extract_description(prev / "SKILL.md")
    curr_desc = extract_description(curr / "SKILL.md")

    prev_refs = _list_files(prev, "references")
    curr_refs = _list_files(curr, "references")
    prev_scripts = _list_files(prev, "scripts")
    curr_scripts = _list_files(curr, "scripts")

    added = sorted((curr_refs - prev_refs) | (curr_scripts - prev_scripts))
    removed = sorted((prev_refs - curr_refs) | (prev_scripts - curr_scripts))

    prev_lc = _line_count(prev / "SKILL.md")
    curr_lc = _line_count(curr / "SKILL.md")
    delta = curr_lc - prev_lc

    lines: list[str] = []
    lines.append(f"# Skill diff: {prev.name} -> current\n")

    lines.append("## description\n")
    if prev_desc == curr_desc:
        lines.append("_unchanged_\n")
    else:
        lines.append(f"- before: {prev_desc or '(none)'}")
        lines.append(f"- after:  {curr_desc or '(none)'}\n")

    lines.append("## files\n")
    if not added and not removed:
        lines.append("_no files added or removed_\n")
    else:
        for p in added:
            lines.append(f"- added: {p}")
        for p in removed:
            lines.append(f"- removed: {p}")
        lines.append("")

    lines.append("## SKILL.md line count\n")
    sign = "+" if delta >= 0 else ""
    lines.append(f"- before: {prev_lc} lines")
    lines.append(f"- after:  {curr_lc} lines ({sign}{delta})\n")

    diff_path.parent.mkdir(parents=True, exist_ok=True)
    diff_path.write_text("\n".join(lines), encoding="utf-8")
