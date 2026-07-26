# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

The map above (AGENTS.md) is the shared cross-tool reference. The rest of this
file adds Claude-Code-specific context on top of it.

## Running things

AGENTS.md covers install / test / lint. A few more:

- **Run a single test:** `pytest tests/test_compiler.py::test_name`, or filter with `pytest tests/test_compiler.py -k pattern`.
- **API server + Knowledge Workbench (Web UI):** install the extra (`pip install -e ".[dev,web]"`), then `openkb-web` (alias: `openkb-api`, or `python -m openkb.api`) serves the REST API and bundled UI at `http://127.0.0.1:7566/`. Auth is off by default; set `OPENKB_API_TOKEN` to require a bearer token.
- **Frontend dev:** `cd frontend && npm install && npm run dev` runs the Vite dev server, which proxies `/api` to a running `openkb-web`. `npm run build` regenerates the bundled `openkb/web/` that ships in the wheel.
- **File-size gate:** `tests/test_file_size.py` enforces the <800-line module limit. `cli.py`, `agent/compiler.py`, and `agent/chat.py` are grandfathered - don't add net-new lines to them; split a focused unit out instead.

## Architecture: the compile pipeline

OpenKB is two layers - a **wiki foundation** (compile + maintain) and **generators** (query / chat / skill / deck / visualize) that read the compiled wiki. Generators never recompile; they read the wiki `agent/tools.py` exposes.

A single `openkb add` flows through these stages across several modules:

1. **CLI** (`cli.py`) parses the file/dir/URL and drives content production, then wraps the result in an `AddMutationPlan` from `add_coordinator.py`.
2. **Convert** - `converter.py` (markitdown) turns the source into Markdown. Long PDFs (≥ `pageindex_threshold`, default 20 pages) instead go through `indexer.py` -> PageIndex, which builds a hierarchical tree index the LLM reads *instead of* the full text (this is the vectorless retrieval core).
3. **Compile** - `agent/compiler.py` is the LLM wiki compiler. It reads the converted text (or the PageIndex tree) and emits wiki pages: a summary page, concept pages (cross-document synthesis), entity pages (people/orgs/places/products), and updates the index + log. One `add` may touch 10-15 pages.
4. **Commit safely** - the produced pages are written through the mutation system below, never ad-hoc.

The compile pipeline has two branches by document length - short docs are read in full by the LLM; long PDFs are read as a PageIndex tree. Both end at the same compiled-wiki shape.

## Crash-safe wiki mutation

The hard invariant "wiki writes go through `locks.py` / `mutation.py`" exists because a single `add` touches many pages and a crash mid-write would corrupt the wiki. The mechanism spans three modules:

- `add_coordinator.py` builds an `AddMutationPlan` (touched paths + a body callable + post-commit hooks) and runs it under an exclusive ingest lock (`kb_ingest_lock_held`).
- `mutation.py` snapshots the paths it will touch (`MutationSnapshot`), applies writes via a journal, and commits atomically. On failure it rolls back from the snapshot.
- If rollback itself fails, `DirtyRollbackError` is raised and the journal is **retained** on disk. The next exclusive-lock acquisition drains retained journals (replay/rollback) to recover. So: do not commit further mutations on top of a dirty state - let it propagate and rerun the command.

When adding any new wiki write path, route it through this system (`mutation.py` / `locks.py`) rather than writing files directly.
