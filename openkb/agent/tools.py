"""Plain wiki tool functions for the OpenKB agent.

These functions are intentionally NOT decorated with ``@function_tool`` here.
Decoration happens when building the agent so that the same functions can be
tested in isolation without requiring the openai-agents runtime.
"""
from __future__ import annotations

import json as _json
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


def parse_pages(pages: str) -> list[int]:
    """Parse a page specification string into a sorted, deduplicated list of page numbers.

    Args:
        pages: Page spec such as ``"3-5,7,10-12"``.

    Returns:
        Sorted list of positive page numbers, e.g. ``[3, 4, 5, 7, 10, 11, 12]``.
    """
    result: set[int] = set()
    for part in pages.split(","):
        part = part.strip()
        if "-" in part:
            # Handle ranges like "3-5"; also handle negative numbers by only
            # splitting on the first "-" that follows a digit.
            segments = part.split("-")
            # Re-join to handle leading negatives: segments[0] may be empty
            # if part starts with "-".  We just try to parse start/end.
            try:
                if len(segments) == 2:
                    start, end = int(segments[0]), int(segments[1])
                    result.update(range(start, end + 1))
                elif len(segments) == 3 and segments[0] == "":
                    # e.g. "-1" split gives ['', '1']
                    result.add(-int(segments[1]))
                # More complex cases (e.g. negative range) are ignored.
            except ValueError:
                pass
        else:
            try:
                result.add(int(part))
            except ValueError:
                pass
    return sorted(n for n in result if n > 0)


def get_page_content(doc_name: str, pages: str, wiki_root: str) -> str:
    """Return formatted content for specified pages of a document.

    Reads ``{wiki_root}/sources/{doc_name}.json`` which must be a JSON array of
    objects with at least ``{"page": int, "content": str}`` fields and an
    optional ``"images"`` list of ``{"path": str, ...}`` objects.

    Args:
        doc_name: Document name without extension (e.g. ``"paper"``).
        pages: Page specification string (e.g. ``"1-3,7"``).
        wiki_root: Absolute path to the wiki root directory.

    Returns:
        Formatted page content, or an error message string.
    """
    root = Path(wiki_root).resolve()
    target = (root / "sources" / f"{doc_name}.json").resolve()
    if not target.is_relative_to(root):
        return "Access denied: path escapes wiki root."
    if not target.exists():
        return f"File not found: sources/{doc_name}.json"

    data = _json.loads(target.read_text(encoding="utf-8"))
    requested = set(parse_pages(pages))
    matches = [entry for entry in data if entry.get("page") in requested]

    if not matches:
        return f"No content found for pages {pages} in {doc_name}."

    parts: list[str] = []
    for entry in matches:
        page_num = entry["page"]
        content = entry.get("content", "")
        block = f"[Page {page_num}]\n{content}"
        images = entry.get("images")
        if images:
            paths = ", ".join(img["path"] for img in images if "path" in img)
            if paths:
                block += f"\n[Images: {paths}]"
        parts.append(block)

    return "\n\n".join(parts) + "\n\n"


_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


def read_wiki_image(path: str, wiki_root: str) -> dict:
    """Read an image file from the wiki and return as base64 data URL.

    Args:
        path: Image path relative to *wiki_root* (e.g. ``"sources/images/doc/p1_img1.png"``).
        wiki_root: Absolute path to the wiki root directory.

    Returns:
        A dict with ``type``, ``image_url`` keys for ``ToolOutputImage``,
        or a dict with ``type``, ``text`` keys on error.
    """
    import base64

    root = Path(wiki_root).resolve()
    full_path = (root / path).resolve()
    if not full_path.is_relative_to(root):
        return {"type": "text", "text": "Access denied: path escapes wiki root."}
    if not full_path.exists():
        return {"type": "text", "text": f"Image not found: {path}"}

    mime = _MIME_TYPES.get(full_path.suffix.lower(), "image/png")
    b64 = base64.b64encode(full_path.read_bytes()).decode()
    return {"type": "image", "image_url": f"data:{mime};base64,{b64}"}


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

