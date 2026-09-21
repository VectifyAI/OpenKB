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
"""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from openkb.agent.content import ContentEntry, get_content, get_kb_status, list_documents
from openkb.agent.tools import list_taxonomy_items
from openkb.config import load_global_config, registered_kbs, resolve_kb_alias
from openkb.fulltext_index import TieredWikiSearch

mcp = FastMCP("openkb")


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


@mcp.tool()
def list_kbs() -> list[dict]:
    """List every KB this MCP server can address via the ``kb`` parameter.

    Returns:
        One dict per registered KB: ``name`` (pass as ``kb=name`` to any
        other tool) and ``path`` (its absolute KB root directory).
    """
    return [{"name": name, "path": str(path)} for name, path in registered_kbs()]


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
    return {"kb_dir": status.kb_dir, "counts": status.counts, "total_indexed": status.total_indexed}


@mcp.tool()
def list_taxonomy(kind: str | None = None, kb: str | None = None) -> list[dict]:
    """List persisted concept/entity pages with their one-line briefs.

    Semantic browsing, not keyword search: pick the slug(s) that match the
    question's meaning by their brief, then fetch the full page with
    ``get_content(slug, kind=...)``.

    Args:
        kind: Restrict to "concept" or "entity"; omit for both.
        kb: Registered KB name/alias or absolute path; omit to use the KB
            resolved from the server's cwd or global default.

    Returns:
        One dict per item: ``kind``, ``slug``, ``path``, ``brief``, and
        ``type`` (entity type, or ``None`` for concepts).
    """
    items = list_taxonomy_items(str(_wiki_root(kb)), kind=kind)
    return [
        {"kind": i.kind, "slug": i.slug, "path": i.path, "brief": i.brief, "type": i.type}
        for i in items
    ]


@mcp.tool(name="list_documents")
def list_documents_tool(kind: str | None = None, kb: str | None = None) -> list[dict]:
    """List persisted summary/exploration pages with their one-line briefs.

    Args:
        kind: Restrict to "summary" or "exploration"; omit for both.
        kb: Registered KB name/alias or absolute path; omit to use the KB
            resolved from the server's cwd or global default.

    Returns:
        One dict per item: ``kind``, ``slug``, ``path``, and ``brief``
        (an exploration's brief is its originally-saved question).
    """
    items = list_documents(str(_wiki_root(kb)), kind=kind)
    return [{"kind": i.kind, "slug": i.slug, "path": i.path, "brief": i.brief} for i in items]


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
        content).
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
        top_k: Maximum ranked results to return per tier.
        kb: Registered KB name/alias or absolute path; omit to use the KB
            resolved from the server's cwd or global default.

    Returns:
        ``{tier: [hit, ...]}`` for each searched tier. Each hit has
        ``path``, ``title``, ``score``, ``snippet``, and ``locator``
        (``{"kind": "line"|"page", "value": int}`` or ``None``) — a "page"
        locator names the exact PageIndex page to fetch for that document.
    """
    results = TieredWikiSearch(str(_wiki_root(kb))).search(query, scope=scope, top_k=top_k)
    return {
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


def main() -> None:
    """Entry point for the ``openkb-mcp`` console script (stdio transport)."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
