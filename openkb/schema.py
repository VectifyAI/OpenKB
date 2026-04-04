SCHEMA_MD = """\
# Wiki Schema

## Directory Structure
- sources/ — Full-text converted from raw documents. Do not modify directly.
- sources/images/ — Extracted images from documents, referenced by sources.
- summaries/ — One per source document. Summary of key content.
- concepts/ — Cross-document topic synthesis. Created when a theme spans multiple documents.
- reports/ — Lint health check reports. Auto-generated.

## Page Types
- **Summary Page** (summaries/): Key content of a single source document.
- **Concept Page** (concepts/): Cross-document topic synthesis with [[wikilinks]].
- **Index Page** (index.md): One-liner summary of every page in the wiki. Auto-maintained.

## Index Page Format
index.md lists all documents and concepts with metadata:
- Documents: name, one-liner description, type (short|pageindex), detail access path
- Concepts: name, one-liner description

## Format
- Use [[wikilink]] to link other wiki pages (e.g., [[concepts/attention]])
- Summary pages header: `sources: [paper.pdf]`
- Concept pages header: `sources: [paper1.pdf, paper2.pdf, ...]`
- Standard Markdown heading hierarchy
- Keep each page focused on a single topic
"""
