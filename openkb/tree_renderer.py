"""Markdown renderers for PageIndex tree structures."""

from __future__ import annotations

from openkb import frontmatter


def _yaml_frontmatter(source_name: str, doc_id: str, description: str = "") -> str:
    """Return a YAML frontmatter block for a PageIndex wiki page."""
    lines = [frontmatter.kv_line("type", "Summary")]
    if description:
        lines.append(frontmatter.kv_line("description", description))
    lines.append("doc_type: pageindex")
    lines.append(frontmatter.kv_line("full_text", f"sources/{source_name}.json"))
    return "---\n" + "\n".join(lines) + "\n---\n"


def _render_nodes_summary(nodes: list[dict], depth: int) -> str:
    """Recursively render nodes for the *summary* view (summaries only)."""
    lines: list[str] = []
    heading_prefix = "#" * min(depth, 6)
    for node in nodes:
        title = node.get("title", "")
        start = node.get("start_index", "")
        end = node.get("end_index", "")
        summary = node.get("summary", "")
        children = node.get("nodes", [])

        lines.append(f"{heading_prefix} {title} (pages {start}–{end})\n")
        if summary:
            lines.append(f"Summary: {summary}\n")
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
