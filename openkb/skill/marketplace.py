"""Regenerate the per-KB Claude Code plugin marketplace manifest.

After every successful skill generation (CLI ``openkb skill new`` or
chat ``/skill new``, both via ``Generator.run``), this module scans
``<kb>/output/skills/*/SKILL.md`` and rewrites
``<kb>/.claude-plugin/marketplace.json`` listing all currently
compiled skills.

The schema is a subset compatible with the OpenKB repo's own
``.claude-plugin/marketplace.json``: one plugin entry per KB, with a
``skills`` array of relative paths. ``owner`` is derived from git
config (run with cwd=kb_dir) so Claude Code's ``/plugin marketplace
add`` accepts the manifest. Other agent CLIs (``npx skills add``)
install from the same file.

This is a deterministic step — no LLM calls. If a chat-session edit
to a SKILL.md changes the description after compile, the manifest is
NOT auto-regenerated; re-run ``openkb skill new`` or
``/skill new`` to refresh it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openkb.config import load_config
from openkb.skill import skills_root


def _git_owner(kb_dir: Path) -> dict[str, str]:
    """Read user.name and user.email from git config (run in kb_dir context).

    Falls back to placeholders if git isn't configured. ``cwd=kb_dir`` so
    that ``git config`` resolves the KB's local-or-walked-up settings,
    not the process's working directory at the time of CLI invocation.
    """
    import subprocess

    def _git(key: str) -> str:
        try:
            result = subprocess.run(
                ["git", "config", "--get", key],
                capture_output=True,
                text=True,
                timeout=2,
                cwd=str(kb_dir),
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


def _list_skill_dirs(kb_dir: Path) -> list[Path]:
    """Return skill directories under <kb>/output/skills/ that contain a SKILL.md."""
    root = skills_root(kb_dir)
    if not root.is_dir():
        return []
    return sorted(d for d in root.iterdir() if d.is_dir() and (d / "SKILL.md").exists())


def _build_manifest(kb_dir: Path) -> dict[str, Any]:
    skills = _list_skill_dirs(kb_dir)
    skill_paths = [f"./output/skills/{d.name}" for d in skills]

    # Fixed clean descriptions — no truncation, no SKILL.md interpolation.
    # Naming convention is locked to `openkb@vectify` so users get one
    # canonical install command regardless of which KB they're consuming;
    # different KBs are distinguished by <owner>/<repo> URL.
    metadata_desc = f"Skills compiled from the {kb_dir.name} knowledge base via OpenKB."
    plugin_desc = "Knowledge skills compiled from this OpenKB-managed knowledge base."

    # Pull KB config for version if available; default to 0.1.0
    config = load_config(kb_dir / ".openkb" / "config.yaml")
    version = str(config.get("version", "0.1.0"))

    owner = _git_owner(kb_dir)
    return {
        "name": "vectify",
        "owner": owner,
        "metadata": {
            "description": metadata_desc,
            "version": version,
        },
        "plugins": [
            {
                "name": "openkb",
                "description": plugin_desc,
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
