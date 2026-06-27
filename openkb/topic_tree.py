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
