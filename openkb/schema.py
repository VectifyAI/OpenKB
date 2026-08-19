from __future__ import annotations

from pathlib import Path

# The compiled page-type subdirectories under wiki/. Shared source of truth
# for surfaces that enumerate page content (list, lint, status, skill gate).
PAGE_CONTENT_DIRS = ("summaries", "concepts", "entities")

# Canonical empty index.md seed. Used by `openkb init` and the compiler's
# lazy-create path so they never drift.
INDEX_SEED = (
    "# Knowledge Base Index\n\n## Documents\n\n## Concepts\n\n## Entities\n\n## Explorations\n"
)

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
- `claims:` — optional one-line JSON array of time-based evidence claims.
  - Each claim must have a non-empty `text` field.
  - Each claim must have an ISO-8601 `as_of` field.
  - Each claim must have a non-empty `source_anchor` field.
  - The `status` field must have a value of `proposed`, `validated`,
    `superseded`, or `abandoned`.
  - OpenKB creates the `id` field.
  - The optional `authority` field can have a value of `first_party`,
    `assistant`, or `document`.
  - If an assistant claim has a `status` value of `validated`, OpenKB changes
    the value to `proposed`.
  - Assistant claims do not enter strict current reads.
  - An assistant claim cannot supersede a claim that has a `status` value of
    `validated`.
  - Only a claim with an `authority` value of `first_party` can supersede
    another claim with an `authority` value of `first_party`.
  - Claims with an `authority` value of `document` use the same `as_of` and
    `status` rules as claims without an `authority` field.
  - The `supersedes` field contains `id` values for old claims on the same page.
  - OpenKB applies supersession links only when the new `as_of` value is not
    earlier than the old `as_of` value.
  - OpenKB creates the `superseded_by` supersession links.
  - A claim with a `status` value of `proposed` cannot supersede a claim that has
    a `status` value of `validated`.
  - A claim with a `status` value of `validated` or `abandoned` can supersede a
    claim with a `status` value of `validated` when all other rules permit the
    supersession link.
  - Strict current reads return claims that have a `status` value of `validated`.
  - An adapter can define the format of the `source_anchor` field.
  - OpenKB does not require a specific transport or provider.
  - If a stored claim has the same `id` value as an incoming duplicate claim,
    OpenKB keeps the stored claim.
  - OpenKB ignores every field and every supersession link from the incoming
    duplicate claim.
  - The document compiler sets the `authority` field to `document` for every
    claim that a model creates.
  - An external adapter can verify source authority.
  - The adapter can then set the `authority` field to `first_party` or
    `assistant` through the public claims API.
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
