"""Enforce a per-module line limit so files stay legible to agents.

Failure messages carry remediation (rule + why + how to fix) so the guidance
lands directly in agent context. Existing over-limit files are grandfathered
and tracked in docs/internal/tech-debt.md.
"""
from __future__ import annotations

from pathlib import Path

LIMIT = 800
_REPO_ROOT = Path(__file__).resolve().parent.parent
_PKG = _REPO_ROOT / "openkb"

# Grandfathered: existing debt, tracked in docs/internal/tech-debt.md.
_GRANDFATHERED = {
    "openkb/cli.py",
    "openkb/agent/compiler.py",
    "openkb/agent/chat.py",
}


def _line_count(path: Path) -> int:
    # Physical line count.
    with path.open("rb") as fh:
        return sum(1 for _ in fh)


def _files_over_limit(pkg: Path, limit: int, grandfathered: set[str]) -> list[tuple[str, int]]:
    over: list[tuple[str, int]] = []
    for path in sorted(pkg.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            rel = path.relative_to(_REPO_ROOT).as_posix()
        except ValueError:
            # pkg lives outside the repo root (e.g. a tmp_path in tests).
            rel = path.relative_to(pkg).as_posix()
        if rel in grandfathered:
            continue
        n = _line_count(path)
        if n > limit:
            over.append((rel, n))
    return over


def test_detector_flags_oversize(tmp_path):
    # Unit-test the detector itself with a synthetic oversize file.
    (tmp_path / "big.py").write_text("x = 1\n" * 5)
    over = _files_over_limit(tmp_path, limit=3, grandfathered=set())
    assert over and over[0][0].endswith("big.py")


def test_no_module_exceeds_limit():
    over = _files_over_limit(_PKG, LIMIT, _GRANDFATHERED)
    if over:
        lines = "\n".join(f"  - {rel}: {n} lines" for rel, n in over)
        raise AssertionError(
            f"These modules exceed the {LIMIT}-line limit:\n{lines}\n\n"
            "How to fix: split cohesive groups into focused modules by "
            "responsibility (see docs/golden-principles.md#file-size). To "
            "grandfather an existing file, add its repo-relative path to "
            "_GRANDFATHERED in this test AND record it in "
            "docs/internal/tech-debt.md."
        )
