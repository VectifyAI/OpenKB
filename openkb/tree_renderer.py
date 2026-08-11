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


def _image_per_page(pages: list[dict] | None) -> dict[int, list[str]]:
    """Map 1-based page number -> list of wiki-root-relative image paths.

    ``pages`` is the per-page list written to ``wiki/sources/<doc>.json`` (each
    item has a 1-based ``page`` and an ``images`` list of ``{"path": ...}``
    dicts whose paths are wiki-root-relative like
    ``sources/images/<doc>/p1_img1.png``). Returns a dict keyed by page number
    for O(1) lookup while rendering nodes.
    """
    if not pages:
        return {}
    per_page: dict[int, list[str]] = {}
    for item in pages:
        page = item.get("page")
        if not isinstance(page, int) or page < 1:
            continue
        paths = [
            img["path"]
            for img in item.get("images", [])
            if isinstance(img, dict) and isinstance(img.get("path"), str)
        ]
        if paths:
            per_page.setdefault(page, []).extend(paths)
    return per_page


def _summary_relative_path(wiki_root_path: str) -> str:
    """Rewrite a wiki-root-relative image path for a page under ``wiki/summaries/``.

    Image paths in the per-page JSON are wiki-root-relative
    (``sources/images/<doc>/file.png``). The summary lives one directory deeper
    (``wiki/summaries/<doc>.md``), so the path that resolves for Obsidian /
    GitHub is ``../`` + the wiki-root-relative path.
    """
    return f"../{wiki_root_path}" if wiki_root_path else ""


def _render_nodes_summary(
    nodes: list[dict],
    depth: int,
    per_page_images: dict[int, list[str]] | None = None,
    emitted: set[str] | None = None,
) -> str:
    """Recursively render nodes for the *summary* view (summaries only).

    When ``per_page_images`` is provided, each node's page range embeds the
    images extracted from those pages (as ``![...](../sources/images/...)``
    links), skirting the PageIndex private-cache refs that are stripped from
    node text. ``emitted`` tracks already-rendered paths so a figure spanning
    pages covered by several sibling nodes is only shown once.
    """
    if per_page_images is None:
        per_page_images = {}
    if emitted is None:
        emitted = set()

    lines: list[str] = []
    heading_prefix = "#" * min(depth, 6)
    for node in nodes:
        title = node.get("title", "")
        start = node.get("start_index", "")
        end = node.get("end_index", "")
        summary = node.get("summary", "")
        children = node.get("nodes", [])

        lines.append(f"{heading_prefix} {title} (pages {start}–{end})\n")

        # Embed figures for the node's page range. Node indices are 0-based
        # page indices; the per-page image map is keyed by 1-based page number.
        node_images: list[str] = []
        if isinstance(start, int) and isinstance(end, int):
            lo, hi = start + 1, end + 1
            for page_num in range(lo, hi + 1):
                for path in per_page_images.get(page_num, []):
                    if path not in emitted:
                        emitted.add(path)
                        node_images.append(_summary_relative_path(path))
        for img_path in node_images:
            lines.append(f"![image]({img_path})\n")

        if summary:
            lines.append(f"Summary: {summary}\n")
        if children:
            lines.append(_render_nodes_summary(children, depth + 1, per_page_images, emitted))

    return "\n".join(lines)


def render_summary_md(
    tree: dict,
    source_name: str,
    doc_id: str,
    description: str = "",
    pages: list[dict] | None = None,
) -> str:
    """Render the summary Markdown page for a PageIndex tree.

    Renders each node as a heading with page range and its summary text, and
    embeds the page images (when ``pages`` is supplied). Includes a YAML
    frontmatter block with ``type: "Summary"`` and an optional ``description``
    field.
    """
    frontmatter_block = _yaml_frontmatter(source_name, doc_id, description)
    structure = tree.get("structure", [])
    body = _render_nodes_summary(structure, depth=1, per_page_images=_image_per_page(pages))
    return frontmatter_block + "\n" + body
