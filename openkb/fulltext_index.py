"""Dependency-free BM25 full-text index over compiled wiki pages.

Hybrid retrieval: the query/chat agent's primary search strategy is
``index.md`` navigation (one-line summaries pointing at pages to read). That
strategy loses recall for details buried deep in a page body that the
one-liner doesn't mention. This module adds an additive, keyword-level
fallback — a BM25 index over the same compiled pages — exposed to the agent
as the ``search_wiki`` tool (see ``openkb.agent.tools.search_wiki``). It is a
union with index-driven navigation, not a replacement, so recall can only
improve relative to index-only navigation, never regress.

No new dependency: OpenKB pins dependencies exactly and vets each one
deliberately (see ``pyproject.toml``), and BM25 over a few hundred wiki pages
is cheap enough in pure Python that a search-library dependency (e.g. Whoosh)
isn't warranted.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

from openkb.schema import PAGE_CONTENT_DIRS

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Standard BM25 hyperparameters (Robertson/Sparck-Jones defaults).
_K1 = 1.5
_B = 0.75

_SNIPPET_RADIUS = 80  # characters of context on each side of the first match


def _tokenize(text: str) -> list[str]:
    """Lowercase, alphanumeric-only tokenization (no stemming)."""
    return _TOKEN_RE.findall(text.lower())


def _extract_title(text: str) -> str | None:
    """Return the first ``# heading`` line's text, or ``None``."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return None


def _make_snippet(text: str, query_terms: list[str]) -> str:
    """Return a short excerpt around the first query-term match in *text*."""
    lowered = text.lower()
    match_pos = -1
    for term in query_terms:
        pos = lowered.find(term)
        if pos != -1 and (match_pos == -1 or pos < match_pos):
            match_pos = pos
    if match_pos == -1:
        collapsed = " ".join(text.split())
        truncated = collapsed[: _SNIPPET_RADIUS * 2]
        suffix = "…" if len(collapsed) > _SNIPPET_RADIUS * 2 else ""
        return truncated + suffix

    start = max(0, match_pos - _SNIPPET_RADIUS)
    end = min(len(text), match_pos + _SNIPPET_RADIUS)
    collapsed = " ".join(text[start:end].split())
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{collapsed}{suffix}"


@dataclass(frozen=True)
class SearchHit:
    """A single BM25 search result over a wiki page."""

    path: str  # wiki-root-relative, e.g. "concepts/attention.md"
    title: str
    score: float
    snippet: str


@dataclass(frozen=True)
class _IndexedPage:
    path: str
    title: str
    text: str
    tokens: list[str]


class WikiFullTextIndex:
    """In-memory BM25 index over :data:`PAGE_CONTENT_DIRS` wiki pages.

    Rebuilt fresh on construction — cheap enough at the wiki sizes this
    pattern targets (hundreds of pages); no on-disk cache or incremental
    update is needed.
    """

    def __init__(self, wiki_root: str | Path) -> None:
        self._wiki_root = Path(wiki_root).resolve()
        self._pages: list[_IndexedPage] = []
        self._df: dict[str, int] = {}
        self._avgdl = 0.0
        self._build()

    def _build(self) -> None:
        for subdir in PAGE_CONTENT_DIRS:
            target = self._wiki_root / subdir
            if not target.is_dir():
                continue
            for md_file in sorted(target.glob("*.md")):
                text = md_file.read_text(encoding="utf-8")
                tokens = _tokenize(text)
                if not tokens:
                    continue
                title = _extract_title(text) or md_file.stem
                path = f"{subdir}/{md_file.name}"
                self._pages.append(_IndexedPage(path=path, title=title, text=text, tokens=tokens))

        if not self._pages:
            return

        self._avgdl = sum(len(page.tokens) for page in self._pages) / len(self._pages)
        for page in self._pages:
            for term in set(page.tokens):
                self._df[term] = self._df.get(term, 0) + 1

    def _idf(self, term: str) -> float:
        n = len(self._pages)
        df = self._df.get(term, 0)
        # +1 smoothing keeps idf non-negative even for very common terms.
        return math.log((n - df + 0.5) / (df + 0.5) + 1)

    def _score(self, query_terms: list[str], page: _IndexedPage) -> float:
        dl = len(page.tokens)
        tf: dict[str, int] = {}
        for term in page.tokens:
            tf[term] = tf.get(term, 0) + 1

        score = 0.0
        for term in query_terms:
            f = tf.get(term, 0)
            if f == 0:
                continue
            idf = self._idf(term)
            numerator = f * (_K1 + 1)
            denominator = f + _K1 * (1 - _B + _B * dl / self._avgdl)
            score += idf * (numerator / denominator)
        return score

    def search(self, query: str, top_k: int = 5) -> list[SearchHit]:
        """Return the ``top_k`` highest-scoring pages for *query* (BM25).

        Args:
            query: Free-text search query (keywords or a question).
            top_k: Maximum number of results to return.

        Returns:
            Ranked hits, highest score first. Empty if the query has no
            tokens or the index has no pages.
        """
        query_terms = _tokenize(query)
        if not query_terms or not self._pages:
            return []

        scored = [(self._score(query_terms, page), page) for page in self._pages]
        scored = [(score, page) for score, page in scored if score > 0]
        scored.sort(key=lambda item: item[0], reverse=True)

        return [
            SearchHit(
                path=page.path,
                title=page.title,
                score=round(score, 3),
                snippet=_make_snippet(page.text, query_terms),
            )
            for score, page in scored[:top_k]
        ]
