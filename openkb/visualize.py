"""Render the wiki's [[wikilink]] graph as a self-contained interactive HTML page."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from openkb import frontmatter
from openkb.lint import _extract_wikilinks, _normalize_target
from openkb.schema import PAGE_CONTENT_DIRS

# Singular display type per content dir; falls back to a derived name for any
# dir not listed (so a new PAGE_CONTENT_DIRS entry never KeyErrors here).
_DIR_TYPE = {"summaries": "Summary", "concepts": "Concept", "entities": "Entity"}


def _type_for_dir(sub: str) -> str:
    return _DIR_TYPE.get(sub) or (sub[:-1] if sub.endswith("s") else sub).capitalize() or sub


def build_graph(wiki_dir: Path) -> dict:
    """Collect nodes (pages), directed edges (wikilinks), and the set of types."""
    nodes: dict[str, dict] = {}
    texts: dict[str, str] = {}  # nid -> file text, read once and reused for edges
    for sub in PAGE_CONTENT_DIRS:
        d = wiki_dir / sub
        if not d.exists():
            continue
        for p in sorted(d.glob("*.md")):
            nid = f"{sub}/{p.stem}"
            text = p.read_text(encoding="utf-8")
            texts[nid] = text
            fm = frontmatter.parse(text)
            t = fm.get("type")
            t = t.strip() if isinstance(t, str) and t.strip() else _type_for_dir(sub)
            desc = fm.get("description")
            desc = desc.strip() if isinstance(desc, str) else ""
            srcs = fm.get("sources")
            srcs = [str(s) for s in srcs] if isinstance(srcs, list) else []
            ft = fm.get(
                "full_text"
            )  # summaries record their origin document here, not in `sources`
            if isinstance(ft, str) and ft.strip():
                srcs.insert(0, ft.strip())
            nodes[nid] = {
                "id": nid,
                "label": p.stem,
                "type": t,
                "description": desc,
                "sources": srcs,
                "out": 0,
                "in": 0,
            }

    norm = {_normalize_target(nid): nid for nid in nodes}
    edges: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for src, text in texts.items():
        for raw in _extract_wikilinks(text):
            tgt = norm.get(_normalize_target(raw))
            if not tgt or tgt == src or (src, tgt) in seen:
                continue
            seen.add((src, tgt))
            edges.append({"source": src, "target": tgt})
            nodes[src]["out"] += 1
            nodes[tgt]["in"] += 1

    types = sorted({n["type"] for n in nodes.values()})
    return {"nodes": list(nodes.values()), "edges": edges, "types": types}


def render_html(graph: dict) -> str:
    """Inject the graph as JSON into the self-contained HTML template."""
    template = (
        resources.files("openkb").joinpath("templates/graph.html").read_text(encoding="utf-8")
    )
    data = json.dumps(graph, ensure_ascii=False).replace("</", "<\\/")  # avoid </script> breakout
    return template.replace("__GRAPH_DATA__", data)
