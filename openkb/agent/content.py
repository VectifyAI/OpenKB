"""Wiki content browsing/reading tools for the OpenKB agent.

Split out of ``agent.tools`` (see ``tests/test_file_size.py``'s 800-line
module gate) — this module owns the "structured content access" surface
(taxonomy/document listings, unified content reads, KB status), while
``agent.tools`` keeps the lower-level, more heterogeneous tools (image
reads, KB-root file read/write, full-text search, artifact detection).
``agent.tools`` re-exports every public name here for backward
compatibility, so existing ``from openkb.agent.tools import ...`` call
sites are unaffected by this split.
"""

from __future__ import annotations

import contextlib
import json as _json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from openkb import frontmatter

# Maps a taxonomy "kind" to its wiki subdirectory. Single source of truth for
# list_taxonomy_items below.
_TAXONOMY_DIRS: dict[str, str] = {"concept": "concepts", "entity": "entities"}

# Maps a "document" kind to its wiki subdirectory. Single source of truth for
# list_documents below.
_DOCUMENT_DIRS: dict[str, str] = {"summary": "summaries", "exploration": "explorations"}

# All directory-backed kinds get_content() reads with an identical "whole
# file" strategy (kind -> subdirectory). "source" and "index" are handled
# separately by get_content itself: "source" because a document may be a
# short .md OR a paginated PageIndex .json with different `pages` semantics;
# "index" because it names a single root-level file, not a per-slug directory.
_CONTENT_DIRS: dict[str, str] = {**_TAXONOMY_DIRS, **_DOCUMENT_DIRS, "report": "reports"}
_CONTENT_KINDS = (*_CONTENT_DIRS, "source", "index")


def parse_pages(pages: str) -> list[int]:
    """Parse a page specification string into a sorted, deduplicated list of page numbers.

    Args:
        pages: Page spec such as ``"3-5,7,10-12"``.

    Returns:
        Sorted list of positive page numbers, e.g. ``[3, 4, 5, 7, 10, 11, 12]``.
    """
    result: set[int] = set()
    for part in pages.split(","):
        part = part.strip()
        if "-" in part:
            # Handle ranges like "3-5"; also handle negative numbers by only
            # splitting on the first "-" that follows a digit.
            segments = part.split("-")
            # Re-join to handle leading negatives: segments[0] may be empty
            # if part starts with "-".  We just try to parse start/end.
            # Silently skip malformed segments — parse_pages is a tolerant
            # parser by design (user-supplied page specs may contain typos).
            with contextlib.suppress(ValueError):
                if len(segments) == 2:
                    start, end = int(segments[0]), int(segments[1])
                    result.update(range(start, end + 1))
                elif len(segments) == 3 and segments[0] == "":
                    # e.g. "-1" split gives ['', '1']
                    result.add(-int(segments[1]))
                # More complex cases (e.g. negative range) are ignored.
        else:
            with contextlib.suppress(ValueError):
                result.add(int(part))
    return sorted(n for n in result if n > 0)


@dataclass(frozen=True)
class TaxonomyItem:
    """One persisted concept or entity page (never a pending candidate).

    ``PendingTopicsStore`` (see ``openkb.pending``) buffers not-yet-paged
    concept/entity candidates separately from the compiled ``.md`` pages
    under ``concepts/``/``entities/`` — this dataclass, and
    :func:`list_taxonomy_items`, only ever surface the latter, so a caller
    never sees an in-progress candidate as if it were a real page.
    """

    kind: Literal["concept", "entity"]
    slug: str
    path: str  # wiki-root-relative, e.g. "concepts/attention.md"
    brief: str
    # Entity type (e.g. "person", "organization"); always None for concepts.
    type: str | None = None


