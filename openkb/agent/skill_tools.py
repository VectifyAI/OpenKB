"""Path-scoped IO tools for the skill-create agent.

The skill-create agent runs with three capabilities:
  * READ from <kb>/wiki/**            (the substrate)
  * QUERY the wiki via openkb query   (semantic retrieval, see skill_creator.py)
  * WRITE under <kb>/output/skills/<name>/**   (the only output destination)

These helpers enforce those boundaries at the Python level — every tool
resolves its target path, then verifies it stays inside the allowed root.
Path traversal (``..``) and absolute paths are rejected outright.
"""
from __future__ import annotations

from pathlib import Path


def list_wiki_dir(directory: str, wiki_root: str) -> str:
    """List ``.md`` files in a wiki subdirectory.

    Args:
        directory: Path relative to *wiki_root* (e.g. ``"concepts"``).
        wiki_root: Absolute path to ``<kb>/wiki``.
    """
    root = Path(wiki_root).resolve()
    target = (root / directory).resolve()
    if not target.is_relative_to(root):
        return "Access denied: path escapes wiki root."
    if not target.exists() or not target.is_dir():
        return "No files found."
    names = sorted(p.name for p in target.iterdir() if p.suffix == ".md")
    return "\n".join(names) if names else "No files found."


def read_wiki_file_for_skill(path: str, wiki_root: str) -> str:
    """Read a Markdown file from the wiki.

    Args:
        path: File path relative to *wiki_root* (e.g. ``"concepts/attention.md"``).
        wiki_root: Absolute path to ``<kb>/wiki``.
    """
    root = Path(wiki_root).resolve()
    full = (root / path).resolve()
    if not full.is_relative_to(root):
        return "Access denied: path escapes wiki root."
    if not full.exists():
        return f"File not found: {path}"
    return full.read_text(encoding="utf-8")


def write_skill_file(path: str, content: str, skill_root: str) -> str:
    """Write a file under the skill directory.

    Args:
        path: Path relative to *skill_root* (e.g. ``"SKILL.md"`` or
            ``"references/methodology.md"``). Absolute paths and ``..``
            traversal are rejected.
        content: File contents.
        skill_root: Absolute path to ``<kb>/output/skills/<name>``.
    """
    if path.startswith("/") or ".." in Path(path).parts:
        return "Access denied: only relative paths within the skill directory are allowed."
    root = Path(skill_root).resolve()
    full = (root / path).resolve()
    if not full.is_relative_to(root):
        return "Access denied: path escapes skill root."
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    return f"Written: {path}"
