from __future__ import annotations

from pathlib import Path

# The compiled page-type subdirectories under wiki/. Shared source of truth
# for surfaces that enumerate page content (list, lint, status, skill gate).
PAGE_CONTENT_DIRS = ("summaries", "concepts", "entities")

# Canonical empty index.md seed. Used by `openkb init` and the compiler's
# lazy-create path so they never drift.
INDEX_SEED = "# Knowledge Base Index\n\n## Documents\n\n## Concepts\n\n## Entities\n\n## Explorations\n"

AGENTS_MD = """\
# Wiki Schema

## Directory Structure
- sources/ — Document content. Short docs as .md, long docs as .json (per-page). Do not modify directly.
- sources/images/ — Extracted images from documents, referenced by sources.
- summaries/ — One per source document. Summary of key content.
- concepts/ — Cross-document topic synthesis. Created when a theme spans multiple documents.
- entities/ — Specific named things: people, organizations, places, products, named works, events. One page per entity, accumulated across documents.
- explorations/ — Saved query results, analyses, and comparisons worth keeping.
- reports/ — Lint health check reports. Auto-generated.

## Special Files
- index.md — Content catalog: every page with link, one-line summary, organized by category.
- log.md — Chronological append-only record of operations (ingests, queries, lints).

## Page Types
- **Summary Page** (summaries/): Key content of a single source document.
- **Concept Page** (concepts/): Cross-document topic synthesis with [[wikilinks]].
- **Entity Page** (entities/): A specific named thing (proper noun) — e.g. a person, organization, place, product, named work, or event. Each page has a `type:` frontmatter field; the exact allowed type set is configurable (default: person, organization, place, product, work, event, other) and the authoritative set for this run is given in the compilation prompt. An entity differs from a concept: a concept is an abstract recurring idea; an entity is a specific named thing. Create an entity page only when the entity is central to a document or recurs across sources — do not page passing mentions.
- **Exploration Page** (explorations/): Saved query results — analyses, comparisons, syntheses.
- **Index Page** (index.md): One-liner summary of every page in the wiki. Auto-maintained.

## Index Page Format
index.md lists all documents, concepts, entities, and explorations with metadata:
- Documents: name, one-liner description, type (short|pageindex), detail access path
- Concepts: name, one-liner description
- Entities: name, type, one-liner description
- Explorations: name, one-liner description

## Log Format
Each log entry: `## [YYYY-MM-DD HH:MM:SS] operation | description`
Operations: ingest, query, lint

## Format
- Use [[wikilink]] to link other wiki pages (e.g., [[concepts/attention]])
- Standard Markdown heading hierarchy
- Keep each page focused on a single topic

## Frontmatter (managed by code — do NOT emit it in generated content)
- Every summary/concept/entity page carries a non-empty `type:` — `Summary`,
  `Concept`, or a capitalized entity subtype (e.g. `Organization`). This is the
  one field OKF requires; consumers use it for routing/filtering/presentation.
- `description:` — a single-sentence one-liner (the field formerly named `brief`).
- Do not include YAML frontmatter (---) in generated content; it is managed by code.
"""

# Backward compat alias
SCHEMA_MD = AGENTS_MD


def get_agents_md(wiki_dir: Path) -> str:
    """Return the AGENTS.md content, reading from disk if available.

    Args:
        wiki_dir: Path to the wiki directory (containing AGENTS.md).

    Returns:
        Content of wiki_dir/AGENTS.md if it exists, otherwise the hardcoded
        AGENTS_MD default.
    """
    agents_file = wiki_dir / "AGENTS.md"
    if agents_file.exists():
        return agents_file.read_text(encoding="utf-8")
    return AGENTS_MD
