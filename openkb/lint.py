"""Structural lint checks for the OpenKB wiki.

Checks for:
- Broken [[wikilinks]] — link targets that don't exist
- Orphaned pages — pages with no incoming or outgoing links
- Missing wiki entries — raw files without corresponding sources/summaries
- Index sync — index.md links vs actual files on disk
- Invalid frontmatter — YAML that won't round-trip through safe_load
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import yaml

from openkb import frontmatter
from openkb.schema import PAGE_CONTENT_DIRS

# Matches [[wikilink]] or [[subdir/link]]
_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")

# Files to exclude from lint scanning (schema, logs, etc.)
_EXCLUDED_FILES = {"AGENTS.md", "SCHEMA.md", "log.md"}


def _normalize_target(target: str) -> str:
    """Normalize a wikilink target for fuzzy comparison.

    Applies, in order:
    - NFKC unicode normalization (e.g. full-width '）' → ASCII ')')
    - Lowercase
    - Underscore → hyphen
    - Collapse repeated hyphens
    - Strip leading/trailing hyphens (per segment when path-like)

    Path separators are preserved so ``concepts/Gist_Memory`` normalizes to
    ``concepts/gist-memory``.
    """
    s = unicodedata.normalize("NFKC", target)
    s = s.lower().replace("_", "-")
    # Normalize each path segment independently to avoid collapsing the '/'
    parts = [re.sub(r"-+", "-", p).strip("-") for p in s.split("/")]
    return "/".join(parts)


def build_norm_index(known_targets: set[str]) -> dict[str, str]:
    """Build the normalized-form → canonical-target index used by
    :func:`strip_ghost_wikilinks`.

    Useful when calling ``strip_ghost_wikilinks`` repeatedly with the same
    ``known_targets`` (e.g. ``fix_broken_links`` scanning N wiki files, or
    ``_save_transcript`` stripping N assistant turns) — build the index
    once and pass it via the ``norm_index`` parameter to avoid O(N·M)
    redundant rebuilds.
    """
    return {_normalize_target(t): t for t in known_targets}


def strip_ghost_wikilinks(
    content: str,
    known_targets: set[str],
    *,
    norm_index: dict[str, str] | None = None,
) -> tuple[str, list[str]]:
    """Strip [[wikilinks]] whose targets do not exist in ``known_targets``.

    For each ``[[X]]`` or ``[[X|alias]]`` in ``content``:

    - If ``X`` is in ``known_targets`` exactly, the link is kept as-is.
    - Otherwise, ``X`` is normalized (see :func:`_normalize_target`) and
      matched against the normalized form of each known target. On a hit,
      the link is rewritten to the canonical target form.
    - Otherwise, the brackets are removed and the link becomes plain text
      (the alias if provided, otherwise the slug rendered as words).

    Args:
        content: Markdown text containing zero or more ``[[wikilinks]]``.
        known_targets: Valid link targets, e.g.
            ``{"concepts/attention", "summaries/paper"}``.
        norm_index: Optional pre-built normalized index from
            :func:`build_norm_index`. Pass this when calling in a loop
            with the same ``known_targets`` to skip redundant rebuilds.

    Returns:
        Tuple of ``(rewritten_content, ghost_targets)`` where
        ``ghost_targets`` is the list of unresolved targets that were
        stripped (one entry per occurrence, in document order).
    """
    if norm_index is None:
        norm_index = build_norm_index(known_targets)

    ghosts: list[str] = []

    def _repl(m: re.Match) -> str:
        raw = m.group(1)
        if "|" in raw:
            target, alias = raw.split("|", 1)
            target = target.strip()
            alias = alias.strip()
        else:
            target = raw.strip()
            alias = None

        # Direct hit
        if target in known_targets:
            return m.group(0)

        # Fuzzy normalized hit → rewrite to canonical
        canonical = norm_index.get(_normalize_target(target))
        if canonical is not None:
            if alias:
                return f"[[{canonical}|{alias}]]"
            return f"[[{canonical}]]"

        # Ghost — strip brackets, leave readable display
        ghosts.append(target)
        if alias:
            return alias
        stem = target.rsplit("/", 1)[-1]
        return stem.replace("-", " ").replace("_", " ")

    cleaned = _WIKILINK_RE.sub(_repl, content)
    return cleaned, ghosts


def _read_md(path: Path) -> str:
    """Read a Markdown file safely, returning empty string on error."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _all_wiki_pages(wiki: Path) -> dict[str, Path]:
    """Return a mapping of stem/relative-path → absolute Path for all .md files.

    Keys are normalized: 'concepts/attention', 'summaries/paper', 'index', etc.
    """
    pages: dict[str, Path] = {}
    for md in wiki.rglob("*.md"):
        rel = md.relative_to(wiki)
        # Store both the full relative path without extension and the stem
        key = str(rel.with_suffix("")).replace("\\", "/")
        pages[key] = md
        # Also index by stem alone for convenience
        pages[md.stem] = md
    return pages


