from __future__ import annotations

from pathlib import Path

AGENTS_MD = """\
# Wiki Schema

## Directory Structure
- sources/ — Full-text converted from raw documents. Do not modify directly.
- sources/images/ — Extracted images from documents, referenced by sources.
- summaries/ — One per source document. Summary of key content.
- concepts/ — Cross-document topic synthesis. Created when a theme spans multiple documents.
- explorations/ — Saved query results, analyses, and comparisons worth keeping.
- reports/ — Lint health check reports. Auto-generated.

## Special Files
- index.md — Content catalog: every page with link, one-line summary, organized by category.
- log.md — Chronological append-only record of operations (ingests, queries, lints).

## Page Types
- **Summary Page** (summaries/): Key content of a single source document.
- **Concept Page** (concepts/): Cross-document topic synthesis with [[wikilinks]].
- **Exploration Page** (explorations/): Saved query results — analyses, comparisons, syntheses.
- **Index Page** (index.md): One-liner summary of every page in the wiki. Auto-maintained.

## Index Page Format
index.md lists all documents, concepts, and explorations with metadata:
- Documents: name, one-liner description, type (short|pageindex), detail access path
- Concepts: name, one-liner description
- Explorations: name, one-liner description

## Log Format
Each log entry: `## [YYYY-MM-DD] operation | description`
Operations: ingest, query, lint

## Format
- Use [[wikilink]] to link other wiki pages (e.g., [[concepts/attention]])
- Summary pages header: `sources: [paper.pdf]`
- Concept pages header: `sources: [paper1.pdf, paper2.pdf, ...]`
- Standard Markdown heading hierarchy
- Keep each page focused on a single topic
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
