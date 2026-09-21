"""MCP server exposing taxonomy browsing and tiered search to external clients.

Lets any MCP-capable AI assistant (GitHub Copilot, Claude Code, Cursor, etc.)
browse the wiki's taxonomy, run the tiered BM25 search, and read wiki content
(``agent.content.list_taxonomy_items``/``list_documents``/``get_content``,
``fulltext_index.TieredWikiSearch``) without running inside the ``openkb
query``/``openkb chat`` agent process or shelling out to the CLI. Run with
the ``openkb-mcp`` console script (stdio transport), or ``python -m
openkb.mcp_server``.

Every tool takes an optional ``kb`` parameter (a registered KB name/alias,
or an absolute path to a KB root) so one long-lived MCP server process can
serve multiple knowledge bases — see ``_resolve_kb`` and ``list_kbs``.
Omitting ``kb`` keeps today's behavior (cwd-walk -> global default),
preserving compatibility with the existing fixed ``"cwd"`` MCP client
config example in README.md.

No index cache: every tool rebuilds its underlying index fresh on every
call, exactly like the CLI (``openkb list-taxonomy``/``openkb search``) and
the query/chat agent already do (see ``fulltext_index`` module docstring).
This MCP server is typically a longer-lived process than a single CLI
invocation, but OpenKB has no long-running daemon/cache-invalidation concept
today — caching the index across calls here would risk staleness if the KB
changes via a separate ``openkb add`` while this process stays alive, so the
same fresh-per-call rebuild is used deliberately rather than introducing a
new caching model just for this surface.

Response size guard: every listing tool below caps its serialized result
at ``MAX_RESULT_BYTES``. A result over budget is never silently truncated
— that would look like the KB simply doesn't have more — instead the tool
returns an info payload naming the actual size and how to retry. For the
``list_*`` tools, where splitting by character or line makes no sense, the
full item list is cut into fixed-size pages (:func:`_split_into_pages`) and
the caller re-requests one page at a time via a 1-based ``page`` parameter
— the same idea as ``get_content``'s ``pages`` argument for a long source
document, just a single page number over a *result listing* rather than a
range over a document's own pre-existing pages. ``search_wiki``/
``search_taxonomy`` narrow only via their ``top_k``/``scope`` arguments (no
``page``) — for ranked results, asking for fewer top hits is the natural
knob, and ``search_wiki``'s four tiers each need independent top-k control
anyway, which a single shared page count could not express.
``get_content`` is exempt: it always returns a page's full content — the
only size control there is its
existing ``pages`` argument for a long PageIndex source.
"""

from __future__ import annotations

import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from openkb.agent.content import ContentEntry, get_content, get_kb_status, list_documents
from openkb.agent.tools import list_taxonomy_items
from openkb.config import load_global_config, registered_kbs, resolve_kb_alias
from openkb.fulltext_index import TaxonomySearch, TieredWikiSearch

mcp = FastMCP("openkb")

# MCP responses commonly land directly in an LLM caller's context window. A
# tool whose result size scales with wiki content (a taxonomy listing, a
# multi-tier search, ...) can reach tens of KB for a large KB, so every such
# tool checks its serialized size against this budget before returning.
MAX_RESULT_BYTES = 25 * 1024


def _result_size_bytes(result: object) -> int:
    """Size in bytes of *result* as the client would receive it (UTF-8 JSON)."""
    return len(json.dumps(result, default=str, ensure_ascii=False).encode("utf-8"))


def _oversized_result(size_bytes: int, hint: str, num_pages: int | None = None) -> dict:
    """Info payload returned instead of an over-budget tool result.

    Args:
        size_bytes: Actual serialized size of the result that was withheld.
        hint: Tool-specific guidance on how to split the request into
            smaller calls (which parameter to narrow or paginate with).
        num_pages: For the ``list_*`` tools, the fixed page count computed
            by :func:`_split_into_pages` — a structured field, not just
            mentioned in ``message``, so a caller doesn't have to parse
            prose to loop ``page=1..num_pages``. ``None`` for tools that
            don't paginate by whole page (``get_content``/``search_wiki``).
    """
    result: dict = {
        "error": "result_too_large",
        "size_bytes": size_bytes,
        "max_bytes": MAX_RESULT_BYTES,
        "message": (
            f"Result would be {size_bytes} bytes, over the {MAX_RESULT_BYTES}-byte MCP "
            f"response limit. {hint}"
        ),
    }
    if num_pages is not None:
        result["num_pages"] = num_pages
    return result