def _extract_wikilinks(text: str) -> list[str]:
    """Return all wikilink targets found in *text*.

    Handles ``[[target|display text]]`` alias syntax — only the target is returned.
    """
    raw = _WIKILINK_RE.findall(text)
    return [link.split("|")[0].strip() for link in raw]


def list_existing_wiki_targets(wiki_dir: Path) -> set[str]:
    """Return the set of currently-existing wikilink targets on disk.

    Includes every ``concepts/{stem}`` and ``summaries/{stem}`` for .md files
    actually present in the wiki, plus ``index`` when ``index.md`` exists.
    Used to seed the whitelist passed to :func:`strip_ghost_wikilinks` from
    both the compile pipeline and any other code path that writes
    LLM-generated content to the wiki (e.g. ``openkb query --save``).
    """
    targets: set[str] = set()
    concepts_dir = wiki_dir / "concepts"
    summaries_dir = wiki_dir / "summaries"
    if concepts_dir.is_dir():
        targets.update(f"concepts/{p.stem}" for p in concepts_dir.glob("*.md"))
    if summaries_dir.is_dir():
        targets.update(f"summaries/{p.stem}" for p in summaries_dir.glob("*.md"))
    entities_dir = wiki_dir / "entities"
    if entities_dir.is_dir():
        targets.update(f"entities/{p.stem}" for p in entities_dir.glob("*.md"))
    if (wiki_dir / "index.md").exists():
        targets.add("index")
    return targets


def fix_broken_links(
    wiki: Path,
    *,
    restrict_to: list[Path] | None = None,
) -> tuple[int, int]:
    """Rewrite or strip broken [[wikilinks]] across the wiki in place.

    For each Markdown page under ``wiki`` (excluding ``reports/`` and
    ``sources/`` and excluded files), runs :func:`strip_ghost_wikilinks`
    against the set of valid targets currently on disk. Targets that match
    fuzzily (case, ``_`` vs ``-``, NFKC) are rewritten to canonical form;
    targets that have no match are demoted to plain text.

    Args:
        wiki: Path to the wiki root directory.
        restrict_to: When provided, only rewrite these files (must live
            under ``wiki``). Paths outside the wiki and non-existent
            paths are silently skipped. An empty list is a no-op — the
            valid-target whitelist is still computed from the entire
            wiki, so links like ``[[concepts/sibling]]`` resolve
            correctly even when ``sibling.md`` is not in the scope.
            Used by ``openkb remove`` (issue #58 / Bug 2) to clean only
            the pages it actually touched instead of sweeping the
            whole wiki and stripping pre-existing dangling links the
            user may want to keep.

    Returns:
        Tuple of ``(files_changed, ghosts_stripped)``.
    """
    pages = _all_wiki_pages(wiki)
    # The same fuzzy normalization _all_wiki_pages stores both the full
    # relative path (e.g. ``concepts/attention``) and the bare stem
    # (``attention``). Use the full-path keys so that links like
    # ``[[concepts/foo]]`` resolve against ``concepts/`` files only.
    known_targets: set[str] = {
        key for key in pages if "/" in key or key == "index"
    }
    # Build the normalized index once and reuse across every file —
    # otherwise strip_ghost_wikilinks would rebuild it per file (O(F·M)).
    norm_index = build_norm_index(known_targets)

    if restrict_to is None:
        candidates: list[Path] = [
            md for md in wiki.rglob("*.md")
            if md.name not in _EXCLUDED_FILES
            and md.relative_to(wiki).parts[:1] not in (("reports",), ("sources",))
        ]
    else:
        wiki_resolved = wiki.resolve()
        candidates = []
        for raw in restrict_to:
            if not raw.is_file():
                continue
            try:
                raw.resolve().relative_to(wiki_resolved)
            except ValueError:
                continue  # outside wiki — skip silently
            candidates.append(raw)

    files_changed = 0
    ghosts_stripped = 0
    for md in candidates:
        text = _read_md(md)
        cleaned, ghosts = strip_ghost_wikilinks(
            text, known_targets, norm_index=norm_index,
        )
        if cleaned != text:
            md.write_text(cleaned, encoding="utf-8")
            files_changed += 1
            ghosts_stripped += len(ghosts)
    return files_changed, ghosts_stripped


