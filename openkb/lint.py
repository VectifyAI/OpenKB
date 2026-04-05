"""Structural lint checks for the OpenKB wiki.

Checks for:
- Broken [[wikilinks]] — link targets that don't exist
- Orphaned pages — pages with no incoming or outgoing links
- Missing wiki entries — raw files without corresponding sources/summaries
- Index sync — index.md links vs actual files on disk
"""
from __future__ import annotations

import re
from pathlib import Path

# Matches [[wikilink]] or [[subdir/link]]
_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def _read_md(path: Path) -> str:
    """Read a Markdown file safely, returning empty string on error."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _all_wiki_pages(wiki: Path) -> dict[str, Path]:
    """Return a mapping of stem/relative-path → absolute Path for all .md files.

    Keys are normalised: 'concepts/attention', 'summaries/paper', 'index', etc.
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
    """Return all wikilink targets found in *text*."""
    return _WIKILINK_RE.findall(text)


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
    all_mds = [p for p in wiki.rglob("*.md") if p.name != "index.md"]
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


def find_missing_entries(raw: Path, wiki: Path) -> list[str]:
    """Find files in raw/ that have no corresponding wiki entries.

    A file is considered "present" if it has either a sources/ or summaries/
    page with the same stem.

    Args:
        raw: Path to the raw documents directory.
        wiki: Path to the wiki root directory.

    Returns:
        List of filenames in raw/ with no wiki entry.
    """
    sources_dir = wiki / "sources"
    summaries_dir = wiki / "summaries"

    sources_stems = {p.stem for p in sources_dir.glob("*.md")} if sources_dir.exists() else set()
    summary_stems = {p.stem for p in summaries_dir.glob("*.md")} if summaries_dir.exists() else set()
    known_stems = sources_stems | summary_stems

    missing: list[str] = []
    if raw.exists():
        for f in raw.iterdir():
            if f.is_file() and f.stem not in known_stems:
                missing.append(f.name)

    return sorted(missing)


def check_index_sync(wiki: Path) -> list[str]:
    """Compare index.md wikilinks against actual files on disk.

    Returns issues for:
    - Links in index.md pointing to non-existent pages
    - Pages in summaries/ or concepts/ not mentioned in index.md

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

    # Check that summaries and concepts pages are mentioned in index
    index_stems = {Path(lnk.strip()).stem for lnk in index_links}
    index_text_lower = index_text.lower()

    for subdir in ("summaries", "concepts"):
        subdir_path = wiki / subdir
        if not subdir_path.exists():
            continue
        for md in sorted(subdir_path.glob("*.md")):
            stem = md.stem
            if stem not in index_stems and stem.lower() not in index_text_lower:
                issues.append(f"{subdir}/{stem}.md not mentioned in index.md")

    return sorted(issues)


def run_structural_lint(kb_dir: Path) -> str:
    """Run all structural lint checks and return a formatted Markdown report.

    Args:
        kb_dir: Root of the knowledge base (contains wiki/ and raw/).

    Returns:
        Formatted Markdown string with lint results.
    """
    wiki = kb_dir / "wiki"
    raw = kb_dir / "raw"

    broken = find_broken_links(wiki)
    orphans = find_orphans(wiki)
    missing = find_missing_entries(raw, wiki)
    sync_issues = check_index_sync(wiki)

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

    return "\n".join(lines)
