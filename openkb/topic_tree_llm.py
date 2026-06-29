"""LLM-backed decision callables for the topic-tree engine.

These are injected into the pure engine (``openkb.topic_tree``) so the engine
stays unit-testable without a network. Production code wires these in.
"""
from __future__ import annotations

import json

from openkb.agent.compiler import _JSON_RESPONSE_FORMAT, _llm_call
from openkb.topic_tree import FANOUT_K, TopicNodeView

_CHOOSE = (
    "You are placing a new concept into a topic tree. Given the current node's "
    "summary and its child topics, choose the ONE child topic the concept best "
    "belongs under, or null to keep it at this node. "
    'Reply JSON: {{"pick": <name|null>}}.\n\n'
    "Node summary: {summary}\nChild topics:\n{topics}\n\nConcept: {brief}"
)


def make_choose(model: str):
    def choose(view: TopicNodeView, brief: str):
        topics = "\n".join(f"- {n}: {s}" for n, s in view.child_topics) or "(none)"
        raw = _llm_call(
            model,
            [{"role": "user", "content": _CHOOSE.format(
                summary=view.summary, topics=topics, brief=brief)}],
            "topic-choose",
            response_format=_JSON_RESPONSE_FORMAT,
        )
        pick = (json.loads(raw) or {}).get("pick")
        valid = {n for n, _ in view.child_topics}
        return pick if pick in valid else None

    return choose


_CLUSTER = (
    "Cluster these concepts into 2-{kmax} coherent subtopics. Reply JSON: "
    '{{"groups": {{"<subtopic-kebab-name>": ["<stem>", ...]}}}}. '
    "Every stem must appear exactly once.\n\nConcepts:\n{items}"
)


def make_cluster(model: str):
    def cluster(items):
        listing = "\n".join(f"- {stem}: {brief}" for stem, brief in items)
        raw = _llm_call(
            model,
            [{"role": "user", "content": _CLUSTER.format(
                kmax=max(2, FANOUT_K // 2), items=listing)}],
            "topic-cluster",
            response_format=_JSON_RESPONSE_FORMAT,
        )
        groups = (json.loads(raw) or {}).get("groups", {})
        known = {s for s, _ in items}
        seen: set[str] = set()
        clean: dict[str, list[str]] = {}
        for name, stems in groups.items():
            kept = [s for s in stems if s in known and s not in seen]
            seen.update(kept)
            if kept:
                clean[name] = kept
        missing = [s for s in known if s not in seen]
        if missing:
            clean.setdefault("misc", []).extend(missing)
        return clean

    return cluster


_SUMMARIZE = (
    'Write a one-paragraph summary of the subtopic "{name}" that abstracts '
    "these concept briefs:\n{briefs}"
)


def make_summarize(model: str):
    def summarize(name: str, briefs: list[str]) -> str:
        raw = _llm_call(
            model,
            [{"role": "user", "content": _SUMMARIZE.format(
                name=name, briefs="\n".join(f"- {b}" for b in briefs))}],
            "topic-summary",
        )
        return raw.strip()

    return summarize
