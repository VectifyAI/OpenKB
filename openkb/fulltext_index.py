"""Dependency-free BM25 full-text index over compiled wiki pages.

Hybrid retrieval: the query/chat agent's primary search strategy is
``index.md`` navigation (one-line summaries pointing at pages to read). That
strategy loses recall for details buried deep in a page body that the
one-liner doesn't mention. This module adds an additive, keyword-level
fallback — a BM25 index over the same compiled pages — exposed to the agent
as the ``search_wiki`` tool (see ``openkb.agent.tools.search_wiki``). It is a
union with index-driven navigation, not a replacement, so recall can only
improve relative to index-only navigation, never regress.

Concepts and entities are deliberately excluded from full-text search (see
:class:`TieredWikiSearch` below) — they are found by semantic browsing
(``list_taxonomy_items``/``get_content`` in ``agent.tools``), not keyword
search, so :class:`WikiFullTextIndex` (kept for backward compatibility with
the original single-tier ``search_wiki`` tool) and :class:`TieredWikiSearch`
cover different, non-overlapping surfaces:

- :class:`WikiFullTextIndex` — the original combined BM25 index over
  ``concepts/`` + ``entities/`` + ``summaries/`` (:data:`PAGE_CONTENT_DIRS`).
- :class:`TieredWikiSearch` — four independent BM25 tiers, each scoped to a
  different part of a document's lifecycle so a query only "wastes" recall
  budget on the granularity it's actually likely to match at:
  1. ``briefs``   — one-line ``description``/``brief`` frontmatter per
     ``summaries/*.md`` (same short text ``index.md`` shows). High precision,
     low recall — good for on-topic queries, filters out incidental
     word-frequency noise from long documents.
  2. ``summaries`` — full body of ``summaries/*.md``. Higher recall for
     specific terms/figures the one-liner omits.
  3. ``sources``  — raw ``sources/*.md`` (whole file) and ``sources/*.json``
     PageIndex documents (indexed **per page**, not per document, so a hit
     can point at an exact page via a :class:`Locator` instead of forcing a
     re-score over an entire long document). Covers details that never make
     it into a summary at all (creation dates, authors, exact field names).
  4. ``explorations`` — full body of ``explorations/*.md`` (saved
     ``openkb query --save`` answers). Its own tier rather than folded into
     ``summaries`` — an exploration is a previously-synthesized answer, not
     a document summary, and keeping it a separate tier means a hit stays
     unambiguously labeled as one or the other by which tier surfaced it.

No new dependency: OpenKB pins dependencies exactly and vets each one
deliberately (see ``pyproject.toml``), and BM25 over a few hundred wiki pages
is cheap enough in pure Python that a search-library dependency (e.g. Whoosh)
isn't warranted.
"""

from __future__ import annotations

import json as _json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from openkb import frontmatter
from openkb.schema import PAGE_CONTENT_DIRS

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Standard BM25 hyperparameters (Robertson/Sparck-Jones defaults).
_K1 = 1.5
_B = 0.75

_SNIPPET_RADIUS = 80  # characters of context on each side of the first match

# Valid `scope` values for TieredWikiSearch.search() — one BM25 tier each.
TIERED_SCOPES = ("briefs", "summaries", "sources", "explorations")


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
class Locator:
    """Points at a specific location within a hit's page for a follow-up read.

    ``kind="line"``: 1-based line number within a Markdown file (computed at
    query time from the first query-term match). ``kind="page"``: 1-based
    PageIndex page number within a long-doc ``sources/*.json`` array — fixed
    at indexing time (one page = one BM25 "document"), and directly usable
    with ``get_page_content(doc_name, pages=str(value))``.
    """

    kind: Literal["line", "page"]
    value: int


@dataclass(frozen=True)
class SearchHit:
    """A single BM25 search result over a wiki page (or a PageIndex page)."""

    path: str  # wiki-root-relative, e.g. "concepts/attention.md"
    title: str
    score: float
    snippet: str
    locator: Locator | None = None


@dataclass(frozen=True)
class _IndexedPage:
    path: str
    title: str
    text: str
    tokens: list[str]
    # Fixed at indexing time for pseudo-documents that are inherently
    # page-scoped (one PageIndex page = one _IndexedPage); None otherwise, in
    # which case _BM25Scorer computes a "line" Locator at query time instead.
    fixed_locator: Locator | None = None


def _find_line_locator(text: str, query_terms: list[str]) -> Locator | None:
    """Return a 1-based ``line`` Locator for the first query-term match line.

    Returns ``None`` if no line contains any query term (can happen when the
    match is only visible after tokenization, e.g. across punctuation).
    """
    for line_no, line in enumerate(text.splitlines(), start=1):
        lowered = line.lower()
        if any(term in lowered for term in query_terms):
            return Locator(kind="line", value=line_no)
    return None