def find_broken_links(wiki: Path) -> list[str]:
    """Scan all wiki pages for [[wikilinks]] pointing to non-existent targets.

    Args:
        wiki: Path to the wiki root directory.

    Returns:
        List of error strings describing each broken link.
    """
    pages = _all_wiki_pages(wiki)
    errors: list[str] = []

    for md in wiki.rglob("*.md"):
        if md.name in _EXCLUDED_FILES:
            continue
        # Skip reports/ and sources/ — auto-generated, not wiki content
        rel_parts = md.relative_to(wiki).parts
        if rel_parts and rel_parts[0] in ("reports", "sources"):
            continue
        text = _read_md(md)
        for target in _extract_wikilinks(text):
            # Normalise target: strip leading/trailing whitespace and slashes
            target_norm = target.strip().strip("/")
            # Check if target resolves as a key in our page map
            if target_norm not in pages:
                rel = md.relative_to(wiki)
                errors.append(f"Broken link [[{target}]] in {rel}")

    return sorted(errors)


def find_orphans(wiki: Path) -> list[str]:
    """Find pages that have no links to or from other pages.

    A page is orphaned if:
    - No other page links to it (no incoming links), AND
    - It has no outgoing wikilinks itself.

    index.md is excluded from orphan detection.

    Args:
        wiki: Path to the wiki root directory.

    Returns:
        List of relative page paths that are orphaned.
    """
    # Exclude index, schema, log, and sources/ (sources are auto-generated, not expected to be linked)
    all_mds = [
        p for p in wiki.rglob("*.md")
        if p.name not in {"index.md", *_EXCLUDED_FILES}
        and "sources" not in p.relative_to(wiki).parts
    ]
    if not all_mds:
        return []

    # Build outgoing links per page
    outgoing: dict[str, set[str]] = {}
    for md in all_mds:
        rel = str(md.relative_to(wiki).with_suffix("")).replace("\\", "/")
        text = _read_md(md)
        outgoing[rel] = set(_extract_wikilinks(text))

    # Build incoming link set (which pages are linked to)
    incoming: set[str] = set()
    for links in outgoing.values():
        for lnk in links:
            incoming.add(lnk.strip().strip("/"))
        # Also add stems
        for lnk in links:
            incoming.add(Path(lnk.strip()).stem)

    orphans: list[str] = []
    for rel, links in outgoing.items():
        stem = Path(rel).stem
        has_incoming = rel in incoming or stem in incoming
        has_outgoing = bool(links)
        if not has_incoming and not has_outgoing:
            orphans.append(rel)

    return sorted(orphans)


def find_missing_entries(
    raw: Path, wiki: Path, *, kb_dir: Path | None = None,
) -> list[str]:
    """Find files in raw/ that have no corresponding wiki entries.

    When ``kb_dir`` is provided, each raw file is first resolved through
    the hash registry (``kb_dir/.openkb/hashes.json``): registered files
    are checked against their ``doc_name`` artifacts, which can differ
    from the raw stem (watch-mode/URL ingests keep the original filename
    in raw/ — e.g. ``2509.11420.pdf`` → ``2509-11420.md`` — and collision
    suffixes rename artifacts too). Files with no registry entry fall back
    to the stem heuristic: "present" means a sources/ or summaries/ page
    with the same stem.

    Args:
        raw: Path to the raw documents directory.
        wiki: Path to the wiki root directory.
        kb_dir: Root of the knowledge base. When None, only the legacy
            stem heuristic is used.

    Returns:
        List of filenames in raw/ with no wiki entry.
    """
    sources_dir = wiki / "sources"
    summaries_dir = wiki / "summaries"

    sources_stems = {p.stem for p in sources_dir.glob("*.md")} if sources_dir.exists() else set()
    summary_stems = {p.stem for p in summaries_dir.glob("*.md")} if summaries_dir.exists() else set()
    known_stems = sources_stems | summary_stems

    registry = None
    if kb_dir is not None:
        registry_file = kb_dir / ".openkb" / "hashes.json"
        if registry_file.exists():
            # Deferred import: converter pulls in pymupdf/markitdown,
            # which lint doesn't otherwise need at import time.
            from openkb.converter import _registry_path
            from openkb.state import HashRegistry

            registry = HashRegistry(registry_file)

    missing: list[str] = []
    if raw.exists():
        for f in raw.iterdir():
            if not f.is_file():
                continue
            if registry is not None:
                meta = registry.get_by_path(_registry_path(f, kb_dir))
                if meta is not None:
                    # Registered file — the registry's doc_name is the
                    # single source of truth for artifact names.
                    doc_name = meta.get("doc_name") or Path(
                        meta.get("name", f.name)
                    ).stem
                    present = (
                        (sources_dir / f"{doc_name}.md").exists()
                        or (sources_dir / f"{doc_name}.json").exists()
                        or (summaries_dir / f"{doc_name}.md").exists()
                    )
                    if not present:
                        missing.append(f.name)
                    continue
            if f.stem not in known_stems:
                missing.append(f.name)

    return sorted(missing)