def list_taxonomy_items(wiki_root: str, kind: str | None = None) -> list[TaxonomyItem]:
    """List persisted concept and/or entity pages with their one-line briefs.

    Intended as the first step of the search strategy: browse this compact,
    semantically-scannable list and let the caller (an LLM) pick the
    relevant slug(s) by meaning — this is deliberately not a keyword search
    (see ``search_wiki`` for that, over summaries/sources only).

    Args:
        wiki_root: Absolute path to the wiki root directory.
        kind: Restrict to ``"concept"`` or ``"entity"``; ``None`` returns both.

    Returns:
        Items sorted by kind, then slug. Empty list if the KB has neither
        directory yet or both are empty.

    Raises:
        ValueError: *kind* is neither ``None``, ``"concept"``, nor ``"entity"``.
    """
    root = Path(wiki_root).resolve()
    kinds = [kind] if kind else ["concept", "entity"]
    for k in kinds:
        if k not in _TAXONOMY_DIRS:
            raise ValueError(f"Unknown kind {k!r}; expected 'concept' or 'entity'.")

    items: list[TaxonomyItem] = []
    for k in kinds:
        directory = root / _TAXONOMY_DIRS[k]
        if not directory.is_dir():
            continue
        for md_file in sorted(directory.glob("*.md")):
            text = md_file.read_text(encoding="utf-8")
            fm = frontmatter.parse(text)
            brief = frontmatter.resolve_description(fm)
            etype = None
            if k == "entity":
                etype = str(fm.get("type") or "").strip().lower() or "other"
            items.append(
                TaxonomyItem(
                    kind=k,  # type: ignore[arg-type]  # validated against _TAXONOMY_DIRS above
                    slug=md_file.stem,
                    path=f"{_TAXONOMY_DIRS[k]}/{md_file.name}",
                    brief=brief,
                    type=etype,
                )
            )
    return items


@dataclass(frozen=True)
class DocumentItem:
    """One persisted summary or exploration page.

    Mirrors :class:`TaxonomyItem` for a different pair of kinds: summaries
    (one per ingested document) and explorations (saved ``openkb query
    --save`` answers). Listed separately from concepts/entities via its own
    :func:`list_documents` rather than folded into :func:`list_taxonomy_items`
    — concepts/entities are meant to be browsed in full by an LLM picking a
    slug by meaning, while summaries/explorations are more commonly
    discovered via ``search_wiki`` than browsed exhaustively; the different
    usage pattern justifies a separate list function (see the tiered-search
    design discussion — this dataclass only covers the LIST side of that
    split, not the GET side, which is unified below in ``get_content``).
    """

    kind: Literal["summary", "exploration"]
    slug: str
    path: str  # wiki-root-relative, e.g. "summaries/paper.md"
    brief: str


def list_documents(wiki_root: str, kind: str | None = None) -> list[DocumentItem]:
    """List persisted summary and/or exploration pages with their one-line briefs.

    Args:
        wiki_root: Absolute path to the wiki root directory.
        kind: Restrict to ``"summary"`` or ``"exploration"``; ``None`` returns both.

    Returns:
        Items sorted by kind, then slug. Empty list if the KB has neither
        directory yet or both are empty.

    Raises:
        ValueError: *kind* is neither ``None``, ``"summary"``, nor ``"exploration"``.
    """
    root = Path(wiki_root).resolve()
    kinds = [kind] if kind else ["summary", "exploration"]
    for k in kinds:
        if k not in _DOCUMENT_DIRS:
            raise ValueError(f"Unknown kind {k!r}; expected 'summary' or 'exploration'.")

    items: list[DocumentItem] = []
    for k in kinds:
        directory = root / _DOCUMENT_DIRS[k]
        if not directory.is_dir():
            continue
        for md_file in sorted(directory.glob("*.md")):
            text = md_file.read_text(encoding="utf-8")
            fm = frontmatter.parse(text)
            if k == "exploration":
                # Explorations carry no description/brief frontmatter — the
                # originally-saved question (see cli.save_exploration) IS
                # the natural one-line brief.
                brief = str(fm.get("query") or "").strip()
            else:
                brief = frontmatter.resolve_description(fm)
            items.append(
                DocumentItem(
                    kind=k,  # type: ignore[arg-type]  # validated against _DOCUMENT_DIRS above
                    slug=md_file.stem,
                    path=f"{_DOCUMENT_DIRS[k]}/{md_file.name}",
                    brief=brief,
                )
            )
    return items


