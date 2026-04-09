"""Markdown renderers for PageIndex tree structures."""
from __future__ import annotations


def _yaml_frontmatter(source_name: str, doc_id: str) -> str:
    """Return a YAML frontmatter block for a PageIndex wiki page."""
    return (
        "---\n"
        f"source: {source_name}\n"
        "type: pageindex\n"
        f"doc_id: {doc_id}\n"
        "---\n"
    )



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

        lines.append(f"{heading_prefix} {title} (pages {start}\u2013{end})\n")
        if summary:
            lines.append(f"Summary: {summary}\n")
        if children:
            lines.append(_render_nodes_summary(children, depth + 1))

    return "\n".join(lines)



def render_summary_md(tree: dict, source_name: str, doc_id: str) -> str:
    """Render the summary Markdown page for a PageIndex tree.

    Identical structure to :func:`render_source_md` but replaces node text
    with ``Summary: {summary}`` for each node.
    """
    frontmatter = _yaml_frontmatter(source_name, doc_id)
    structure = tree.get("structure", [])
    body = _render_nodes_summary(structure, depth=1)
    return frontmatter + "\n" + body