def check_index_sync(wiki: Path) -> list[str]:
    """Compare index.md wikilinks against actual files on disk.

    Returns issues for:
    - Links in index.md pointing to non-existent pages
    - Pages in summaries/, concepts/, or entities/ not mentioned in index.md

    Args:
        wiki: Path to the wiki root directory.

    Returns:
        List of sync issue strings.
    """
    index_path = wiki / "index.md"
    issues: list[str] = []

    if not index_path.exists():
        return ["index.md does not exist"]

    index_text = _read_md(index_path)
    index_links = set(_extract_wikilinks(index_text))
    pages = _all_wiki_pages(wiki)

    # Check that all index links resolve
    for lnk in index_links:
        lnk_norm = lnk.strip().strip("/")
        if lnk_norm not in pages:
            issues.append(f"index.md links to missing page: [[{lnk}]]")

    # Check that summaries, concepts, and entities pages are mentioned in index
    index_stems = {Path(lnk.strip()).stem for lnk in index_links}
    index_text_lower = index_text.lower()

    for subdir in PAGE_CONTENT_DIRS:
        subdir_path = wiki / subdir
        if not subdir_path.exists():
            continue
        for md in sorted(subdir_path.glob("*.md")):
            stem = md.stem
            if stem not in index_stems and stem.lower() not in index_text_lower:
                issues.append(f"{subdir}/{stem}.md not mentioned in index.md")

    return sorted(issues)


def _load_wiki_pages(wiki: Path) -> dict[Path, str]:
    """Read all wiki .md files once and return a ``{path: text}`` mapping.

    Applies the same scope as :func:`find_invalid_frontmatter`: walks the
    whole wiki, skips names in :data:`_EXCLUDED_FILES`, and skips any path
    whose first relative-to-wiki directory part is ``"reports"`` or
    ``"sources"``.  The result is the superset that both frontmatter checks
    iterate; :func:`find_missing_okf_fields` additionally filters down to
    :data:`PAGE_CONTENT_DIRS <openkb.schema.PAGE_CONTENT_DIRS>` paths.
    """
    pages: dict[Path, str] = {}
    for path in wiki.rglob("*.md"):
        if path.name in _EXCLUDED_FILES:
            continue
        rel_parts = path.relative_to(wiki).parts
        if rel_parts and rel_parts[0] in ("reports", "sources"):
            continue
        pages[path] = _read_md(path)
    return pages


def find_invalid_frontmatter(
    wiki: Path,
    pages: dict[Path, str] | None = None,
) -> list[str]:
    """Return wiki pages whose YAML frontmatter fails ``yaml.safe_load``.

    Catches the silent-write class of bug where an LLM-authored field
    (e.g. ``brief:``) ships unquoted and turns a colon-bearing value
    into invalid YAML that OpenKB itself reads with string slicing but
    external YAML-aware tools (VS Code, Obsidian, doc generators) reject.

    Args:
        wiki: Path to the wiki root directory.
        pages: Optional pre-loaded ``{path: text}`` mapping from
            :func:`_load_wiki_pages`.  When ``None`` (the default), the
            mapping is built internally so existing callers that pass only
            ``wiki`` keep working unchanged.
    """
    issues: list[str] = []
    if not wiki.exists():
        return issues
    if pages is None:
        pages = _load_wiki_pages(wiki)
    for path, text in sorted(pages.items()):
        parts = frontmatter.split(text)
        if parts is None:
            continue
        fm_block = parts[0]
        # Extract the raw YAML between the two ``---`` delimiters so that
        # yaml.safe_load can surface the exact error message.  The closing
        # delimiter is line-anchored (frontmatter.split guarantees this),
        # so we strip the opening ``---\n`` and everything from the final
        # ``\n---`` onward.
        inner = fm_block[4:]          # drop "---\n"
        close = inner.rfind("\n---")
        if close != -1:
            inner = inner[:close]
        try:
            yaml.safe_load(inner)
        except yaml.YAMLError as exc:
            rel = path.relative_to(wiki)
            msg = str(exc).splitlines()[0]
            issues.append(f"{rel}: {msg}")
    return issues


