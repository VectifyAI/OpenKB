# Changelog

All notable changes to OpenKB are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions
follow [PEP 440](https://peps.python.org/pep-0440/).

See [`RELEASING.md`](RELEASING.md) for the release process.

## [Unreleased]

### Added
- `openkb lint --fix` strips broken `[[wikilinks]]` in place — fuzzy
  matches (NFKC + case + `_`↔`-`) get rewritten to canonical form,
  unresolved targets become plain text. (#49)
- `openkb init --language` / `-l` flag and interactive prompt for wiki
  output language at initialization. (#48)
- Anthropic prompt caching via `cache_control` markers in the compile
  pipeline. Subsequent LLM calls reuse `(system + doc + summary +
  whitelist)` from cache instead of re-billing them per request. (#38)

### Fixed
- Ghost `[[wikilinks]]` no longer accumulate on every ingest. The
  compile pipeline now constrains LLM output to a whitelist of known
  targets and strips any unresolved links before writing to disk; the
  summary is rewritten with the full whitelist after concepts are
  finalized. (closes #47, #49)
- `openkb query` is safe for non-TTY stdout (pipes, redirects, MCP
  stdio transport). The streaming code path now falls back to plain
  text when stdout isn't a real console, and `--save` strips ghost
  wikilinks from saved answers. (closes #34, #45)
- Folder ingest no longer silently drops `index.md` updates when the
  file has been hand-edited away from the canonical layout. (closes #26)

## [0.1.3] - 2026-04-29

PyPI release. No git tag was created at the time. Highlights inferred
from commit history:

- Custom terminal Markdown renderer for `chat` and `query` streaming.
- `--raw` flag on `chat` and `query` to show raw Markdown instead of
  rendered output.
- Tab completion for slash commands and file paths in the chat REPL.
- `/save` slash command exports the chat transcript to
  `wiki/explorations/`.
- Numerous streaming-rendering and tab-completion fixes.

## [0.1.1] - 2026-04-10

PyPI release. No git tag was created at the time. Highlights inferred
from commit history:

- Switched build system from Hatchling to Poetry for PyPI packaging.
- `pageindex` dependency bumped to `0.3.0.dev1`; long-document indexing
  now uses the public PageIndex API.
- Brief system for summary/concept frontmatter and per-page JSON
  sources for long documents.
- Multimodal `get_image` tool in the query agent.
- Concept deduplication, compile pipeline refactor, and bidirectional
  backlinks between summary and concept pages.

## [0.1.0] - 2026-04-02

First public PyPI release. No git tag was created at the time. Core
pipeline:

- `openkb init` / `add` / `query` / `chat` / `watch` / `lint` / `list` /
  `status` commands.
- Markdown wiki layout (`raw/`, `wiki/sources/`, `wiki/summaries/`,
  `wiki/concepts/`, `wiki/explorations/`, `wiki/index.md`).
- LiteLLM-backed agent over OpenAI / Anthropic / Gemini / others.
- PageIndex for long-document tree-based retrieval.
- MarkItDown for multi-format ingestion (PDF, Word, PPT, Excel, HTML,
  Markdown, CSV, text).

[Unreleased]: https://github.com/VectifyAI/OpenKB/compare/v0.1.3...HEAD
[0.1.3]: https://pypi.org/project/openkb/0.1.3/
[0.1.1]: https://pypi.org/project/openkb/0.1.1/
[0.1.0]: https://pypi.org/project/openkb/0.1.0/
