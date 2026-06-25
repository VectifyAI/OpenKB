"""Shared helpers for the YAML frontmatter blocks of OpenKB wiki pages.

Single source of truth for building, splitting, parsing, and mutating the
``---`` frontmatter used by summaries / concepts / entities. The closing
delimiter is always matched at the start of a line (``\\n---``), so a ``---``
that appears inside a quoted value never truncates the block — the failure
mode that ad-hoc ``text.find("---", 3)`` parsing was prone to.
"""
from __future__ import annotations

import json
import re

import yaml


def kv_line(key: str, value: str) -> str:
    """Render ``key: "value"`` with the value JSON-quoted (always single-line).

    JSON strings are a strict subset of YAML: always single-line, always
    correctly escaped (newlines, quotes, colons, control chars), and never
    auto-promoted to a block scalar.
    """
    return f"{key}: {json.dumps(value, ensure_ascii=False)}"


def list_line(key: str, items) -> str:
    """Render ``key: ["a", "b"]`` as JSON-style YAML (always single-line)."""
    return f"{key}: {json.dumps(list(items), ensure_ascii=False)}"


def block(lines: list[str]) -> str:
    """Assemble a complete frontmatter block (with delimiters + trailing blank)."""
    return "---\n" + "\n".join(lines) + "\n---\n\n"


def parse_list_value(line: str) -> list[str] | None:
    """Parse the right-hand side of ``key: [...]`` into a list of strings.

    Returns ``None`` when the value cannot be interpreted as a list — callers
    treat that as "leave the frontmatter alone".
    """
    colon = line.find(":")
    if colon == -1:
        return None
    try:
        parsed = yaml.safe_load(line[colon + 1:])
    except yaml.YAMLError:
        return None
    if not isinstance(parsed, list):
        return None
    return [str(x) for x in parsed]


def split(text: str) -> tuple[str, str] | None:
    """Split ``text`` into ``(frontmatter_block, body)``.

    ``frontmatter_block`` includes both ``---`` delimiters and the newline that
    ends the closing delimiter line; ``body`` is everything after, so
    ``frontmatter_block + body == text`` exactly (lossless).

    Returns ``None`` when ``text`` has no well-formed frontmatter: no leading
    ``---`` or no line-anchored closing ``---``. Because the closing delimiter
    must start a line (``\\n---``), a ``---`` inside a quoted value is ignored.
    """
    if not text.startswith("---"):
        return None
    nl = text.find("\n---", 3)
    if nl == -1:
        return None
    after = text.find("\n", nl + 1)  # newline ending the closing '---' line
    if after == -1:
        return text, ""
    return text[:after + 1], text[after + 1:]


def parse(text: str) -> dict:
    """Return the frontmatter as a dict (``{}`` when absent or malformed)."""
    parts = split(text)
    if parts is None:
        return {}
    fm_block = parts[0]
    inner = fm_block[3:]                 # drop opening '---'
    close = inner.rfind("\n---")        # drop closing '---' line
    if close != -1:
        inner = inner[:close]
    try:
        data = yaml.safe_load(inner)
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def set_line(fm_block: str, key: str, value: str) -> str:
    """Set or insert a single scalar ``key:`` line in a frontmatter block.

    Replaces an existing line for ``key``; otherwise inserts it right after the
    opening ``---``. A lambda replacement is used so values containing regex
    backrefs (``\\1``, ``\\g<…>``) are inserted literally.
    """
    line = kv_line(key, value)
    if re.search(rf"^{re.escape(key)}:", fm_block, flags=re.MULTILINE):
        return re.sub(rf"^{re.escape(key)}:.*", lambda _m: line, fm_block,
                      count=1, flags=re.MULTILINE)
    return fm_block.replace("---\n", f"---\n{line}\n", 1)


def drop_line(fm_block: str, key: str) -> str:
    """Remove any ``key:`` line from a frontmatter block (no-op if absent)."""
    return re.sub(rf"^{re.escape(key)}:.*\n?", "", fm_block, flags=re.MULTILINE)
