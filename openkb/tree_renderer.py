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


def _render_nodes_summary(nodes: list[dict], depth: int, seen: dict[str, str]) -> str:
    """Recursively render nodes for the *summary* view (summaries only).

    Nodes on the same source page can be handed byte-identical text by the
    underlying PageIndex tree (whole-page text slicing, not a finer offset —
    see PageIndex#340), which then produces duplicate LLM summaries. This
    isn't confined to parent/child pairs — siblings and cousins anywhere
    earlier in the document can collide too — so ``seen`` maps every summary
    already rendered to the title that first produced it, and any later node
    with the same summary is rendered as a short pointer back to that title
    instead of repeating the block.
    """
    lines: list[str] = []
    heading_prefix = "#" * min(depth, 6)
    for node in nodes:
        title = node.get("title", "")
        start = node.get("start_index", "")
        end = node.get("end_index", "")
        summary = node.get("summary", "")
        children = node.get("nodes", [])

        lines.append(f"{heading_prefix} {title} (pages {start}–{end})\n")

        first_seen_title = seen.get(summary) if summary else None
        if first_seen_title is not None:
            lines.append(f'_(same content as "{first_seen_title}" above)_\n')
        elif summary:
            lines.append(f"Summary: {summary}\n")
            seen[summary] = title

        if children:
            lines.append(_render_nodes_summary(children, depth + 1, seen))

    return "\n".join(lines)


def render_summary_md(tree: dict, source_name: str, doc_id: str, description: str = "") -> str:
    """Render the summary Markdown page for a PageIndex tree.

    Renders each node as a heading with page range and its summary text.
    Includes a YAML frontmatter block with ``type: "Summary"`` and an
    optional ``description`` field.
    """
    frontmatter = _yaml_frontmatter(source_name, doc_id, description)
    structure = tree.get("structure", [])
    body = _render_nodes_summary(structure, depth=1, seen={})
    return frontmatter + "\n" + body
