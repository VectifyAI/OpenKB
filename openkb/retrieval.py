"""Relevance retrieval for the concepts-plan step (scaling prototype).

The default OpenKB pipeline injects *every* existing concept/entity brief into
the ``concepts-plan`` prompt. That makes the plan prompt grow O(N) with the KB
(observed: ~2k tokens early -> ~18k tokens at a few hundred concepts), which
hurts both speed/cost and the model's ability to reconcile the new doc against
the right existing pages (it starts creating near-duplicates).

This module provides an opt-in post-filter: given the current document's
summary as a query, rank the formatted brief lines and keep only the top-K
most relevant. Converts the per-doc plan context from O(N) back to ~O(K).

First cut uses a dependency-free TF-IDF cosine over the brief lines. The
interface (``select_relevant_briefs``) is intentionally swappable for an
embedding-based ranker later without touching the compiler.
"""
from __future__ import annotations

import math
import re
from collections import Counter

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_NONE = "(none yet)"


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _tfidf_vectors(docs: list[list[str]]) -> tuple[list[dict[str, float]], dict[str, float]]:
    """Return per-doc tf-idf vectors and the idf map for a small corpus."""
    n = len(docs)
    df: Counter[str] = Counter()
    for toks in docs:
        df.update(set(toks))
    idf = {t: math.log((n + 1) / (c + 1)) + 1.0 for t, c in df.items()}
    vecs: list[dict[str, float]] = []
    for toks in docs:
        tf = Counter(toks)
        vecs.append({t: (cnt / len(toks)) * idf.get(t, 0.0) for t, cnt in tf.items()} if toks else {})
    return vecs, idf


def _cosine(a: dict[str, float], q: dict[str, float]) -> float:
    if not a or not q:
        return 0.0
    dot = sum(v * q.get(t, 0.0) for t, v in a.items())
    na = math.sqrt(sum(v * v for v in a.values()))
    nq = math.sqrt(sum(v * v for v in q.values()))
    return dot / (na * nq) if na and nq else 0.0


def select_relevant_briefs(query: str, briefs_block: str, k: int) -> str:
    """Keep the top-K brief lines most relevant to ``query``.

    ``briefs_block`` is the formatted output of ``_read_concept_briefs`` /
    ``_read_entity_briefs`` (one ``- ...`` line per page). Returns a block of
    the same shape. No-ops when the block is empty/"(none yet)" or already
    within the budget, so behaviour is unchanged for small KBs.
    """
    if not briefs_block or briefs_block.strip() == _NONE:
        return briefs_block
    lines = [ln for ln in briefs_block.splitlines() if ln.strip()]
    if k <= 0 or len(lines) <= k:
        return briefs_block

    # Corpus = brief lines + the query, so idf is shared across the comparison.
    line_toks = [_tokenize(ln) for ln in lines]
    q_toks = _tokenize(query)
    vecs, _ = _tfidf_vectors(line_toks + [q_toks])
    q_vec = vecs[-1]
    scored = sorted(
        range(len(lines)),
        key=lambda i: _cosine(vecs[i], q_vec),
        reverse=True,
    )
    keep = sorted(scored[:k])  # restore original (stable) order for the prompt
    return "\n".join(lines[i] for i in keep)
