"""Regenerate the per-KB Claude Code plugin marketplace manifest.

After every `openkb skill new` (and after any chat-side edit to a SKILL.md
frontmatter), this module scans ``<kb>/output/skills/*/SKILL.md`` and
rewrites ``<kb>/.claude-plugin/marketplace.json`` listing all currently
compiled skills.

The schema is a subset compatible with the OpenKB repo's own
``.claude-plugin/marketplace.json``: one plugin entry per KB, with a
``skills`` array of relative paths. ``owner`` is derived from git config
so Claude Code's ``/plugin marketplace add`` accepts the manifest. Other
agent CLIs (``npx skills add``) install from the same file.

This is a deterministic step — no LLM calls.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from openkb.config import load_config


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
_DESCRIPTION_RE = re.compile(r"^description:\s*(.+?)\s*$", re.MULTILINE)


def _git_owner() -> dict[str, str]:
    """Read user.name and user.email from git config for the manifest owner.

    Falls back to placeholders if git isn't configured — the manifest is
    still valid, just less helpful for marketplace listings.
    """
    import subprocess

    def _git(key: str) -> str:
        try:
            result = subprocess.run(
                ["git", "config", "--get", key],
                capture_output=True, text=True, timeout=2,
            )
            return result.stdout.strip()
        except (subprocess.SubprocessError, FileNotFoundError):
            return ""

    name = _git("user.name") or "openkb-user"
    email = _git("user.email") or ""
    owner: dict[str, str] = {"name": name}
    if email:
        owner["email"] = email
    return owner


def _read_skill_description(skill_md: Path) -> str:
    """Pull the ``description:`` field from a SKILL.md frontmatter block.

    Returns an empty string if missing or unparseable — the manifest still
    lists the skill, just with a generic plugin-level description.
    """
    if not skill_md.exists():
        return ""
    text = skill_md.read_text(encoding="utf-8")
    fm_match = _FRONTMATTER_RE.match(text)
    if not fm_match:
        return ""
    desc_match = _DESCRIPTION_RE.search(fm_match.group(1))
    if not desc_match:
        return ""
    return desc_match.group(1).strip()


def _kb_name(kb_dir: Path) -> str:
    """Use the KB directory name as the marketplace name (sluggable)."""
    return kb_dir.name


def _list_skill_dirs(kb_dir: Path) -> list[Path]:
    """Return skill directories under <kb>/output/skills/ that contain a SKILL.md."""
    skills_root = kb_dir / "output" / "skills"
    if not skills_root.is_dir():
        return []
    return sorted(
        d for d in skills_root.iterdir()
        if d.is_dir() and (d / "SKILL.md").exists()
    )


def _build_manifest(kb_dir: Path) -> dict[str, Any]:
    skills = _list_skill_dirs(kb_dir)
    skill_paths = [f"./output/skills/{d.name}" for d in skills]

    # Aggregate description for the manifest metadata
    name = _kb_name(kb_dir)
    metadata_desc = (
        f"Skills compiled from the '{name}' knowledge base via OpenKB."
    )
    if skills:
        first_desc = _read_skill_description(skills[0] / "SKILL.md")
        if first_desc:
            metadata_desc += f" Featured: {first_desc[:200]}"

    # Pull KB config for version if available; default to 0.1.0
    config = load_config(kb_dir / ".openkb" / "config.yaml")
    version = str(config.get("version", "0.1.0"))

    owner = _git_owner()
    return {
        "name": name,
        "owner": owner,
        "metadata": {
            "description": metadata_desc,
            "version": version,
        },
        "plugins": [
            {
                "name": name,
                "description": metadata_desc,
                "source": "./",
                "version": version,
                "author": owner,
                "skills": skill_paths,
            }
        ],
    }


def regenerate_marketplace(kb_dir: Path) -> Path:
    """Rewrite ``<kb>/.claude-plugin/marketplace.json`` from current skills.

    Returns the path to the manifest. Creates ``.claude-plugin/`` if needed.
    Safe to call when zero skills exist (manifest lists an empty ``skills``
    array).
    """
    manifest = _build_manifest(kb_dir)
    out_dir = kb_dir / ".claude-plugin"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "marketplace.json"
    out_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return out_path
