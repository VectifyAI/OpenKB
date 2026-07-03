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


def _build_page_images(pages: list[dict] | None) -> dict[int, list[str]]:
    """Map page number -> ordered, de-duplicated list of image paths.

    ``pages`` is the same per-page list written to ``wiki/sources/{doc}.json``
    (each item: ``{"page": int, "content": str, "images": [{"path": str}]}``).
    Extracted images live there and nowhere else reachable from the rendered
    summary — see ``_render_nodes_summary`` for why that alone left them
    invisible in the actual Obsidian vault.
    """
    page_images: dict[int, list[str]] = {}
    for page in pages or []:
        page_num_raw = page.get("page")
        if page_num_raw is None:
            continue
        try:
            page_num = int(page_num_raw)
        except (TypeError, ValueError):
            continue
        paths: list[str] = []
        for img in page.get("images") or []:
            path = img.get("path") if isinstance(img, dict) else None
            if isinstance(path, str):
                paths.append(path)
        if paths:
            page_images.setdefault(page_num, []).extend(paths)
    return page_images


def _render_nodes_summary(
    nodes: list[dict],
    depth: int,
    page_images: dict[int, list[str]],
    seen_images: set[str],
) -> str:
    """Recursively render nodes for the *summary* view (summaries only).

    A page's images are attached to whichever node covers that page first
    (``seen_images`` tracks paths already emitted), so a page split across
    many sibling nodes doesn't repeat the same figure at every one of them.
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

        new_images: list[str] = []
        try:
            page_range = range(int(start), int(end) + 1)
        except (TypeError, ValueError):
            page_range = range(0)
        for page_num in page_range:
            for path in page_images.get(page_num, []):
                if path not in seen_images:
                    seen_images.add(path)
                    new_images.append(path)
        for path in new_images:
            lines.append(f"![image]({path})\n")

        if summary:
            lines.append(f"Summary: {summary}\n")
        if children:
            lines.append(_render_nodes_summary(children, depth + 1, page_images, seen_images))

    return "\n".join(lines)


def render_summary_md(
    tree: dict,
    source_name: str,
    doc_id: str,
    description: str = "",
    pages: list[dict] | None = None,
) -> str:
    """Render the summary Markdown page for a PageIndex tree.

    Renders each node as a heading with page range and its summary text.
    Includes a YAML frontmatter block with ``type: "Summary"`` and an
    optional ``description`` field. ``pages`` (the same per-page list written
    to ``wiki/sources/{doc}.json``) is used to embed each node's page-range
    images inline — without it, extracted images are only ever referenced
    from that raw JSON file, never from anything the Obsidian vault renders.
    """
    frontmatter = _yaml_frontmatter(source_name, doc_id, description)
    structure = tree.get("structure", [])
    page_images = _build_page_images(pages)
    body = _render_nodes_summary(structure, depth=1, page_images=page_images, seen_images=set())
    return frontmatter + "\n" + body
