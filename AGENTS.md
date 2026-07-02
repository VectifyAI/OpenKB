# AGENTS.md — OpenKB map for coding agents

OpenKB compiles raw documents into an interlinked wiki knowledge base using
LLMs (vectorless retrieval via PageIndex). This repo is developed **agent-first**:
humans steer, agents execute. Optimize changes for agent legibility.

## Read next
- `docs/golden-principles.md` — mechanical rules to follow (enforced where possible).
- `docs/internal/superpowers/{specs,plans}/` — design history & plans *(maintainer-local, not in git)*.
- `README.md` — user-facing overview and commands.

## Dev commands
- Install: `pip install -e ".[dev]"`  (or `uv sync --extra dev` — plain `uv sync` skips the dev tools)
- Run CLI: `openkb <command>`  (entry point: `openkb.cli:cli`)
- Test: `pytest`
- Lint/format/types: `ruff check .` · `ruff format .` · `mypy openkb`

## Module map (openkb/)
- `cli.py` — Click CLI entry point & command wiring *(large; see tech-debt)*.
- `config.py` — config loading/validation (LiteLLM passthrough, env).
- `converter.py` — document → markdown conversion (markitdown).
- `url_ingest.py` — fetch & ingest URLs (trafilatura).
- `images.py` — figure/image extraction & handling.
- `indexer.py` — PageIndex tree indexing for long docs.
- `mutation.py` — crash-safe, serial KB mutations.
- `locks.py` — atomic writes / file locking (`atomic_write_text`, portalocker).
- `state.py` — run/session state tracking.
- `frontmatter.py` — YAML frontmatter round-trip (OKF).
- `schema.py` — page/content schema constants & helpers.
- `lint.py` — structural wiki lint (broken links, orphans, index sync).
- `tree_renderer.py`, `visualize.py`, `watcher.py` — rendering / graph / file watch.
- `agent/compiler.py` — LLM wiki compiler *(large; see tech-debt)*.
- `agent/linter.py` — semantic (LLM) wiki lint (contradictions, gaps, staleness).
- `agent/chat.py`, `agent/chat_session.py` — chat over the wiki *(chat.py large)*.
- `agent/query.py` — one-off query generator.
- `agent/tools.py` — shared wiki read/write tool functions used by query/linter (and by chat indirectly via `query.build_chat_agent`).
- `agent/skills.py`, `agent/skill_runner.py`, `skill/` — Skill Factory.
- `deck/`, `templates/`, `prompts/` — deck output, templates, prompt assets.

## Hard invariants
- Deps are pinned **exactly** (supply-chain caution). Vet before bumping.
- Wiki writes go through `locks.py` / `mutation.py` (never ad-hoc).
- Modules stay < 800 lines (`tests/test_file_size.py`); grandfathered files are in tech-debt.
- Keep this file a short map — put depth in `docs/`.
