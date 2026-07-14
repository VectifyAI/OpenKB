"""One-time migrations for knowledge bases created by older OpenKB versions."""

from __future__ import annotations

import re
from pathlib import Path

from openkb.locks import atomic_write_text

# Inline-link opener followed by the old wiki-root-relative image prefix.
# Only the prefix is swapped (`](sources/images/` → `](images/`), so the
# path tail up to the closing paren never needs parsing, and image embeds
# (`![alt](...)`) and plain links are handled alike.
_OLD_IMAGE_PREFIX_RE = re.compile(r"\]\(sources/images/")


def migrate_source_image_links(wiki: Path, *, dry_run: bool = False) -> list[tuple[Path, int]]:
    """Rewrite wiki-root-relative image links in sources pages to note-relative.

    KBs ingested before the ``md_image_ref()`` change embedded images in
    ``wiki/sources/<doc>.md`` as ``![alt](sources/images/<doc>/<file>)``.
    Renderers resolve links relative to the containing file, so from a
    sources page those pointed at the non-existent
    ``sources/sources/images/...`` and rendered broken. This rewrites them
    to the ``images/<doc>/<file>`` form now emitted at ingest.

    Only ``.md`` files directly under ``wiki/sources/`` are touched — the
    per-page JSON of long documents intentionally keeps wiki-root-relative
    paths (internal metadata, resolved against the wiki root), and pages
    outside ``sources/`` never carried the old prefix. Idempotent: already
    note-relative links don't match the old prefix.

    Args:
        wiki: Path to the wiki root directory.
        dry_run: When True, report what would change without writing.

    Returns:
        List of ``(path, links_rewritten)`` for each file that changed
        (or would change, under ``dry_run``).
    """
    sources = wiki / "sources"
    changed: list[tuple[Path, int]] = []
    if not sources.is_dir():
        return changed

    for md in sorted(sources.glob("*.md")):
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        migrated, count = _OLD_IMAGE_PREFIX_RE.subn("](images/", text)
        if count == 0:
            continue
        if not dry_run:
            atomic_write_text(md, migrated)
        changed.append((md, count))
    return changed
