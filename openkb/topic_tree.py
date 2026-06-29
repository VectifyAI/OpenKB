"""Generic hierarchical-index engine over a page collection.

A topic node is a directory containing a ``_topic.md`` (summary + size).
Children are derived from the directory: subdirectories are child topics,
``*.md`` files (except ``_topic.md``) are concept leaves. The POC wires
this to ``wiki/concepts/`` only; entities/documents can reuse it later by
passing different callables.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import yaml

from openkb.locks import atomic_write_text

FANOUT_K = 10
MAX_DEPTH = 6
TOPIC_FILE = "_topic.md"


@dataclass
class TopicNodeView:
    summary: str
    child_topics: list[tuple[str, str]] = field(default_factory=list)  # (name, summary)
    child_concepts: list[tuple[str, str]] = field(default_factory=list)  # (stem, brief)


def _frontmatter(md: Path) -> dict:
    if not md.is_file():
        return {}
    text = md.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return {}
    try:
        data = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _brief(concept_md: Path) -> str:
    return str(_frontmatter(concept_md).get("description", "")).strip()


def write_topic_md(node_dir: Path, summary: str, size: int) -> None:
    node_dir.mkdir(parents=True, exist_ok=True)
    # Dump the frontmatter as a mapping (not a bare scalar) so PyYAML never
    # emits a ``...`` document-end marker that would corrupt the block, and
    # multi-line summaries are properly escaped/round-tripped.
    fm = yaml.safe_dump(
        {"type": "topic", "summary": summary, "size": int(size)},
        sort_keys=False,
        allow_unicode=True,
    ).strip()
    body = f"---\n{fm}\n---\n\n# {node_dir.name or 'root'}\n\n{summary}\n"
    atomic_write_text(node_dir / TOPIC_FILE, body)


def child_count(node_dir: Path) -> int:
    subtopics = [d for d in node_dir.iterdir() if d.is_dir()]
    concepts = [f for f in node_dir.glob("*.md") if f.name != TOPIC_FILE]
    return len(subtopics) + len(concepts)


def read_topic(concepts_root: Path, rel: str = "") -> TopicNodeView:
    node_dir = concepts_root if not rel else concepts_root / rel
    summary = str(_frontmatter(node_dir / TOPIC_FILE).get("summary", "")).strip()
    child_topics: list[tuple[str, str]] = []
    child_concepts: list[tuple[str, str]] = []
    if node_dir.is_dir():
        for child in sorted(node_dir.iterdir()):
            if child.is_dir():
                sub_sum = str(_frontmatter(child / TOPIC_FILE).get("summary", "")).strip()
                child_topics.append((child.name, sub_sum))
            elif child.suffix == ".md" and child.name != TOPIC_FILE:
                child_concepts.append((child.stem, _brief(child)))
    return TopicNodeView(
        summary=summary, child_topics=child_topics, child_concepts=child_concepts
    )


ChooseFn = Callable[[TopicNodeView, str], Optional[str]]


def place_concept(
    concepts_root: Path,
    stem: str,
    brief: str,
    content: str,
    *,
    choose: ChooseFn,
    on_overflow: Optional[Callable[[Path], None]] = None,
) -> Path:
    """Descend from the root, letting ``choose`` pick a child topic at each
    level, until it returns None; drop the concept as a leaf there.

    Cost is O(depth) ``choose`` calls. ``on_overflow`` (if given) fires on
    the landing node when its direct-child count exceeds ``FANOUT_K``.
    """
    rel = ""
    for _ in range(MAX_DEPTH):
        view = read_topic(concepts_root, rel)
        pick = choose(view, brief)
        if pick is None:
            break
        if pick not in {t for t, _ in view.child_topics}:
            break  # choose returned a non-existent child; stop here defensively
        rel = f"{rel}/{pick}" if rel else pick
    node_dir = concepts_root if not rel else concepts_root / rel
    node_dir.mkdir(parents=True, exist_ok=True)
    path = node_dir / f"{stem}.md"
    atomic_write_text(path, content)
    if on_overflow is not None and child_count(node_dir) > FANOUT_K:
        on_overflow(node_dir)
    return path


ClusterFn = Callable[[list[tuple[str, str]]], dict[str, list[str]]]
SummarizeFn = Callable[[str, list[str]], str]


def split_node(node_dir: Path, *, cluster: ClusterFn, summarize: SummarizeFn) -> None:
    """Cluster a node's direct concept leaves into subtopics and move them in.

    Files are moved (``Path.replace``), not copied; because wikilinks resolve
    by bare stem, links to moved concepts keep resolving.
    """
    view = read_topic(node_dir.parent if node_dir.name else node_dir, node_dir.name)
    leaves = {stem: brief for stem, brief in view.child_concepts}
    if not leaves:
        return
    groups = cluster(list(leaves.items()))
    for sub_name, stems in groups.items():
        if not stems:
            continue
        sub_dir = node_dir / sub_name
        sub_dir.mkdir(parents=True, exist_ok=True)
        write_topic_md(
            sub_dir, summarize(sub_name, [leaves.get(s, "") for s in stems]), len(stems)
        )
        for stem in stems:
            src = node_dir / f"{stem}.md"
            if src.is_file():
                src.replace(sub_dir / f"{stem}.md")
    # refresh the split node's own summary/size
    new_view = read_topic(node_dir.parent if node_dir.name else node_dir, node_dir.name)
    size = len(new_view.child_topics) + len(new_view.child_concepts)
    write_topic_md(node_dir, view.summary or node_dir.name, size)


def place_topic_dir(concepts_root: Path, *, brief: str, choose: ChooseFn) -> Path:
    """Descend with ``choose`` and return the landing topic directory WITHOUT
    writing a concept file. Lets the caller own the concept-page format (e.g.
    the compiler's ``_write_concept``) while the tree owns placement."""
    rel = ""
    for _ in range(MAX_DEPTH):
        view = read_topic(concepts_root, rel)
        pick = choose(view, brief)
        if pick is None or pick not in {t for t, _ in view.child_topics}:
            break
        rel = f"{rel}/{pick}" if rel else pick
    node = concepts_root if not rel else concepts_root / rel
    node.mkdir(parents=True, exist_ok=True)
    return node


def _build_subtree(
    node_dir: Path,
    items: list[tuple[str, str, str]],  # (stem, brief, content)
    cluster: ClusterFn,
    summarize: SummarizeFn,
    depth: int,
) -> int:
    """Recursively build a topic subtree under ``node_dir`` (already created,
    with its ``_topic.md``). Clusters the full item set at this level, recurses
    into any group still larger than ``FANOUT_K``, and writes leaves otherwise."""
    if len(items) <= FANOUT_K or depth >= MAX_DEPTH:
        for stem, _brief_, content in items:
            atomic_write_text(node_dir / f"{stem}.md", content)
        return len(items)

    briefs = {stem: b for stem, b, _ in items}
    contents = {stem: c for stem, _, c in items}
    groups = cluster([(s, briefs[s]) for s in contents])

    placed = 0
    seen: set[str] = set()
    for name, stems in groups.items():
        kept = [s for s in stems if s in contents and s not in seen]
        if not kept:
            continue
        seen.update(kept)
        sub = node_dir / name
        sub.mkdir(parents=True, exist_ok=True)
        write_topic_md(sub, summarize(name, [briefs[s] for s in kept]), len(kept))
        placed += _build_subtree(
            sub, [(s, briefs[s], contents[s]) for s in kept], cluster, summarize, depth + 1
        )
    # Any concept the clusterer dropped stays as a leaf at this node.
    for s in [s for s in contents if s not in seen]:
        atomic_write_text(node_dir / f"{s}.md", contents[s])
        placed += 1
    return placed


def bootstrap(
    concepts_root: Path,
    *,
    cluster: ClusterFn,
    summarize: SummarizeFn,
) -> int:
    """Build a topic tree over the existing flat concepts under ``concepts_root``.

    Top-down, global cold-start seed: cluster the FULL concept set into top
    topics, recurse into any topic still over ``FANOUT_K``. Building from the
    whole set (rather than greedily one-by-one) avoids freezing the high-level
    taxonomy on early-arriving concepts. Returns the number placed.
    """
    concepts_root.mkdir(parents=True, exist_ok=True)
    # Deterministic order; read all into memory, then clear the flat root.
    flat = sorted(p for p in concepts_root.glob("*.md") if p.name != TOPIC_FILE)
    items = [(p.stem, _brief(p), p.read_text(encoding="utf-8")) for p in flat]
    for p in flat:
        p.unlink()
    if not (concepts_root / TOPIC_FILE).exists():
        write_topic_md(concepts_root, "Knowledge base topics.", 0)
    return _build_subtree(concepts_root, items, cluster, summarize, depth=0)