def find_kb_dir(start: Path | None = None) -> Path | None:
    """Resolve the active KB root: walk up from *start* (default cwd) looking
    for ``.openkb/``, else fall back to the global config's ``default_kb``.

    Mirrors ``openkb.cli._find_kb_dir``'s resolution order. Kept as a
    separate, lightweight copy here (rather than importing from ``cli.py``)
    so starting this MCP server doesn't pull in ``cli.py``'s much heavier
    import chain (click, litellm, the Agents SDK) just to resolve a
    directory path.
    """
    current = (start or Path.cwd()).resolve()
    while True:
        if (current / ".openkb").is_dir():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent

    gc = load_global_config()
    default = gc.get("default_kb")
    if default:
        candidate = Path(default)
        if (candidate / ".openkb").is_dir():
            return candidate
    return None


def _resolve_kb(kb: str | None) -> Path:
    """Resolve *kb* to a KB root directory, for a tool's optional ``kb`` arg.

    - ``None`` (default): today's behavior — :func:`find_kb_dir` (cwd-walk,
      then the global default). Fully backward compatible with a fixed
      ``"cwd"`` MCP client config (the only way to pick a KB before this).
    - An existing directory containing ``.openkb/``: used directly (mirrors
      the CLI's ``--kb-dir`` override).
    - Otherwise: resolved as a registered KB name/alias via
      :func:`openkb.config.resolve_kb_alias` — the same name->path registry
      the CLI's ``delete-kb`` and the REST API's ``/api/v1/kbs`` already
      use. Call :func:`list_kbs` to discover the available names.

    Raises:
        ValueError: *kb* doesn't resolve to a real KB by any of the above,
            or (*kb* is ``None`` and) no KB can be found at all.
    """
    if kb is None:
        kb_dir = find_kb_dir()
        if kb_dir is None:
            raise ValueError(
                "No knowledge base found. Run this from inside a KB directory "
                "(or a subdirectory of one), set a default with `openkb use "
                "<name>`, or pass an explicit kb=<name-or-path>."
            )
        return kb_dir

    candidate = Path(kb).expanduser()
    if candidate.is_dir() and (candidate / ".openkb").is_dir():
        return candidate.resolve()

    try:
        resolved = resolve_kb_alias(kb)
    except ValueError:
        resolved = None
    if resolved is not None and (resolved / ".openkb").is_dir():
        return resolved

    known = ", ".join(name for name, _ in registered_kbs()) or "(none registered)"
    raise ValueError(
        f"Unknown KB {kb!r}: not an existing KB directory and not a registered "
        f"KB name. Known KBs: {known}. Call list_kbs() to discover names."
    )


def _wiki_root(kb: str | None = None) -> Path:
    """Return the *kb* KB's ``wiki/`` directory (see :func:`_resolve_kb`)."""
    return _resolve_kb(kb) / "wiki"


def _split_into_pages(items: list[dict]) -> list[list[dict]]:
    """Greedily pack *items* into fixed pages that each fit ``MAX_RESULT_BYTES``.

    Deterministic for a given item list (same KB content -> same page
    boundaries), so a caller can request ``page=2`` and reliably get the
    same slice on a later call. A single pathological item bigger than the
    whole budget still becomes its own (oversized) page rather than being
    split mid-item — there is nothing smaller to fall back to.
    """
    pages: list[list[dict]] = []
    current: list[dict] = []
    for item in items:
        candidate = current + [item]
        if current and _result_size_bytes(candidate) > MAX_RESULT_BYTES:
            pages.append(current)
            current = [item]
        else:
            current = candidate
    if current:
        pages.append(current)
    return pages


