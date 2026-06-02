"""Plain wiki tool functions for the OpenKB agent.

These functions are intentionally NOT decorated with ``@function_tool`` here.
Decoration happens when building the agent so that the same functions can be
tested in isolation without requiring the openai-agents runtime.
"""
from __future__ import annotations

import contextlib
import functools
import json as _json
import os
import shutil
import subprocess
from pathlib import Path

from openkb.schema import EXCLUDED_WIKI_FILES

# grep_wiki_files tuning
_GREP_MAX_LINES = 50
_GREP_TIMEOUT_S = 10


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


@functools.cache
def _grep_binary() -> str | None:
    """Locate the system grep once per process (PATH does not change at runtime)."""
    return shutil.which("grep")


def grep_wiki_files(
    pattern: str,
    wiki_root: str,
    *,
    ignore_case: bool = True,
    fixed_string: bool = False,
) -> str:
    """Lexically search the wiki's markdown layer for ``pattern`` using grep.

    A completeness sweep over every ``*.md`` file under *wiki_root* —
    summaries, concepts, entities, explorations, ``index.md``, and short-doc
    ``sources/*.md``. Long-doc per-page ``*.json`` (PageIndex's domain) is
    excluded (only ``*.md`` is searched), as are the wiki's bookkeeping /
    scaffolding files (``log.md``, ``AGENTS.md``, ``SCHEMA.md`` — see
    :data:`openkb.schema.EXCLUDED_WIKI_FILES`).

    Shells out to the system ``grep`` (POSIX, ubiquitous on macOS/Linux) with
    ``shell=False``, so a hostile *pattern* cannot inject commands. ``pattern``
    is an **extended** regular expression (ERE) by default — alternation
    ``a|b``, ``?``, ``+``, ``()`` all work — or a literal string when
    *fixed_string* is True.

    Args:
        pattern: Search pattern. ERE by default; literal when *fixed_string*.
        wiki_root: Absolute path to the wiki root directory.
        ignore_case: Case-insensitive match (default True).
        fixed_string: Treat *pattern* as a literal string, not a regex.

    Returns:
        Up to :data:`_GREP_MAX_LINES` matches, each line ``relative/path.md:LINE:text``
        (the path is everything before the first colon), plus a truncation
        notice if capped. On empty pattern / no match / missing grep / timeout /
        error-with-no-results, returns an explicit message string. Never raises.
    """
    if not pattern or not pattern.strip():
        return "Provide a non-empty search pattern."

    root = Path(wiki_root).resolve()
    if not root.exists():
        return f"Wiki root not found: {wiki_root}"

    grep = _grep_binary()
    if not grep:
        return "grep unavailable on this system."

    cmd = [grep, "-rn", "--include=*.md", "--exclude-dir=images", "--exclude-dir=.git"]
    for name in sorted(EXCLUDED_WIKI_FILES):
        cmd.append(f"--exclude={name}")
    if ignore_case:
        cmd.append("-i")
    cmd.append("-F" if fixed_string else "-E")
    cmd += ["-e", pattern, str(root)]

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, errors="replace",
            timeout=_GREP_TIMEOUT_S, check=False,
        )
    except subprocess.TimeoutExpired:
        return "grep timed out; narrow the pattern."

    prefix = str(root) + os.sep
    results: list[str] = []
    for line in proc.stdout.splitlines():
        if not line:
            continue
        if not line.startswith(prefix):
            continue  # defensive: only surface paths under wiki_root
        rel = line[len(prefix):]
        path_part = rel.split(":", 1)[0]
        # Defense in depth: --exclude already drops these basenames; this also
        # catches a same-named file in a subdirectory.
        if Path(path_part).name in EXCLUDED_WIKI_FILES:
            continue
        results.append(rel)
        if len(results) > _GREP_MAX_LINES:
            break  # only need 51 to detect truncation; stop processing

    if not results:
        # grep exit codes: 0 = match, 1 = no match, >=2 = error. grep can exit
        # >=2 (e.g. one unreadable file) while still printing valid matches —
        # those were collected above. Only report an error when nothing usable
        # came back.
        if proc.returncode >= 2:
            stderr_lines = (proc.stderr or "").strip().splitlines()
            first = stderr_lines[0] if stderr_lines else "unknown error"
            return f"grep error: {first}."
        return f"No matches for {pattern}."

    truncated = len(results) > _GREP_MAX_LINES
    out = "\n".join(results[:_GREP_MAX_LINES])
    if truncated:
        out += "\n… more matches; narrow the pattern."
    return out


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
        return (
            "Access denied: path must be under wiki/, output/, or skills/."
        )
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
    allowed = (
        len(parts) >= 3 and parts[0] == "wiki" and parts[1] == "explorations"
    ) or (
        len(parts) >= 2 and parts[0] == "output"
    )
    if not allowed:
        return (
            "Access denied: path must be a file under "
            "wiki/explorations/ or output/."
        )
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content, encoding="utf-8")
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
    full_path.write_text(content, encoding="utf-8")
    return f"Written: {path}"

