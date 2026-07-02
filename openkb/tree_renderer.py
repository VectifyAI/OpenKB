"""Markdown renderers for PageIndex tree structures."""

from __future__ import annotations

import re

from openkb import frontmatter

# PageIndex's own include_text extraction embeds image links into its private
# cache (.openkb/files/{doc_id}/images/...), not wiki/sources/images/ where
# OpenKB's own page extraction saves them — those paths don't resolve from a
# wiki page and aren't part of the Obsidian vault. Strip them from the quoted
# source text; the real per-page images are referenced from
# wiki/sources/{doc_name}.json instead.
_PAGEINDEX_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)\n?")


def _strip_internal_image_refs(text: str) -> str:
    return _PAGEINDEX_IMAGE_RE.sub("", text)


def _quote_block(text: str) -> str:
    """Render ``text`` as a Markdown blockquote, one ``>`` per line."""
    return "\n".join(f"> {line}" if line else ">" for line in text.strip().splitlines())


def _yaml_frontmatter(source_name: str, doc_id: str, description: str = "") -> str:
    """Return a YAML frontmatter block for a PageIndex wiki page."""
    lines = [frontmatter.kv_line("type", "Summary")]
    if description:
        lines.append(frontmatter.kv_line("description", description))
    lines.append("doc_type: pageindex")
    lines.append(frontmatter.kv_line("full_text", f"sources/{source_name}.json"))
    return "---\n" + "\n".join(lines) + "\n---\n"


def _render_nodes_summary(nodes: list[dict], depth: int) -> str:
    """Recursively render nodes for the *summary* view (summary + source text)."""
    lines: list[str] = []
    heading_prefix = "#" * min(depth, 6)
    for node in nodes:
        title = node.get("title", "")
        start = node.get("start_index", "")
        end = node.get("end_index", "")
        summary = node.get("summary", "")
        text = node.get("text", "")
        children = node.get("nodes", [])

        lines.append(f"{heading_prefix} {title} (pages {start}–{end})\n")
        if summary:
            lines.append(f"Summary: {summary}\n")
        stripped_text = _strip_internal_image_refs(text).strip() if text else ""
        if stripped_text:
            lines.append("Source text:\n")
            lines.append(_quote_block(stripped_text) + "\n")
        if children:
            lines.append(_render_nodes_summary(children, depth + 1))

    return "\n".join(lines)


def render_summary_md(tree: dict, source_name: str, doc_id: str, description: str = "") -> str:
    """Render the summary Markdown page for a PageIndex tree.

    Renders each node as a heading with page range and its summary text.
    Includes a YAML frontmatter block with ``type: "Summary"`` and an
    optional ``description`` field.
    """
    frontmatter = _yaml_frontmatter(source_name, doc_id, description)
    structure = tree.get("structure", [])
    body = _render_nodes_summary(structure, depth=1)
    return frontmatter + "\n" + body