class _BM25Scorer:
    """Pure BM25 ranking (Robertson/Sparck-Jones) over a fixed page list.

    Extracted from the original :class:`WikiFullTextIndex` so the same
    scoring math is shared between the legacy combined index and
    :class:`TieredWikiSearch`'s three independent tiers, without duplicating
    the formula. No I/O — callers build the ``pages`` list.
    """

    def __init__(self, pages: list[_IndexedPage]) -> None:
        self._pages = pages
        self._df: dict[str, int] = {}
        self._avgdl = 0.0
        if not pages:
            return
        self._avgdl = sum(len(page.tokens) for page in pages) / len(pages)
        for page in pages:
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
                locator=page.fixed_locator or _find_line_locator(page.text, query_terms),
            )
            for score, page in scored[:top_k]
        ]


class WikiFullTextIndex:
    """In-memory BM25 index over :data:`PAGE_CONTENT_DIRS` wiki pages.

    Rebuilt fresh on construction — cheap enough at the wiki sizes this
    pattern targets (hundreds of pages); no on-disk cache or incremental
    update is needed. Kept for backward compatibility with the original
    (PR #234) single-tier ``search_wiki`` tool — new callers should prefer
    :class:`TieredWikiSearch`, which separates concepts/entities (browsed via
    ``list_taxonomy_items``, not indexed here) from summaries/sources.
    """

    def __init__(self, wiki_root: str | Path) -> None:
        self._wiki_root = Path(wiki_root).resolve()
        self._pages: list[_IndexedPage] = _build_pages_from_dirs(self._wiki_root, PAGE_CONTENT_DIRS)
        self._scorer = _BM25Scorer(self._pages)

    def search(self, query: str, top_k: int = 5) -> list[SearchHit]:
        """Return the ``top_k`` highest-scoring pages for *query* (BM25).

        Args:
            query: Free-text search query (keywords or a question).
            top_k: Maximum number of results to return.

        Returns:
            Ranked hits, highest score first. Empty if the query has no
            tokens or the index has no pages.
        """
        return self._scorer.search(query, top_k=top_k)


def _build_pages_from_dirs(wiki_root: Path, subdirs: tuple[str, ...]) -> list[_IndexedPage]:
    """Index every ``*.md`` file's full text under each of *subdirs*."""
    pages: list[_IndexedPage] = []
    for subdir in subdirs:
        target = wiki_root / subdir
        if not target.is_dir():
            continue
        for md_file in sorted(target.glob("*.md")):
            text = md_file.read_text(encoding="utf-8")
            tokens = _tokenize(text)
            if not tokens:
                continue
            title = _extract_title(text) or md_file.stem
            path = f"{subdir}/{md_file.name}"
            pages.append(_IndexedPage(path=path, title=title, text=text, tokens=tokens))
    return pages


def _build_brief_pages(wiki_root: Path) -> list[_IndexedPage]:
    """One pseudo-document per ``summaries/*.md``, text = its one-line brief.

    Uses the ``description``/legacy ``brief`` frontmatter field — the same
    short text ``index.md``'s ``## Documents`` section shows — not the full
    body. Pages without a resolvable brief are skipped (nothing to index).
    """
    summaries_dir = wiki_root / "summaries"
    if not summaries_dir.is_dir():
        return []
    pages: list[_IndexedPage] = []
    for md_file in sorted(summaries_dir.glob("*.md")):
        text = md_file.read_text(encoding="utf-8")
        brief = frontmatter.resolve_description(frontmatter.parse(text))
        tokens = _tokenize(brief)
        if not tokens:
            continue
        title = _extract_title(text) or md_file.stem
        pages.append(
            _IndexedPage(path=f"summaries/{md_file.name}", title=title, text=brief, tokens=tokens)
        )
    return pages


def _build_summary_pages(wiki_root: Path) -> list[_IndexedPage]:
    """One document per ``summaries/*.md``, text = full body (no frontmatter)."""
    summaries_dir = wiki_root / "summaries"
    if not summaries_dir.is_dir():
        return []
    pages: list[_IndexedPage] = []
    for md_file in sorted(summaries_dir.glob("*.md")):
        text = md_file.read_text(encoding="utf-8")
        body = frontmatter.body_only(text)
        tokens = _tokenize(body)
        if not tokens:
            continue
        title = _extract_title(text) or md_file.stem
        pages.append(
            _IndexedPage(path=f"summaries/{md_file.name}", title=title, text=body, tokens=tokens)
        )
    return pages


def _build_exploration_pages(wiki_root: Path) -> list[_IndexedPage]:
    """One document per ``explorations/*.md``, text = full saved-answer body.

    Its own independent tier — not merged into ``summaries``/``briefs`` —
    so a hit here is unambiguously a previously-saved query answer rather
    than a document summary, even though both are searched the same way
    (full body, BM25). Title is the original saved ``query:`` frontmatter
    value (explorations are freeform answers with no "# heading"
    convention to fall back on as reliably as summaries/sources have).
    """
    explorations_dir = wiki_root / "explorations"
    if not explorations_dir.is_dir():
        return []
    pages: list[_IndexedPage] = []
    for md_file in sorted(explorations_dir.glob("*.md")):
        text = md_file.read_text(encoding="utf-8")
        body = frontmatter.body_only(text)
        tokens = _tokenize(body)
        if not tokens:
            continue
        query = str(frontmatter.parse(text).get("query") or "").strip()
        title = query or _extract_title(text) or md_file.stem
        pages.append(
            _IndexedPage(path=f"explorations/{md_file.name}", title=title, text=body, tokens=tokens)
        )
    return pages


