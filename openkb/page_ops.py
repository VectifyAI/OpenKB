"""Wiki-page mutations for the Workbench: delete a concept/entity page.

Reuses the compile/lint machinery so a deletion degrades cleanly rather than
leaving dangling references:

- inbound ``[[wikilinks]]`` in other pages are demoted to plain text
  (``lint.fix_broken_links``), not left pointing at a gone page;
- the page's ``index.md`` entry (itself a ``[[wikilink]]`` line) is removed
  outright (``compiler.remove_doc_from_index``).

All writes run under the KB ingest lock (crash-safe serial mutation, matching
``openkb remove``); the underlying rewrites use ``atomic_write_text``.
"""

from __future__ import annotations

from pathlib import Path

from openkb.lint import _extract_wikilinks, _normalize_target, _read_md, fix_broken_links
from openkb.locks import kb_ingest_lock

# Only these compiled page types are user-deletable/editable. summaries are
# per-source-document (managed by add/remove); index/log/reports are generated.
EDITABLE_SECTIONS = ("concepts", "entities")


def validate_page_ref(path: str) -> tuple[str, str]:
    """Split a ``'<section>/<name>'`` page ref into ``(section, stem)``.

    Guards against path traversal: ``section`` must be an editable section and
    ``stem`` a single safe filename segment (no separators, ``..``, or leading
    dot). Raises :class:`ValueError` otherwise.
    """
    parts = path.strip().strip("/").split("/")
    if len(parts) != 2:
        raise ValueError("page ref must be '<section>/<name>' (e.g. concepts/attention)")
    section, stem = parts
    if section not in EDITABLE_SECTIONS:
        raise ValueError(f"section must be one of {EDITABLE_SECTIONS}, got {section!r}")
    if not stem or stem in (".", "..") or stem.startswith(".") or "\\" in stem:
        raise ValueError(f"invalid page name: {stem!r}")
    return section, stem


def pages_linking_to(wiki: Path, target_norm: str, *, exclude: Path) -> list[str]:
    """Content pages whose ``[[wikilinks]]`` resolve to ``target_norm`` (backlinks).

    Excludes ``exclude`` (the target page itself), ``index.md`` (handled by the
    index-entry removal, not link demotion), and ``reports/`` + ``sources/``.
    Returns relative ``'section/stem'`` refs, sorted, for a stable impact preview.
    """
    hits: set[str] = set()
    exclude_resolved = exclude.resolve()
    for md in wiki.rglob("*.md"):
        rel = md.relative_to(wiki)
        if rel.parts[:1] in (("reports",), ("sources",)):
            continue
        if md.name == "index.md" or md.resolve() == exclude_resolved:
            continue
        for raw in _extract_wikilinks(_read_md(md)):
            if _normalize_target(raw) == target_norm:
                hits.add(str(rel.with_suffix("")).replace("\\", "/"))
                break
    return sorted(hits)


def delete_wiki_page(kb_dir: Path, path: str, *, dry_run: bool = False) -> dict:
    """Delete a concept/entity page and clean up references to it.

    ``path`` is a ``'section/stem'`` ref (e.g. ``'concepts/attention'``). With
    ``dry_run`` it only reports the impacted backlink pages (whose inbound links
    WOULD be demoted). Returns a dict whose ``status`` is one of ``not_found``,
    ``dry_run``, ``deleted``.
    """
    section, stem = validate_page_ref(path)
    wiki = kb_dir / "wiki"
    page = wiki / section / f"{stem}.md"
    target = f"{section}/{stem}"

    if not page.is_file():
        return {"status": "not_found", "target": target, "backlinks": []}

    backlinks = pages_linking_to(wiki, _normalize_target(target), exclude=page)
    if dry_run:
        return {"status": "dry_run", "target": target, "backlinks": backlinks}

    # Imported lazily: compiler pulls in the LLM stack, which this pure
    # index-editing helper does not otherwise need at module import.
    from openkb.agent.compiler import remove_doc_from_index

    with kb_ingest_lock(kb_dir / ".openkb"):
        page.unlink()
        # Remove the page's index.md entry (a [[link]] LINE — full removal,
        # not link demotion). Empty doc_name leaves the Documents section alone.
        remove_doc_from_index(
            wiki,
            "",
            concept_slugs_deleted=[stem] if section == "concepts" else [],
            entity_slugs_deleted=[stem] if section == "entities" else [],
        )
        # Demote the now-dangling inbound [[links]] to plain text — surgically,
        # only in the pages that referenced this one (matches `openkb remove`,
        # so pre-existing dangling links elsewhere are left untouched).
        restrict = [wiki / f"{ref}.md" for ref in backlinks]
        files_changed, ghosts_stripped = fix_broken_links(wiki, restrict_to=restrict)

    return {
        "status": "deleted",
        "target": target,
        "backlinks": backlinks,
        "files_changed": files_changed,
        "ghosts_stripped": ghosts_stripped,
    }