# Wiki subdirectories counted by get_kb_status — mirrors cli.print_status's
# subdirs list, plus "explorations" (which print_status doesn't count today).
_STATUS_SUBDIRS = ("sources", "summaries", "concepts", "entities", "reports", "explorations")


@dataclass(frozen=True)
class KbStatus:
    """Structured KB status: same counts as the CLI's ``openkb status``
    (``cli.print_status``), returned as data instead of printed so non-CLI
    callers — e.g. the MCP server's ``get_status`` tool — can use them
    without pulling in ``cli.py``'s much heavier import chain (click,
    litellm, the Agents SDK).
    """

    kb_dir: str
    counts: dict[str, int]
    total_indexed: int


def get_kb_status(kb_dir: str) -> KbStatus:
    """Return structured status counts for the knowledge base at *kb_dir*.

    Args:
        kb_dir: Absolute path to the KB root directory (containing ``wiki/``,
            ``.openkb/``, and optionally ``raw/``).

    Returns:
        ``.md`` file counts per wiki subdirectory (:data:`_STATUS_SUBDIRS`),
        a ``"raw"`` count when ``raw/`` exists, and ``total_indexed`` from
        the ``.openkb/hashes.json`` registry (``0`` if no registry exists
        yet).
    """
    root = Path(kb_dir).resolve()
    wiki_dir = root / "wiki"
    counts: dict[str, int] = {}
    for subdir in _STATUS_SUBDIRS:
        path = wiki_dir / subdir
        counts[subdir] = len(list(path.glob("*.md"))) if path.is_dir() else 0

    raw_dir = root / "raw"
    if raw_dir.is_dir():
        counts["raw"] = len([f for f in raw_dir.iterdir() if f.is_file()])

    hashes_file = root / ".openkb" / "hashes.json"
    total_indexed = 0
    if hashes_file.exists():
        hashes = _json.loads(hashes_file.read_text(encoding="utf-8"))
        total_indexed = len(hashes)

    return KbStatus(kb_dir=str(root), counts=counts, total_indexed=total_indexed)


@dataclass(frozen=True)
class ContentEntry:
    """One match from :func:`get_content`.

    Always returned as part of a list, even when there is exactly one
    match — callers never need to branch on "single result vs. list of
    results" depending on how many kinds matched. ``error`` is a soft,
    per-entry explanation (not found, or ``pages`` used/missing where it
    shouldn't be) — the only case :func:`get_content` raises an exception
    for is an unrecognized ``kind`` value (a caller bug, not a normal
    "need more info" outcome).
    """

    kind: str
    path: str  # wiki-root-relative
    content: str | None
    error: str | None = None


def _read_pageindex_pages(json_path: Path, pages: str, doc_name: str) -> str:
    """Return formatted content for the requested *pages* of a PageIndex doc.

    Reads a JSON array of ``{"page": int, "content": str}`` objects (see
    ``get_content``'s "source" handling) with an optional ``"images"`` list
    of ``{"path": str, ...}`` objects.

    Returns a "no content found" message (not an exception) when the
    requested pages have no matching entries — a typo'd page range is a
    normal, expected outcome, not a caller bug.
    """
    data = _json.loads(json_path.read_text(encoding="utf-8"))
    requested = set(parse_pages(pages))
    matches = [entry for entry in data if entry.get("page") in requested]

    if not matches:
        return f"No content found for pages {pages} in {doc_name}."

    parts: list[str] = []
    for entry in matches:
        page_num = entry["page"]
        content = entry.get("content", "")
        block = f"[Page {page_num}]\n{content}"
        images = entry.get("images")
        if images:
            paths = ", ".join(img["path"] for img in images if "path" in img)
            if paths:
                block += f"\n[Images: {paths}]"
        parts.append(block)

    return "\n\n".join(parts) + "\n\n"


