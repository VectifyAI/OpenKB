"""Enforce a per-module line limit so files stay legible to agents.

Failure messages carry remediation (rule + why + how to fix) so the guidance
lands directly in agent context. Existing over-limit files are grandfathered
below, each with a reason; maintainers additionally track them in
docs/internal/tech-debt.md (maintainer-local, not in git).
"""

from __future__ import annotations

from pathlib import Path

import openkb

LIMIT = 800
# Resolve the package from the imported module (not path math relative to this
# file) so the gate cannot go silently vacuous if this test file moves.
_PKG = Path(openkb.__file__).resolve().parent
_REPO_ROOT = _PKG.parent

# Grandfathered: existing debt. Keys are posix paths relative to the repo
# root; add a brief reason comment with every new entry.
_GRANDFATHERED = {
    "openkb/cli.py",  # monolithic Click entry point; split into command groups
    "openkb/agent/compiler.py",  # LLM wiki compiler; split into focused units
    "openkb/agent/chat.py",  # chat loop; extract cohesive concerns
}


def _line_count(path: Path) -> int:
    # Physical lines; splitlines() handles \n, \r\n, and bare \r alike, so an
    # unusual line-ending style cannot under-count and slip past the gate.
    return len(path.read_bytes().splitlines())


def _py_files(pkg: Path) -> list[Path]:
    return [p for p in sorted(pkg.rglob("*.py")) if "__pycache__" not in p.parts]


def _files_over_limit(
    root: Path, pkg: Path, limit: int, grandfathered: set[str]
) -> list[tuple[str, int]]:
    over: list[tuple[str, int]] = []
    for path in _py_files(pkg):
        rel = path.relative_to(root).as_posix()
        if rel in grandfathered:
            continue
        n = _line_count(path)
        if n >= limit:
            over.append((rel, n))
    return over


def test_detector_flags_oversize(tmp_path):
    (tmp_path / "big.py").write_text("x = 1\n" * 5)
    (tmp_path / "small.py").write_text("x = 1\n" * 2)
    over = _files_over_limit(tmp_path, tmp_path, limit=3, grandfathered=set())
    assert [name for name, _ in over] == ["big.py"]


def test_exactly_at_limit_is_flagged(tmp_path):
    # Docs promise modules stay "under" the limit, so a file AT it violates.
    (tmp_path / "edge.py").write_text("x = 1\n" * 3)
    over = _files_over_limit(tmp_path, tmp_path, limit=3, grandfathered=set())
    assert [name for name, _ in over] == ["edge.py"]


def test_bare_cr_line_endings_are_counted(tmp_path):
    (tmp_path / "cr.py").write_bytes(b"x = 1\r" * 10)
    assert _line_count(tmp_path / "cr.py") == 10


def test_grandfathered_files_are_exempt(tmp_path):
    (tmp_path / "old.py").write_text("x = 1\n" * 5)
    over = _files_over_limit(tmp_path, tmp_path, limit=3, grandfathered={"old.py"})
    assert over == []


def test_no_module_exceeds_limit():
    files = _py_files(_PKG)
    assert files, f"no Python files found under {_PKG} — the scan would be vacuous"
    over = _files_over_limit(_REPO_ROOT, _PKG, LIMIT, _GRANDFATHERED)
    if over:
        lines = "\n".join(f"  - {rel}: {n} lines" for rel, n in over)
        raise AssertionError(
            f"These modules reach or exceed the {LIMIT}-line limit:\n{lines}\n\n"
            "How to fix: split cohesive groups into focused modules by "
            "responsibility (see docs/golden-principles.md#file-size). To "
            "grandfather an existing file instead, add its repo-relative path "
            "to _GRANDFATHERED in this test with a brief reason comment. "
            "(Maintainers additionally log grandfathered files in "
            "docs/internal/tech-debt.md — maintainer-local, not in git.)"
        )