def _build_source_pages(wiki_root: Path) -> list[_IndexedPage]:
    """Sources tier: ``sources/*.md`` (whole file) + ``sources/*.json`` (per page).

    A PageIndex ``sources/*.json`` document is a JSON array of
    ``{"page": int, "content": str, ...}`` objects (see
    ``agent.tools.get_wiki_page_content``). Each page is indexed as its own
    ``_IndexedPage`` with a fixed ``page`` :class:`Locator` — never the whole
    document as one BM25 unit — so a hit points at an exact page instead of
    diluting the score across a potentially very long document, and so the
    locator is directly usable with ``get_page_content(doc_name, pages=...)``.
    """
    sources_dir = wiki_root / "sources"
    if not sources_dir.is_dir():
        return []
    pages: list[_IndexedPage] = []
    for src_file in sorted(sources_dir.iterdir()):
        if src_file.suffix == ".md":
            text = src_file.read_text(encoding="utf-8")
            tokens = _tokenize(text)
            if not tokens:
                continue
            title = _extract_title(text) or src_file.stem
            pages.append(
                _IndexedPage(path=f"sources/{src_file.name}", title=title, text=text, tokens=tokens)
            )
        elif src_file.suffix == ".json":
            pages.extend(_index_pageindex_source(src_file))
    return pages


def _index_pageindex_source(src_file: Path) -> list[_IndexedPage]:
    """Return one ``_IndexedPage`` per page of a PageIndex ``sources/*.json`` doc.

    Tolerant of malformed/foreign JSON (skips, doesn't raise) — a hand-edited
    or unexpected file under ``sources/`` shouldn't break indexing of the rest
    of the KB.
    """
    try:
        data = _json.loads(src_file.read_text(encoding="utf-8"))
    except (_json.JSONDecodeError, OSError, UnicodeDecodeError):
        return []
    if not isinstance(data, list):
        return []

    pages: list[_IndexedPage] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        page_num = entry.get("page")
        content = entry.get("content", "")
        if not isinstance(page_num, int) or not isinstance(content, str):
            continue
        tokens = _tokenize(content)
        if not tokens:
            continue
        pages.append(
            _IndexedPage(
                path=f"sources/{src_file.name}",
                title=f"{src_file.stem} (page {page_num})",
                text=content,
                tokens=tokens,
                fixed_locator=Locator(kind="page", value=page_num),
            )
        )
    return pages


class TieredWikiSearch:
    """Four independent BM25 tiers over ``summaries/``, ``sources/``, and
    ``explorations/``.

    Concepts and entities are intentionally out of scope here — they are
    browsed semantically via ``list_taxonomy_items``/``get_content``
    (``agent.tools``), not keyword-searched. Rebuilt fresh on construction,
    same no-cache rationale as :class:`WikiFullTextIndex` (see module
    docstring); cheap at the wiki sizes this pattern targets.
    """

    def __init__(self, wiki_root: str | Path) -> None:
        wiki_root = Path(wiki_root).resolve()
        self._scorers: dict[str, _BM25Scorer] = {
            "briefs": _BM25Scorer(_build_brief_pages(wiki_root)),
            "summaries": _BM25Scorer(_build_summary_pages(wiki_root)),
            "sources": _BM25Scorer(_build_source_pages(wiki_root)),
            "explorations": _BM25Scorer(_build_exploration_pages(wiki_root)),
        }

    def search(
        self, query: str, scope: list[str] | None = None, top_k: int = 5
    ) -> dict[str, list[SearchHit]]:
        """Search one or more tiers; returns ``{tier_name: [SearchHit, ...]}``.

        Args:
            query: Free-text search query (keywords or a question).
            scope: Subset of :data:`TIERED_SCOPES` to search; ``None``
                searches all three tiers.
            top_k: Maximum ranked results to return per tier.

        Returns:
            One entry per searched tier (only the requested/valid tiers are
            present as keys — never an empty-list placeholder for tiers the
            caller didn't ask for).

        Raises:
            ValueError: *scope* contains a name outside :data:`TIERED_SCOPES`.
        """
        tiers = scope if scope else list(TIERED_SCOPES)
        invalid = [t for t in tiers if t not in TIERED_SCOPES]
        if invalid:
            raise ValueError(f"Unknown scope(s) {invalid}; expected any of {TIERED_SCOPES}.")
        return {tier: self._scorers[tier].search(query, top_k=top_k) for tier in tiers}