def _paginate_list(items: list[dict], page: int | None, hint: str) -> list[dict] | dict:
    """Return *items* whole, one fixed page of it, or an oversized-result info payload.

    Mirrors ``get_content``'s ``pages`` argument, but for a *result listing*
    rather than a long source document's own pages: a single 1-based page
    number (not a range), and the pages are computed here rather than fixed
    at indexing time. Splitting a listing by byte/line offset would cut mid
    item; splitting into whole items per page does not.

    Args:
        items: The full, unpaginated list of plain dicts.
        page: 1-based page to return; ``None`` means "give me everything if
            it fits, otherwise tell me how many pages there are."
        hint: Name of the listing, e.g. ``"list_taxonomy"`` (for the message).
    """
    size = _result_size_bytes(items)
    if size <= MAX_RESULT_BYTES:
        return items

    pages = _split_into_pages(items)
    num_pages = len(pages)
    if page is None:
        return _oversized_result(
            size,
            f"{hint} has {len(items)} total item(s), too large for one response. "
            f"Split into {num_pages} page(s) — call again with page=1, then "
            f"page=2, ... up to page={num_pages}.",
            num_pages=num_pages,
        )
    if page < 1 or page > num_pages:
        return _oversized_result(
            size,
            f"{hint} has {num_pages} page(s) total; page={page} is out of range. "
            f"Call again with a page between 1 and {num_pages}.",
            num_pages=num_pages,
        )
    return pages[page - 1]


@mcp.tool()
def list_kbs(page: int | None = None) -> list[dict] | dict:
    """List every KB this MCP server can address via the ``kb`` parameter.

    Args:
        page: 1-based page to return if the full listing is too large; omit
            to get everything (or, if too large, a page count to choose from).

    Returns:
        One dict per registered KB (``name`` — pass as ``kb=name`` to any
        other tool — and ``path``, its absolute KB root directory), or, if
        the serialized result would exceed ~25 KB, a
        ``{"error": "result_too_large", ...}`` payload naming the page count.
    """
    result = [{"name": name, "path": str(path)} for name, path in registered_kbs()]
    return _paginate_list(result, page, hint="list_kbs")


@mcp.tool()
def get_status(kb: str | None = None) -> dict:
    """Return the active KB's absolute path and basic content counts.

    Closes the one gap the other tools can't: they return wiki-root-relative
    paths (e.g. ``"concepts/attention.md"``), but nothing else reveals the
    absolute KB path a client needs to resolve one — call this first if you
    don't already know it (mirrors ``openkb status`` for MCP-only clients
    with no shell access).

    Args:
        kb: Registered KB name/alias or absolute path; omit to use the KB
            resolved from the server's cwd or global default (see
            :func:`_resolve_kb`).

    Returns:
        ``kb_dir`` (absolute path), ``counts`` (``.md`` file count per wiki
        subdirectory, plus ``"raw"`` if present), and ``total_indexed``
        (documents in the ``.openkb/hashes.json`` registry).
    """
    status = get_kb_status(str(_resolve_kb(kb)))
    result = {
        "kb_dir": status.kb_dir,
        "counts": status.counts,
        "total_indexed": status.total_indexed,
    }
    size = _result_size_bytes(result)
    if size > MAX_RESULT_BYTES:
        return _oversized_result(size, "get_status takes no parameters to narrow this further.")
    return result


@mcp.tool()
def list_taxonomy(
    kind: str | None = None, page: int | None = None, kb: str | None = None
) -> list[dict] | dict:
    """List persisted concept/entity pages with their one-line briefs.

    Semantic browsing, not keyword search: pick the slug(s) that match the
    question's meaning by their brief, then fetch the full page with
    ``get_content(slug, kind=...)``.

    Args:
        kind: Restrict to "concept" or "entity"; omit for both.
        page: 1-based page to return if the full listing is too large; omit
            to get everything (or, if too large, a page count to choose
            from). Mirrors ``get_content``'s ``pages`` idea, one page number
            over this listing rather than a range over a document's pages.
        kb: Registered KB name/alias or absolute path; omit to use the KB
            resolved from the server's cwd or global default.

    Returns:
        One dict per item (``kind``, ``slug``, ``path``, ``brief``, and
        ``type`` — entity type, or ``None`` for concepts), or, if the
        serialized result would exceed ~25 KB, a
        ``{"error": "result_too_large", ...}`` payload naming the page count.
    """
    items = list_taxonomy_items(str(_wiki_root(kb)), kind=kind)
    result = [
        {"kind": i.kind, "slug": i.slug, "path": i.path, "brief": i.brief, "type": i.type}
        for i in items
    ]
    return _paginate_list(result, page, hint="list_taxonomy")


