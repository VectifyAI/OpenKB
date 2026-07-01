"""Path-scoped IO tools for the skill-create agent.

The skill-create agent runs with these capabilities:
  * READ wiki structure       — ``list_wiki_dir`` (delegates to
    ``openkb.agent.tools.list_wiki_files``)
  * READ wiki markdown        — ``read_wiki_file_for_skill`` (delegates to
    ``openkb.agent.tools.read_wiki_file``)
  * READ PageIndex source pages — ``get_skill_page_content`` (delegates to
    ``openkb.agent.tools.get_wiki_page_content``)
  * READ wiki images          — ``read_skill_image`` (delegates to
    ``openkb.agent.tools.read_wiki_image``)
  * WRITE under skill root    — ``write_skill_file``

The first four are thin wrappers around the canonical wiki tools in
``openkb/agent/tools.py`` so the skill agent traverses the wiki the same
way the query agent does — no separate retrieval semantics, no second
implementation to drift.

These helpers enforce write boundaries at the Python level — every write
resolves its target path, then verifies it stays inside the skill root.
Path traversal (``..``) and absolute paths are rejected outright.
"""

from __future__ import annotations

from pathlib import Path

from openkb.agent.tools import (
    get_wiki_page_content as _get_wiki_page_content,
)
from openkb.agent.tools import (
    list_wiki_files as _list_wiki_files,
)
from openkb.agent.tools import (
    read_wiki_file as _read_wiki_file,
)
from openkb.agent.tools import (
    read_wiki_image as _read_wiki_image,
)


def list_wiki_dir(directory: str, wiki_root: str) -> str:
    """List ``.md`` files in a wiki subdirectory.

    Thin wrapper around :func:`openkb.agent.tools.list_wiki_files`.

    Args:
        directory: Path relative to *wiki_root* (e.g. ``"concepts"``).
        wiki_root: Absolute path to ``<kb>/wiki``.
    """
    return _list_wiki_files(directory, wiki_root)


def read_wiki_file_for_skill(path: str, wiki_root: str) -> str:
    """Read a Markdown file from the wiki.

    Thin wrapper around :func:`openkb.agent.tools.read_wiki_file`.

    Args:
        path: File path relative to *wiki_root* (e.g. ``"concepts/attention.md"``).
        wiki_root: Absolute path to ``<kb>/wiki``.
    """
    return _read_wiki_file(path, wiki_root)


def get_skill_page_content(doc_name: str, pages: str, wiki_root: str) -> str:
    """Return formatted source pages from a PageIndex (long) document.

    Thin pass-through to :func:`openkb.agent.tools.get_wiki_page_content` so
    the skill agent shares the query agent's source-traversal semantics.
    """
    return _get_wiki_page_content(doc_name, pages, wiki_root)


def read_skill_image(path: str, wiki_root: str) -> dict:
    """Read a wiki image as a base64 data URL.

    Thin pass-through to :func:`openkb.agent.tools.read_wiki_image`. The
    caller decides whether to surface the result to the model as
    ``ToolOutputImage`` or ``ToolOutputText``.
    """
    return _read_wiki_image(path, wiki_root)


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
