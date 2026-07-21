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

from openkb import frontmatter
from openkb.lint import (
    _EXCLUDED_FILES,
    _extract_wikilinks,
    _normalize_target,
    _read_md,
    fix_broken_links,
    list_existing_wiki_targets,
    strip_ghost_wikilinks,
)
from openkb.locks import atomic_write_text, kb_ingest_lock

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
    index-entry removal, not link demotion), ``reports/`` + ``sources/``, and
    :data:`lint._EXCLUDED_FILES` (AGENTS.md/SCHEMA.md/log.md) — the generated
    docs lint never rewrites; scanning them would list the shipped schema doc as
    a backlink and then let ``fix_broken_links`` corrupt it.
    Returns relative ``'section/stem'`` refs, sorted, for a stable impact preview.
    """
    hits: set[str] = set()
    exclude_resolved = exclude.resolve()
    for md in wiki.rglob("*.md"):
        rel = md.relative_to(wiki)
        if rel.parts[:1] in (("reports",), ("sources",)):
            continue
        if md.name == "index.md" or md.name in _EXCLUDED_FILES or md.resolve() == exclude_resolved:
            continue
        for raw in _extract_wikilinks(_read_md(md)):
            if _normalize_target(raw) == target_norm:
                hits.add(str(rel.with_suffix("")).replace("\\", "/"))
                break
    return sorted(hits)


def _on_disk_stem(section_dir: Path, stem: str) -> str:
    """The actual on-disk stem for ``stem``.

    A case-insensitive filesystem (macOS/Windows) may store the real filename
    with different case than the ref the caller resolved (e.g. opened via a
    ``[[concepts/foo]]`` wikilink while the file is ``Foo.md``). index.md entries
    use the on-disk name, so the exact-match index cleanup needs the real stem.
    """
    if not section_dir.is_dir():
        return stem
    ci_match = stem
    for p in section_dir.glob("*.md"):
        if p.stem == stem:  # exact — prefer it (case-sensitive FS, both variants)
            return stem
        if p.stem.lower() == stem.lower():
            ci_match = p.stem
    return ci_match


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

    if dry_run:
        # Preview only — no mutation, so an unlocked scan is fine.
        backlinks = pages_linking_to(wiki, _normalize_target(target), exclude=page)
        return {"status": "dry_run", "target": target, "backlinks": backlinks}

    # Imported lazily: compiler pulls in the LLM stack, which this pure
    # index-editing helper does not otherwise need at module import.
    from openkb.agent.compiler import remove_doc_from_index

    with kb_ingest_lock(kb_dir / ".openkb"):
        # Re-check under the lock: a concurrent delete/recompile may have removed
        # the page in the window since the pre-lock is_file() gate.
        if not page.is_file():
            return {"status": "not_found", "target": target, "backlinks": []}
        # Compute backlinks INSIDE the lock so a concurrently-added inbound link
        # is still demoted (matches run_remove_for_api's plan-inside-the-lock).
        backlinks = pages_linking_to(wiki, _normalize_target(target), exclude=page)
        # Remove the page's index.md entry (a [[link]] LINE — full removal, not
        # link demotion). Empty doc_name leaves the Documents section alone; use
        # the ACTUAL on-disk stem so the exact-match cleanup works on a
        # case-insensitive FS where the ref may differ in case.
        real_stem = _on_disk_stem(wiki / section, stem)
        page.unlink(missing_ok=True)
        remove_doc_from_index(
            wiki,
            "",
            concept_slugs_deleted=[real_stem] if section == "concepts" else [],
            entity_slugs_deleted=[real_stem] if section == "entities" else [],
        )
        # Demote now-dangling inbound [[links]] to plain text — in the backlink
        # pages AND in index.md (a [[target]] embedded in another entry's brief;
        # the page's own entry line was already removed above). Surgical restrict
        # leaves pre-existing dangling links elsewhere untouched (like remove).
        restrict = [wiki / f"{ref}.md" for ref in backlinks] + [wiki / "index.md"]
        files_changed, ghosts_stripped = fix_broken_links(wiki, restrict_to=restrict)

    return {
        "status": "deleted",
        "target": target,
        "backlinks": backlinks,
        "files_changed": files_changed,
        "ghosts_stripped": ghosts_stripped,
    }


def page_link_context(kb_dir: Path, path: str) -> dict:
    """Outbound + inbound links for a page — the edit-impact panel.

    ``outlinks`` are the distinct pages this page links to (resolvable
    ``[[wikilinks]]``); ``backlinks`` are the pages that link to it. Editing the
    BODY does not break either (links are path-based), so this is context, not a
    blocker. Returns ``status`` ``not_found`` when the page is absent.
    """
    section, stem = validate_page_ref(path)
    wiki = kb_dir / "wiki"
    page = wiki / section / f"{stem}.md"
    target = f"{section}/{stem}"
    if not page.is_file():
        return {"status": "not_found", "target": target, "outlinks": [], "backlinks": []}

    target_norm = _normalize_target(target)
    norm_known = {_normalize_target(t): t for t in list_existing_wiki_targets(wiki)}
    out: set[str] = set()
    for raw in _extract_wikilinks(_read_md(page)):
        canon = norm_known.get(_normalize_target(raw))
        if canon and _normalize_target(canon) != target_norm:
            out.add(canon)
    backlinks = pages_linking_to(wiki, target_norm, exclude=page)
    return {"status": "ok", "target": target, "outlinks": sorted(out), "backlinks": backlinks}


def edit_wiki_page(kb_dir: Path, path: str, content: str) -> dict:
    """Replace a concept/entity page's BODY, preserving its OKF frontmatter.

    The ``type:``/``description:``/``sources:`` frontmatter is code-managed, so
    any frontmatter in the incoming ``content`` is discarded and the existing
    block is re-attached verbatim. Dead ``[[wikilinks]]`` the user typed are
    demoted to plain text on save (OpenKB keeps the wiki free of broken links);
    the demoted targets are returned so the UI can warn. Write is atomic, under
    the KB ingest lock. Returns ``status`` ``not_found`` when the page is absent.
    """
    section, stem = validate_page_ref(path)
    wiki = kb_dir / "wiki"
    page = wiki / section / f"{stem}.md"
    target = f"{section}/{stem}"
    if not page.is_file():
        return {"status": "not_found", "target": target, "ghosts_stripped": []}

    with kb_ingest_lock(kb_dir / ".openkb"):
        # Re-check under the lock: never resurrect a page a concurrent
        # delete/recompile removed in the pre-lock window (atomic_write would
        # otherwise recreate the file). Read + demote + write all run here so
        # the op is atomic against its own inputs (no lost-update).
        if not page.is_file():
            return {"status": "not_found", "target": target, "ghosts_stripped": []}
        existing = frontmatter.split(_read_md(page))
        fm_block = existing[0] if existing else ""
        # `content` IS the body: the reader strips the code-managed OKF
        # frontmatter before editing, so it is NEVER re-split here — a body that
        # legitimately opens with a '---' block must not be mistaken for
        # frontmatter and silently dropped. The existing block is re-attached.
        cleaned_body, ghosts = strip_ghost_wikilinks(content, list_existing_wiki_targets(wiki))
        atomic_write_text(page, fm_block + cleaned_body)

    return {
        "status": "saved",
        "target": target,
        "ghosts_stripped": sorted(set(ghosts)),
        "content": fm_block + cleaned_body,
    }