def find_missing_okf_fields(
    wiki: Path,
    pages: dict[Path, str] | None = None,
) -> list[str]:
    """Return knowledge pages missing a non-empty ``type`` or ``description``.

    OKF v0.1 requires every non-reserved knowledge page to carry a non-empty
    ``type``; ``description`` is OpenKB's required one-liner. Only summaries/,
    concepts/, entities/ are checked — index.md, log.md and sources/ are exempt.

    Args:
        wiki: Path to the wiki root directory.
        pages: Optional pre-loaded ``{path: text}`` mapping from
            :func:`_load_wiki_pages`.  When ``None`` (the default), the
            mapping is built internally so existing callers that pass only
            ``wiki`` keep working unchanged.
    """
    issues: list[str] = []
    if not wiki.exists():
        return issues
    if pages is None:
        pages = _load_wiki_pages(wiki)
    for path, text in sorted(pages.items()):
        # find_missing_okf_fields covers only PAGE_CONTENT_DIRS subsets.
        rel_parts = path.relative_to(wiki).parts
        if not rel_parts or rel_parts[0] not in PAGE_CONTENT_DIRS:
            continue
        fm = frontmatter.parse(text)
        rel = path.relative_to(wiki)
        type_val = fm.get("type")
        if not (isinstance(type_val, str) and type_val.strip()):
            issues.append(f"{rel}: missing non-empty 'type'")
        desc_val = fm.get("description")
        if not (isinstance(desc_val, str) and desc_val.strip()):
            issues.append(f"{rel}: missing non-empty 'description'")
    return issues


def run_structural_lint(kb_dir: Path) -> str:
    """Run all structural lint checks and return a formatted Markdown report.

    Args:
        kb_dir: Root of the knowledge base (contains wiki/ and raw/).

    Returns:
        Formatted Markdown string with lint results.
    """
    wiki = kb_dir / "wiki"
    raw = kb_dir / "raw"

    # Load all wiki pages once and share across both frontmatter checks
    # (avoids 2N disk reads when the wiki is large).
    wiki_pages = _load_wiki_pages(wiki) if wiki.exists() else {}

    broken = find_broken_links(wiki)
    orphans = find_orphans(wiki)
    missing = find_missing_entries(raw, wiki, kb_dir=kb_dir)
    sync_issues = check_index_sync(wiki)
    bad_frontmatter = find_invalid_frontmatter(wiki, pages=wiki_pages)
    missing_okf = find_missing_okf_fields(wiki, pages=wiki_pages)

    lines = ["## Structural Lint Report\n"]

    # Broken links
    lines.append(f"### Broken Links ({len(broken)})")
    if broken:
        for issue in broken:
            lines.append(f"- {issue}")
    else:
        lines.append("No broken links found.")
    lines.append("")

    # Orphans
    lines.append(f"### Orphaned Pages ({len(orphans)})")
    if orphans:
        for page in orphans:
            lines.append(f"- {page}")
    else:
        lines.append("No orphaned pages found.")
    lines.append("")

    # Missing entries
    lines.append(f"### Raw Files Without Wiki Entry ({len(missing)})")
    if missing:
        for name in missing:
            lines.append(f"- {name}")
    else:
        lines.append("All raw files have wiki entries.")
    lines.append("")

    # Index sync
    lines.append(f"### Index Sync Issues ({len(sync_issues)})")
    if sync_issues:
        for issue in sync_issues:
            lines.append(f"- {issue}")
    else:
        lines.append("Index is in sync.")
    lines.append("")

    # Invalid frontmatter
    lines.append(f"### Invalid Frontmatter ({len(bad_frontmatter)})")
    if bad_frontmatter:
        for issue in bad_frontmatter:
            lines.append(f"- {issue}")
    else:
        lines.append("All frontmatter parses as valid YAML.")
    lines.append("")

    # OKF conformance
    lines.append(f"### OKF Conformance ({len(missing_okf)})")
    if missing_okf:
        for issue in missing_okf:
            lines.append(f"- {issue}")
    else:
        lines.append("All knowledge pages carry required OKF fields.")

    return "\n".join(lines)
