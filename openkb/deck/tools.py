"""Path-scoped IO tools for the deck-create and deck-critic agents.

* WRITE under deck root  — ``write_deck_file``
* READ from deck root    — ``read_deck_file``

Wiki read tools (``list_wiki_dir``, ``read_wiki_file_for_skill``,
``get_skill_page_content``, ``read_skill_image``) are reused verbatim from
``openkb.skill.tools`` — no duplication. Importers should pull those
directly from there.

Write boundary is enforced at the Python level: every path resolves and
must stay inside the deck root. Absolute paths and ``..`` traversal are
rejected outright. Mirror of ``openkb/skill/tools.py::write_skill_file``.
"""
from __future__ import annotations

from pathlib import Path


def write_deck_file(path: str, content: str, deck_root: str) -> str:
    """Write a file under the deck directory.

    Args:
        path: Path relative to *deck_root* (e.g. ``"index.html"``).
            Absolute paths and ``..`` traversal are rejected.
        content: File contents.
        deck_root: Absolute path to ``<kb>/output/decks/<name>``.
    """
    if path.startswith("/") or ".." in Path(path).parts:
        return "Access denied: only relative paths within the deck directory are allowed."
    root = Path(deck_root).resolve()
    full = (root / path).resolve()
    if not full.is_relative_to(root):
        return "Access denied: path escapes deck root."
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    return f"Written: {path}"


def read_deck_file(path: str, deck_root: str) -> str:
    """Read a file from the deck directory.

    Used by the critic agent to re-read the draft ``index.html`` for
    revision — main's ``write_deck_file`` result string is "Written: X",
    not the HTML body, so critic needs an explicit re-read tool.

    Args:
        path: Path relative to *deck_root* (e.g. ``"index.html"``).
        deck_root: Absolute path to ``<kb>/output/decks/<name>``.
    """
    if path.startswith("/") or ".." in Path(path).parts:
        return "Access denied: only relative paths within the deck directory are allowed."
    root = Path(deck_root).resolve()
    full = (root / path).resolve()
    if not full.is_relative_to(root):
        return "Access denied: path escapes deck root."
    if not full.exists():
        return f"File not found: {path}"
    return full.read_text(encoding="utf-8")
