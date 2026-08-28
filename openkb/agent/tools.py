"""Plain wiki tool functions for the OpenKB agent.

These functions are intentionally NOT decorated with ``@function_tool`` here.
Decoration happens when building the agent so that the same functions can be
tested in isolation without requiring the openai-agents runtime.
"""

from __future__ import annotations

import contextlib
import json as _json
from pathlib import Path, PurePosixPath

from openkb.locks import atomic_write_text


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
            # Silently skip malformed segments — parse_pages is a tolerant
            # parser by design (user-supplied page specs may contain typos).
            with contextlib.suppress(ValueError):
                if len(segments) == 2:
                    start, end = int(segments[0]), int(segments[1])
                    result.update(range(start, end + 1))
                elif len(segments) == 3 and segments[0] == "":
                    # e.g. "-1" split gives ['', '1']
                    result.add(-int(segments[1]))
                # More complex cases (e.g. negative range) are ignored.
        else:
            with contextlib.suppress(ValueError):
                result.add(int(part))
    return sorted(n for n in result if n > 0)


def get_wiki_page_content(doc_name: str, pages: str, wiki_root: str) -> str:
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


def search_wiki(query: str, wiki_root: str, top_k: int = 5) -> str:
    """Full-text (BM25) search over concepts/entities/summaries wiki pages.

    Hybrid retrieval helper: complements index.md-driven navigation by
    surfacing pages whose one-line index summary doesn't mention a specific
    buried detail the query is looking for (a niche term, a figure, an exact
    fact). Additive — use alongside, not instead of, index.md navigation.

    Args:
        query: Free-text search query (keywords or a natural-language question).
        wiki_root: Absolute path to the wiki root directory.
        top_k: Maximum number of ranked results to return.

    Returns:
        A formatted, ranked list of page hits (wikilink, title, snippet), or
        a message indicating no matches were found.
    """
    from openkb.fulltext_index import WikiFullTextIndex

    hits = WikiFullTextIndex(wiki_root).search(query, top_k=top_k)
    if not hits:
        return "No matching pages found."

    lines = []
    for i, hit in enumerate(hits, start=1):
        wikilink = hit.path[:-3] if hit.path.endswith(".md") else hit.path
        lines.append(f"{i}. [[{wikilink}]] — {hit.title} (score: {hit.score})\n   {hit.snippet}")
    return "\n".join(lines)


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
        path: Image path relative to *wiki_root*
            (e.g. ``"sources/images/doc/p1_img1.png"``), or note-relative
            as embedded in sources/ .md pages
            (``"images/doc/p1_img1.png"`` — retried under ``sources/``).
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
        # Source .md pages embed images note-relative ("images/<doc>/<file>",
        # resolved from wiki/sources/). Callers pass those verbatim — retry
        # under sources/ before failing.
        alt_path = (root / "sources" / path).resolve()
        if not (alt_path.is_relative_to(root) and alt_path.exists()):
            return {"type": "text", "text": f"Image not found: {path}"}
        full_path = alt_path

    mime = _MIME_TYPES.get(full_path.suffix.lower(), "image/png")
    b64 = base64.b64encode(full_path.read_bytes()).decode()
    return {"type": "image", "image_url": f"data:{mime};base64,{b64}"}


def read_kb_file(path: str, kb_root: str) -> str:
    """Read a text file from the KB, restricted to safe read zones.

    Allowed prefixes (relative to *kb_root*):
      * ``wiki/**``    — compiled wiki content (full read access).
      * ``output/**``  — generated artifacts (decks, skills, etc.) for
        critic / iteration workflows.
      * ``skills/**``  — locally installed skills' bodies.

    Args:
        path: File path relative to *kb_root*.
        kb_root: Absolute path to the KB root directory.

    Returns:
        File content on success, or an access-denied / not-found message.
    """
    if not path:
        return "Access denied: empty path."
    root = Path(kb_root).resolve()
    full_path = (root / path).resolve()
    if not full_path.is_relative_to(root):
        return "Access denied: path escapes KB root."
    rel = full_path.relative_to(root)
    if not rel.parts:
        return "Access denied: KB root itself is not readable."
    if rel.parts[0] not in ("wiki", "output", "skills"):
        return "Access denied: path must be under wiki/, output/, or skills/."
    if not full_path.is_file():
        return f"File not found: {path}"
    return full_path.read_text(encoding="utf-8", errors="replace")