def _resolve_source_entries(
    slug: str, root: Path, pages: str | None, explicit: bool
) -> list[ContentEntry]:
    """Resolve a "source" kind match — short ``.md`` or paginated PageIndex ``.json``.

    Auto-detects which of the two a document is, so the caller never has to
    know/choose between them up front: ``pages`` is required for the long
    (PageIndex) case, forbidden for the short case — a soft ``error`` on the
    single returned entry explains which, rather than the caller picking the
    wrong one of two differently-shaped functions (the previous split
    between ``read_wiki_file`` and ``get_wiki_page_content``).
    """
    json_path = (root / "sources" / f"{slug}.json").resolve()
    md_path = (root / "sources" / f"{slug}.md").resolve()

    if not json_path.is_relative_to(root) or not md_path.is_relative_to(root):
        return [
            ContentEntry(
                kind="source",
                path=f"sources/{slug}",
                content=None,
                error="Access denied: path escapes wiki root.",
            )
        ]

    if json_path.exists():
        rel_path = f"sources/{slug}.json"
        if pages is None:
            return [
                ContentEntry(
                    kind="source",
                    path=rel_path,
                    content=None,
                    error=(
                        "This is a long (PageIndex) document; pages is required "
                        "(e.g. pages='3-5,7'). Use search_wiki(scope=['sources']) "
                        "for a locator naming the right page, or list_documents "
                        "for this document's overview."
                    ),
                )
            ]
        return [
            ContentEntry(
                kind="source", path=rel_path, content=_read_pageindex_pages(json_path, pages, slug)
            )
        ]

    if md_path.exists():
        rel_path = f"sources/{slug}.md"
        if pages is not None and explicit:
            # Only an error when the caller explicitly asked for kind="source"
            # with pages set (a genuine mistake) — during a kind=None fan-out,
            # pages was probably meant for a different (long) source match
            # elsewhere, so a short doc here just ignores it like every other
            # non-"source" kind already does.
            return [
                ContentEntry(
                    kind="source",
                    path=rel_path,
                    content=None,
                    error=(
                        "pages is not valid for a short (non-paginated) source document; omit it."
                    ),
                )
            ]
        return [
            ContentEntry(kind="source", path=rel_path, content=md_path.read_text(encoding="utf-8"))
        ]

    if explicit:
        return [
            ContentEntry(
                kind="source",
                path=f"sources/{slug}.md",
                content=None,
                error=f"File not found: sources/{slug}.md",
            )
        ]
    return []


