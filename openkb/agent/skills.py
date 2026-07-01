"""Skill discovery for openkb chat — Anthropic-style SKILL.md format.

Scans skill directories and returns the metadata list the OpenAI Agents
SDK expects for ``ShellTool.environment.skills``: a list of
``{name, description, path}`` dicts.

Skill search roots (first hit wins on name collision):

  1. ``<kb>/skills/``         — project-local skills shipped with the KB
  2. ``~/.openkb/skills/``    — user-global skills
  3. ``~/.claude/skills/``    — Claude Code's skill dir (interop bonus)
  4. bundled skills           — built-in deck themes / critic shipped with
                               the package (lowest priority, so the roots
                               above can override them)

Skill file layout::

    <root>/<skill-name>/
      SKILL.md          # frontmatter + body
      references/...    # optional supporting files (the skill body can
                        # cite them; agent reads via shell)

Frontmatter::

    ---
    name: my-skill
    description: One-line trigger description the agent sees up front.
    ---
    <skill body — instructions the agent reads when it loads the skill>
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Tuple

import yaml

DEFAULT_SKILL_ROOTS: Tuple[str, ...] = (
    "skills",  # relative to kb_dir
    "~/.openkb/skills",
    "~/.claude/skills",
)

# Skills shipped with the package so the built-in deck themes + html critic
# work out of the box (no manual install). Two candidates cover both install
# modes; whichever exists is scanned, at lowest priority:
#   - wheel install:  force-included at ``openkb/_skills/``
#   - editable/source checkout:  the repo's top-level ``skills/``
_PKG_DIR = Path(__file__).resolve().parent.parent
BUNDLED_SKILL_ROOTS: Tuple[str, ...] = (
    str(_PKG_DIR / "_skills"),
    str(_PKG_DIR.parent / "skills"),
)


def _parse_frontmatter(text: str) -> Tuple[dict, str]:
    """Return ``(metadata_dict, body)`` from a markdown file with YAML
    frontmatter delimited by ``---`` lines. Files without frontmatter
    return ``({}, full_text)``.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    try:
        end = lines.index("---", 1)
    except ValueError:
        return {}, text
    try:
        meta = yaml.safe_load("\n".join(lines[1:end])) or {}
    except yaml.YAMLError:
        meta = {}
    body = "\n".join(lines[end + 1 :])
    return meta if isinstance(meta, dict) else {}, body


def scan_local_skills(
    kb_dir: Path,
    extra_roots: Iterable[str | Path] = (),
) -> list[dict[str, str]]:
    """Scan known skill directories. Return SDK-shape skill list.

    Each entry is ``{"name": str, "description": str, "path": str}`` —
    the exact shape :class:`agents.ShellToolLocalSkill` expects.

    Args:
        kb_dir: KB root. Used to resolve the relative ``skills/`` root.
        extra_roots: Additional roots to scan, appended after defaults.

    Returns:
        List of skill metadata dicts. Empty if no skills found.
    """
    seen: dict[str, dict[str, str]] = {}
    # Bundled roots go last so KB/user/Claude skills override the built-ins.
    roots = list(DEFAULT_SKILL_ROOTS) + [str(r) for r in extra_roots] + list(BUNDLED_SKILL_ROOTS)
    for root_spec in roots:
        root = Path(root_spec).expanduser()
        if not root.is_absolute():
            root = kb_dir / root
        if not root.is_dir():
            continue
        for skill_dir in sorted(root.iterdir()):
            if not skill_dir.is_dir():
                continue
            md = skill_dir / "SKILL.md"
            if not md.is_file():
                continue
            try:
                text = md.read_text(encoding="utf-8")
            except OSError:
                continue
            meta, _body = _parse_frontmatter(text)
            name = str(meta.get("name") or skill_dir.name).strip()
            desc = str(meta.get("description") or "").strip()
            if not name or not desc:
                continue  # SDK requires both
            if name in seen:
                continue  # first-hit-wins; earlier roots take precedence
            seen[name] = {
                "name": name,
                "description": desc[:1024],
                "path": str(skill_dir.resolve()),
            }
    return list(seen.values())
