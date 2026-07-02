"""All skill-related code (generator, marketplace, agent runtime, validator,
workspace, evaluator) lives in this subpackage.

Today's only artifact type is ``skill``; the generator + marketplace
abstractions are nominally generic, but in v0.x they only serve skill
artifacts. If/when ppt / podcast / report targets land, factor the
generic primitives back out to ``openkb/<shared>/`` at that time.

This module also owns two single-source-of-truth helpers consumed by
every submodule:

* path construction — ``skill_dir`` / ``skills_root`` / ``skill_workspace_dir``
* SKILL.md frontmatter parsing — ``extract_frontmatter`` / ``extract_description``

Keeping them at the package root avoids both circular imports and the
"five files independently hardcode the same path" drift problem.
"""

from __future__ import annotations

import re
from pathlib import Path

__all__ = [
    "skills_root",
    "skill_dir",
    "skill_workspace_dir",
    "extract_frontmatter",
    "extract_body",
    "extract_description",
]


def skills_root(kb_dir: Path) -> Path:
    """``<kb>/output/skills`` — the directory holding every compiled skill."""
    return kb_dir / "output" / "skills"


def skill_dir(kb_dir: Path, skill_name: str) -> Path:
    """``<kb>/output/skills/<name>`` — one compiled skill's home."""
    return skills_root(kb_dir) / skill_name


def skill_workspace_dir(kb_dir: Path, skill_name: str) -> Path:
    """``<kb>/output/skills/<name>-workspace`` — iteration history for a skill."""
    return skills_root(kb_dir) / f"{skill_name}-workspace"


_DESC_RE = re.compile(r"^description:\s*(.*?)\s*$", re.MULTILINE)


def extract_frontmatter(text: str) -> str | None:
    """Return the YAML body between the first two ``---`` lines, or ``None``.

    Canonical parser for SKILL.md frontmatter — every consumer in this
    package should route through here so edge cases (CRLF, missing close,
    body containing ``---``) behave identically across the validator,
    evaluator, marketplace, and workspace modules.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    try:
        end = lines.index("---", 1)
    except ValueError:
        return None
    return "\n".join(lines[1:end])


def extract_body(text: str) -> str:
    """Return the body of a SKILL.md — everything after the closing ``---``.

    Uses the same line-anchored logic as :func:`extract_frontmatter` so a
    body that contains a standalone ``---`` (e.g. a Markdown horizontal
    rule) is preserved intact. Files without frontmatter return their
    full text unchanged.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    try:
        end = lines.index("---", 1)
    except ValueError:
        return text
    return "\n".join(lines[end + 1 :])


def extract_description(skill_md: Path) -> str:
    """Return the ``description:`` value from a SKILL.md, or ``""``.

    Missing file, missing frontmatter, or missing field all return the
    empty string — callers that need to distinguish should check the file
    themselves first.
    """
    if not skill_md.is_file():
        return ""
    text = skill_md.read_text(encoding="utf-8", errors="replace")
    fm = extract_frontmatter(text)
    if fm is None:
        return ""
    m = _DESC_RE.search(fm)
    return m.group(1).strip() if m else ""