def get_content(
    slug: str,
    wiki_root: str,
    kind: str | None = None,
    pages: str | None = None,
) -> list[ContentEntry]:
    """Read wiki content by slug — one function for every content kind.

    Replaces the previously separate ``get_taxonomy_item``/``read_wiki_file``/
    ``get_wiki_page_content`` split: ``read_wiki_file`` and
    ``get_wiki_page_content`` now delegate to this function (kept for
    backward compatibility — both predate this change and are already
    released); ``get_taxonomy_item`` is gone (it was still unreleased).

    Args:
        slug: Page slug (filename without extension), e.g. ``"attention"``.
            For "source", identical to the paired summary's slug (a summary
            and its source describe the same document 1:1) — so a plain
            ``get_content(slug, wiki_root)`` without ``kind`` commonly
            returns both as separate entries, not a single "first match".
        wiki_root: Absolute path to the wiki root directory.
        kind: One of ``"concept"``, ``"entity"``, ``"summary"``,
            ``"exploration"``, ``"source"``, ``"report"``, ``"index"``.
            ``None`` (default) searches ALL seven and returns one entry per
            match found — 0, 1, or several (mirrors ``list_taxonomy_items``/
            ``list_documents``' "kind=None returns a combined list" behavior,
            rather than a "first match wins" precedence that would silently
            drop e.g. the source when a summary shares its slug). ``"report"``
            and ``"index"`` are gettable like any other kind but deliberately
            have no ``list_*`` counterpart — pure diagnostic/meta artifacts
            with no meaningful one-line brief to browse.
        pages: Only meaningful for a ``"source"`` match — required for a long
            (PageIndex) document, forbidden otherwise; see
            :func:`_resolve_source_entries`. Ignored (has no effect) for
            every other kind — set alongside a non-"source" kind, it is
            silently dropped rather than erroring, since ``kind=None`` fans
            out across kinds where "pages" simply isn't applicable to most
            of them.

    Returns:
        One :class:`ContentEntry` per match — always a list, even for a
        single match, so callers never branch on the return shape.

    Raises:
        ValueError: *kind* is not one of the recognized values.
    """
    root = Path(wiki_root).resolve()
    kinds = [kind] if kind else list(_CONTENT_KINDS)
    for k in kinds:
        if k not in _CONTENT_KINDS:
            raise ValueError(f"Unknown kind {k!r}; expected one of {_CONTENT_KINDS}.")

    entries: list[ContentEntry] = []
    for k in kinds:
        if k == "source":
            entries.extend(_resolve_source_entries(slug, root, pages, explicit=kind is not None))
            continue

        if k == "index":
            if slug != "index":
                if kind is not None:
                    entries.append(
                        ContentEntry(
                            kind="index",
                            path="index.md",
                            content=None,
                            error="File not found: index.md",
                        )
                    )
                continue
            index_path = (root / "index.md").resolve()
            if not index_path.is_relative_to(root) or not index_path.exists():
                if kind is not None:
                    entries.append(
                        ContentEntry(
                            kind="index",
                            path="index.md",
                            content=None,
                            error="File not found: index.md",
                        )
                    )
                continue
            if pages is not None and kind is not None:
                entries.append(
                    ContentEntry(
                        kind="index",
                        path="index.md",
                        content=None,
                        error="pages is not valid for kind='index'.",
                    )
                )
                continue
            entries.append(
                ContentEntry(
                    kind="index", path="index.md", content=index_path.read_text(encoding="utf-8")
                )
            )
            continue

        # concept/entity/summary/exploration/report: identical whole-file lookup.
        directory = _CONTENT_DIRS[k]
        rel_path = f"{directory}/{slug}.md"
        path = (root / directory / f"{slug}.md").resolve()
        if not path.is_relative_to(root):
            if kind is not None:
                entries.append(
                    ContentEntry(
                        kind=k,
                        path=rel_path,
                        content=None,
                        error="Access denied: path escapes wiki root.",
                    )
                )
            continue
        if not path.exists():
            if kind is not None:
                entries.append(
                    ContentEntry(
                        kind=k, path=rel_path, content=None, error=f"File not found: {rel_path}"
                    )
                )
            continue
        if pages is not None and kind is not None:
            entries.append(
                ContentEntry(
                    kind=k,
                    path=rel_path,
                    content=None,
                    error=f"pages is only valid for kind='source', not {k!r}.",
                )
            )
            continue
        entries.append(
            ContentEntry(kind=k, path=rel_path, content=path.read_text(encoding="utf-8"))
        )
    return entries


_CONTENT_DIR_TO_KIND = {v: k for k, v in _CONTENT_DIRS.items()}


def _kind_and_slug_from_path(path: str) -> tuple[str, str] | None:
    """Map a wiki-root-relative *path* to a ``(kind, slug)`` pair for
    :func:`get_content`, or ``None`` if it doesn't cleanly fall under one of
    get_content's known directories/files (defensive fallback only — every
    path in the current wiki schema, including ``index.md`` and
    ``reports/*.md``, maps cleanly; this stays conservative for anything
    unexpected, e.g. path traversal or an unforeseen nesting, rather than
    guessing).
    """
    normalized = path.replace("\\", "/").strip("/")
    if normalized == "index.md":
        return "index", "index"
    if "/" not in normalized:
        return None
    top, rest = normalized.split("/", 1)
    if "/" in rest:
        return None  # only a single flat filename per kind is recognized
    kind = _CONTENT_DIR_TO_KIND.get(top) or ("source" if top == "sources" else None)
    if kind is None:
        return None
    slug = rest[: -len(Path(rest).suffix)] if Path(rest).suffix else rest
    return kind, slug