def write_kb_file(path: str, content: str, kb_root: str) -> str:
    """Write a text file under the KB, restricted to safe write zones.

    Allowed prefixes (relative to *kb_root*):
      * ``wiki/explorations/**`` — user-saved chat transcripts and notes.
      * ``output/**``            — generator artifacts (skills, etc.) the
        user iterates on via natural-language chat follow-ups.

    Parent directories are created automatically. Any path outside the
    allow-list is rejected.

    Args:
        path: File path relative to *kb_root*.
        content: Text content to write.
        kb_root: Absolute path to the KB root directory.

    Returns:
        ``"Written: {path}"`` on success, or an access-denied message.
    """
    if not path:
        return "Access denied: path must be a file under wiki/explorations/ or output/."
    root = Path(kb_root).resolve()
    full_path = (root / path).resolve()
    if not full_path.is_relative_to(root):
        return "Access denied: path escapes KB root."
    rel = full_path.relative_to(root)
    parts = rel.parts
    # Require a file path with at least one component beyond the allow-list
    # prefix, so a bare directory name (e.g. "output") does not slip through
    # and crash on write_text with IsADirectoryError.
    allowed = (len(parts) >= 3 and parts[0] == "wiki" and parts[1] == "explorations") or (
        len(parts) >= 2 and parts[0] == "output"
    )
    if not allowed:
        return "Access denied: path must be a file under wiki/explorations/ or output/."
    full_path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic temp-file + os.replace rename (openkb.locks): a crash/interleave
    # mid-write can never leave a truncated page for a concurrent lint/recompile
    # scan to read, and this satisfies the AGENTS.md "wiki writes go through
    # locks.py" invariant. (No per-turn KB mutation lock — that would serialize
    # every chat turn; atomic rename alone resolves the torn-file hazard.)
    atomic_write_text(full_path, content)
    return f"Written: {path}"


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
    # Atomic temp-file + os.replace rename (openkb.locks): reached from the
    # write-capable REST /chat agent, a plain write_text could leave a torn
    # page for a concurrent lint/recompile scan — the same hazard write_kb_file
    # already closes. (See write_kb_file above.)
    atomic_write_text(full_path, content)
    return f"Written: {path}"


_VIEWABLE_ARTIFACT_SUFFIXES = (".html", ".htm")


def artifact_event_from_write(name: str, arguments: str, output: str) -> dict | None:
    """Return the ``artifact`` SSE payload for a completed ``write_file`` call
    that produced a viewable artifact under ``output/``, else ``None``.

    Gated so it fires ONLY for a *successful* ``write_file`` (``write_kb_file``
    returns ``"Written: {path}"`` on success) of a viewable ``.html``/``.htm``
    file under the sanctioned ``output/`` zone — the same zone ``write_kb_file``
    itself allows. Every other case (a read tool, a failed/denied write, a
    non-html path, a non-output zone, malformed arguments) returns ``None`` so
    no card is ever painted for a file that isn't a viewable artifact on disk.
    """
    if name != "write_file":
        return None
    if not isinstance(output, str) or not output.startswith("Written:"):
        return None
    try:
        parsed = _json.loads(arguments)
        path = str(parsed.get("path", "")).strip()
    except (ValueError, TypeError, AttributeError):
        return None
    if not path:
        return None
    rel = PurePosixPath(path.replace("\\", "/"))
    if not rel.parts or rel.parts[0] != "output":
        return None
    if rel.suffix.lower() not in _VIEWABLE_ARTIFACT_SUFFIXES:
        return None
    return {"kind": "file", "path": path, "name": rel.name}