@mcp.tool()
def search_taxonomy(
    query: str,
    kind: str | None = None,
    top_k: int = 20,
    kb: str | None = None,
) -> list[dict] | dict:
    """Rank concept/entity pages by BM25 match against their slug + one-line brief.

    Complements ``list_taxonomy``'s plain browse listing: for a KB with too
    many taxonomy items to scan by eye, this ranks them by relevance to
    *query* instead. Deliberately narrow — matched only against each page's
    slug (readable form) and brief, never its full body, unlike
    ``search_wiki`` over summaries/sources/explorations. A query that only
    matches text buried in a concept/entity page's body won't surface it
    here; read the page itself (``get_content``) for that.

    Args:
        query: Free-text search query (keywords or a natural-language question).
        kind: Restrict to "concept" or "entity"; omit for both.
        top_k: Maximum ranked results to return — the only narrowing knob
            (no ``page`` parameter; lower this if the result is flagged too
            large). Deliberately much higher than ``search_wiki``'s
            ``top_k=5`` default — a brief is short, so more hits cost
            little, and this is the primary way to narrow a large taxonomy
            instead of browsing every page via ``list_taxonomy``.
        kb: Registered KB name/alias or absolute path; omit to use the KB
            resolved from the server's cwd or global default.

    Returns:
        One dict per hit, highest score first (``kind``, ``slug``, ``path``,
        ``brief``, ``type``, ``score``), or, if the serialized result would
        exceed ~25 KB, a ``{"error": "result_too_large", ...}`` payload
        naming a smaller ``top_k`` to retry with.
    """
    hits = TaxonomySearch(str(_wiki_root(kb)), kind=kind).search(query, top_k=top_k)
    result = [
        {
            "kind": h.kind,
            "slug": h.slug,
            "path": h.path,
            "brief": h.brief,
            "type": h.type,
            "score": h.score,
        }
        for h in hits
    ]
    size = _result_size_bytes(result)
    if size <= MAX_RESULT_BYTES:
        return result
    suggested = max(1, top_k // 2)
    return _oversized_result(
        size,
        f"search_taxonomy returned {len(result)} hit(s) for top_k={top_k}. "
        f"Retry with a smaller `top_k` (e.g. top_k={suggested}).",
    )


@mcp.tool(name="list_documents")
def list_documents_tool(
    kind: str | None = None, page: int | None = None, kb: str | None = None
) -> list[dict] | dict:
    """List persisted summary/exploration pages with their one-line briefs.

    Args:
        kind: Restrict to "summary" or "exploration"; omit for both.
        page: 1-based page to return if the full listing is too large; omit
            to get everything (or, if too large, a page count to choose
            from). Mirrors ``get_content``'s ``pages`` idea, one page number
            over this listing rather than a range over a document's pages.
        kb: Registered KB name/alias or absolute path; omit to use the KB
            resolved from the server's cwd or global default.

    Returns:
        One dict per item (``kind``, ``slug``, ``path``, and ``brief`` — an
        exploration's brief is its originally-saved question), or, if the
        serialized result would exceed ~25 KB, a
        ``{"error": "result_too_large", ...}`` payload naming the page count.
    """
    items = list_documents(str(_wiki_root(kb)), kind=kind)
    result = [{"kind": i.kind, "slug": i.slug, "path": i.path, "brief": i.brief} for i in items]
    return _paginate_list(result, page, hint="list_documents")


def _content_entry_to_dict(entry: ContentEntry) -> dict:
    return {"kind": entry.kind, "path": entry.path, "content": entry.content, "error": entry.error}


@mcp.tool(name="get_content")
def get_content_tool(
    slug: str,
    kind: str | None = None,
    pages: str | None = None,
    kb: str | None = None,
) -> list[dict]:
    """Read wiki content by slug — one tool for every content kind.

    Args:
        slug: Page slug (filename without extension), e.g. ``"attention"``.
            For "source", identical to the paired summary's slug — a plain
            call without ``kind`` commonly returns both as separate entries.
        kind: One of "concept", "entity", "summary", "exploration",
            "source", "report", "index". Omit to search all seven and get
            one entry per match found (0, 1, or several).
        pages: Only meaningful for a "source" match — required for a long
            (PageIndex) document (e.g. ``"3-5,7"``), forbidden otherwise.
        kb: Registered KB name/alias or absolute path; omit to use the KB
            resolved from the server's cwd or global default.

    Returns:
        One dict per match — always a list, even for a single match:
        ``kind``, ``path``, ``content`` (``None`` on error), and ``error``
        (``None`` on success — e.g. a long PageIndex document without
        ``pages`` set gets an error explaining what to pass instead of
        content). Not subject to the response-size guard — a full page's
        content is always returned as-is; for a long PageIndex "source",
        use ``pages`` to fetch a smaller range instead of the whole document.
    """
    entries = get_content(slug, str(_wiki_root(kb)), kind=kind, pages=pages)
    return [_content_entry_to_dict(e) for e in entries]


@mcp.tool()
def search_wiki(
    query: str, scope: list[str] | None = None, top_k: int = 5, kb: str | None = None
) -> dict:
    """Tiered full-text (BM25) search over summaries/sources/explorations.

    Never covers concepts/entities — use ``list_taxonomy`` for those. Use
    this in addition to, not instead of, taxonomy browsing: a hybrid
    fallback for a specific buried detail (a niche term, an exact figure, an
    author/creation-date only present in a raw source).

    Args:
        query: Free-text search query (keywords or a natural-language question).
        scope: Restrict to a subset of "briefs" (one-line document
            summaries), "summaries" (full document-summary text), "sources"
            (raw source files, including per-page indexing of long
            PageIndex documents), "explorations" (saved query answers);
            omit to search all four.
        top_k: Maximum ranked results to return per tier — the pagination
            knob for this tool; lower it if the result is flagged too large.
        kb: Registered KB name/alias or absolute path; omit to use the KB
            resolved from the server's cwd or global default.

    Returns:
        ``{tier: [hit, ...]}`` for each searched tier. Each hit has
        ``path``, ``title``, ``score``, ``snippet``, and ``locator``
        (``{"kind": "line"|"page", "value": int}`` or ``None``) — a "page"
        locator names the exact PageIndex page to fetch for that document.
        If the serialized result would exceed ~25 KB, returns a single
        ``{"error": "result_too_large", ...}`` payload instead, naming the
        actual size and a smaller ``top_k``/narrower ``scope`` to retry with.
    """
    results = TieredWikiSearch(str(_wiki_root(kb))).search(query, scope=scope, top_k=top_k)
    result = {
        tier: [
            {
                "path": hit.path,
                "title": hit.title,
                "score": hit.score,
                "snippet": hit.snippet,
                "locator": (
                    {"kind": hit.locator.kind, "value": hit.locator.value} if hit.locator else None
                ),
            }
            for hit in hits
        ]
        for tier, hits in results.items()
    }
    size = _result_size_bytes(result)
    if size <= MAX_RESULT_BYTES:
        return result
    suggested = max(1, top_k // 2)
    total_hits = sum(len(hits) for hits in result.values())
    return _oversized_result(
        size,
        f"search_wiki returned {total_hits} hit(s) across {len(result)} tier(s) for "
        f"top_k={top_k}. Retry with a smaller `top_k` (e.g. top_k={suggested}) or a "
        f"narrower `scope`.",
    )


def main() -> None:
    """Entry point for the ``openkb-mcp`` console script (stdio transport)."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
