"""Plain wiki tool functions for the OpenKB agent.

These functions are intentionally NOT decorated with ``@function_tool`` here.
Decoration happens when building the agent so that the same functions can be
tested in isolation without requiring the openai-agents runtime.
"""
from __future__ import annotations

from pathlib import Path


def list_wiki_files(directory: str, wiki_root: str) -> str:
    """List all Markdown files in a wiki subdirectory.

    Args:
        directory: Subdirectory path relative to *wiki_root* (e.g. ``"sources"``).
        wiki_root: Absolute path to the wiki root directory.

    Returns:
        Newline-separated list of ``.md`` filenames found in *directory*,
        or ``"No files found."`` if the directory is empty or does not exist.
    """
    root = Path(wiki_root).resolve()
    target = (root / directory).resolve()
    if not target.is_relative_to(root):
        return "Access denied: path escapes wiki root."
    if not target.exists() or not target.is_dir():
        return "No files found."

    md_files = sorted(p.name for p in target.iterdir() if p.suffix == ".md")
    if not md_files:
        return "No files found."
    return "\n".join(md_files)


def read_wiki_file(path: str, wiki_root: str) -> str:
    """Read a Markdown file from the wiki.

    Args:
        path: File path relative to *wiki_root* (e.g. ``"sources/notes.md"``).
        wiki_root: Absolute path to the wiki root directory.

    Returns:
        File contents as a string, or ``"File not found: {path}"`` if missing.
    """
    root = Path(wiki_root).resolve()
    full_path = (root / path).resolve()
    if not full_path.is_relative_to(root):
        return "Access denied: path escapes wiki root."
    if not full_path.exists():
        return f"File not found: {path}"
    return full_path.read_text(encoding="utf-8")


def write_wiki_file(path: str, content: str, wiki_root: str) -> str:
    """Write or overwrite a Markdown file in the wiki.

    Parent directories are created automatically if they do not exist.

    Args:
        path: File path relative to *wiki_root* (e.g. ``"concepts/attention.md"``).
        content: Markdown content to write.
        wiki_root: Absolute path to the wiki root directory.

    Returns:
        ``"Written: {path}"`` on success.
    """
    root = Path(wiki_root).resolve()
    full_path = (root / path).resolve()
    if not full_path.is_relative_to(root):
        return "Access denied: path escapes wiki root."
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content, encoding="utf-8")
    return f"Written: {path}"


def write_wiki_files(files_json: str, wiki_root: str) -> str:
    """Write multiple Markdown files to the wiki in one call.

    Args:
        files_json: JSON array of objects, each with ``"path"`` and ``"content"`` keys.
            Example: ``[{"path": "concepts/foo.md", "content": "# Foo\\n..."}]``
        wiki_root: Absolute path to the wiki root directory.

    Returns:
        Summary of written files, or error message on failure.
    """
    import json

    try:
        files = json.loads(files_json)
    except json.JSONDecodeError as exc:
        return f"Invalid JSON: {exc}"

    if not isinstance(files, list):
        return "Expected a JSON array of {path, content} objects."

    root = Path(wiki_root).resolve()
    written: list[str] = []
    for entry in files:
        path = entry.get("path", "")
        content = entry.get("content", "")
        if not path:
            continue
        full_path = (root / path).resolve()
        if not full_path.is_relative_to(root):
            written.append(f"Skipped (path escape): {path}")
            continue
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")
        written.append(path)

    return f"Written {len(written)} files: {', '.join(written)}"
