"""OpenKB CLI — command-line interface for the knowledge base workflow."""
from __future__ import annotations

# Silence import-time warnings (e.g. pydub's missing-ffmpeg warning emitted
# when markitdown pulls it in). markitdown later clobbers the filters during
# its own import, so we re-apply after all imports below.
import warnings
warnings.filterwarnings("ignore")

import asyncio
import json
import logging
import shutil
import sys
import time
from pathlib import Path
from typing import Literal

import os

from agents import set_tracing_disabled
set_tracing_disabled(True)
# Use local model cost map — skip fetching from GitHub on every invocation
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import click

# Silence LiteLLM's "could not pre-load <aws-service> response stream
# shape" warnings — they fire at import time when ``botocore`` isn't
# installed, but botocore is only needed for AWS Bedrock / SageMaker
# users. Filter must be attached before ``import litellm`` runs.
class _SuppressLiteLLMPreloadWarnings(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "could not pre-load" not in record.getMessage()


logging.getLogger("LiteLLM").addFilter(_SuppressLiteLLMPreloadWarnings())

import litellm
litellm.suppress_debug_info = True
from dotenv import load_dotenv

from openkb.config import DEFAULT_CONFIG, load_config, save_config, load_global_config, register_kb
from openkb.converter import convert_document
from openkb.log import append_log
from openkb.schema import AGENTS_MD

# Suppress warnings after all imports — markitdown overrides filters at import time
import warnings
warnings.filterwarnings("ignore")

load_dotenv()  # load from cwd (covers running inside the KB dir)


def _setup_llm_key(kb_dir: Path | None = None) -> None:
    """Set LiteLLM API key from LLM_API_KEY env var if present.

    Load order (override=False, so first one wins):
    1. System environment variables (already set)
    2. KB-local .env  (kb_dir/.env)
    3. Global .env    (~/.config/openkb/.env)

    Also propagates to provider-specific env vars (OPENAI_API_KEY, etc.)
    so that the Agents SDK litellm provider can pick them up.
    """
    if kb_dir is not None:
        env_file = kb_dir / ".env"
        if env_file.exists():
            load_dotenv(env_file, override=False)

    from openkb.config import GLOBAL_CONFIG_DIR
    global_env = GLOBAL_CONFIG_DIR / ".env"
    if global_env.exists():
        load_dotenv(global_env, override=False)

    api_key = os.environ.get("LLM_API_KEY", "")
    if not api_key:
        # Check if any provider key is already set
        has_key = any(os.environ.get(k) for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"))
        if not has_key:
            click.echo(
                "Warning: No LLM API key found. Set one of:\n"
                f"  1. {kb_dir / '.env' if kb_dir else '<kb_dir>/.env'} — LLM_API_KEY=sk-...\n"
                f"  2. {GLOBAL_CONFIG_DIR / '.env'} — LLM_API_KEY=sk-...\n"
                "  3. Export LLM_API_KEY in your shell profile"
            )
    else:
        litellm.api_key = api_key
        for env_var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
            if not os.environ.get(env_var):
                os.environ[env_var] = api_key

# Supported document extensions for the `add` command
SUPPORTED_EXTENSIONS = {
    ".pdf", ".md", ".markdown", ".docx", ".pptx", ".xlsx",
    ".html", ".htm", ".txt", ".csv",
}

# Map raw doc types to display types
_TYPE_DISPLAY_MAP = {
    "long_pdf": "pageindex",
}

_SHORT_DOC_TYPES = {"pdf", "docx", "md", "markdown", "html", "htm", "txt", "csv", "pptx", "xlsx"}


def _display_type(raw_type: str) -> str:
    """Map a raw stored doc type to a display type string."""
    if raw_type in _TYPE_DISPLAY_MAP:
        return _TYPE_DISPLAY_MAP[raw_type]
    if raw_type in _SHORT_DOC_TYPES:
        return "short"
    return raw_type


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_kb_dir(override: Path | None = None) -> Path | None:
    """Find the KB root: explicit override → walk up from cwd → global default_kb."""
    # 0. Explicit override (--kb-dir or OPENKB_DIR)
    if override is not None:
        if (override / ".openkb").is_dir():
            return override
        return None
    # 1. Walk up from cwd
    current = Path.cwd().resolve()
    while True:
        if (current / ".openkb").is_dir():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    # 2. Fall back to global config default_kb
    gc = load_global_config()
    default = gc.get("default_kb")
    if default:
        p = Path(default)
        if (p / ".openkb").is_dir():
            return p
    return None


def add_single_file(file_path: Path, kb_dir: Path) -> Literal["added", "skipped", "failed"]:
    """Convert, index, and compile a single document into the knowledge base.

    Steps:
    1. Load config to get the model name.
    2. Convert the document (hash-check; skip if already known).
    3. If long doc: run PageIndex then compile_long_doc.
    4. Else: compile_short_doc.

    Returns:
        ``"added"`` on full success, ``"skipped"`` when the file's hash
        is already in the registry (dedup), or ``"failed"`` when any
        pipeline stage raised. URL-ingest distinguishes these so it can
        unlink the just-downloaded raw file on dedup (it would otherwise
        be an orphan) while preserving it on failure so the user can
        retry without re-downloading.
    """
    from openkb.agent.compiler import compile_long_doc, compile_short_doc
    from openkb.state import HashRegistry

    logger = logging.getLogger(__name__)
    openkb_dir = kb_dir / ".openkb"
    config = load_config(openkb_dir / "config.yaml")
    _setup_llm_key(kb_dir)
    model: str = config.get("model", DEFAULT_CONFIG["model"])
    registry = HashRegistry(openkb_dir / "hashes.json")

    # 2. Convert document
    click.echo(f"Adding: {file_path.name}")
    try:
        result = convert_document(file_path, kb_dir)
    except Exception as exc:
        click.echo(f"  [ERROR] Conversion failed: {exc}")
        logger.debug("Conversion traceback:", exc_info=True)
        return "failed"

    if result.skipped:
        click.echo(f"  [SKIP] Already in knowledge base: {file_path.name}")
        return "skipped"

    doc_name = file_path.stem
    index_result = None  # populated only on the long-doc branch

    # 3/4. Index and compile
    if result.is_long_doc:
        click.echo(f"  Long document detected — indexing with PageIndex...")
        try:
            from openkb.indexer import index_long_document
            index_result = index_long_document(result.raw_path, kb_dir)
        except Exception as exc:
            click.echo(f"  [ERROR] Indexing failed: {exc}")
            logger.debug("Indexing traceback:", exc_info=True)
            return "failed"

        summary_path = kb_dir / "wiki" / "summaries" / f"{doc_name}.md"
        click.echo(f"  Compiling long doc (doc_id={index_result.doc_id})...")
        for attempt in range(2):
            try:
                asyncio.run(
                    compile_long_doc(doc_name, summary_path, index_result.doc_id, kb_dir, model,
                                     doc_description=index_result.description)
                )
                break
            except Exception as exc:
                if attempt == 0:
                    click.echo(f"  Retrying compilation in 2s...")
                    time.sleep(2)
                else:
                    click.echo(f"  [ERROR] Compilation failed: {exc}")
                    logger.debug("Compilation traceback:", exc_info=True)
                    return "failed"
    else:
        click.echo(f"  Compiling short doc...")
        for attempt in range(2):
            try:
                asyncio.run(compile_short_doc(doc_name, result.source_path, kb_dir, model))
                break
            except Exception as exc:
                if attempt == 0:
                    click.echo(f"  Retrying compilation in 2s...")
                    time.sleep(2)
                else:
                    click.echo(f"  [ERROR] Compilation failed: {exc}")
                    logger.debug("Compilation traceback:", exc_info=True)
                    return "failed"

    # Register hash only after successful compilation
    if result.file_hash:
        doc_type = "long_pdf" if result.is_long_doc else file_path.suffix.lstrip(".")
        meta = {
            "name": file_path.name,
            "doc_name": doc_name,
            "type": doc_type,
        }
        # For long PDFs we also persist the PageIndex doc_id so `openkb
        # remove` can later call ``Collection.delete_document(doc_id)``
        # to free the managed PDF copy + SQLite row.
        if index_result is not None:
            meta["doc_id"] = index_result.doc_id
        registry.add(result.file_hash, meta)

    append_log(kb_dir / "wiki", "ingest", file_path.name)
    click.echo(f"  [OK] {file_path.name} added to knowledge base.")
    return "added"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.group()
@click.option("-v", "--verbose", is_flag=True, default=False, help="Enable verbose logging.")
@click.option("--kb-dir", "kb_dir_override", default=None, type=click.Path(exists=True, file_okay=False, resolve_path=True), help="Path to a KB root directory (overrides auto-detection).")
@click.pass_context
def cli(ctx, verbose, kb_dir_override):
    """OpenKB — Karpathy's LLM Knowledge Base workflow, powered by PageIndex."""
    logging.basicConfig(
        format="%(name)s %(levelname)s: %(message)s",
        level=logging.WARNING,
    )
    if verbose:
        logging.getLogger("openkb").setLevel(logging.DEBUG)
    ctx.ensure_object(dict)
    if kb_dir_override:
        ctx.obj["kb_dir_override"] = Path(kb_dir_override)
    else:
        env_kb = os.environ.get("OPENKB_DIR")
        if env_kb:
            ctx.obj["kb_dir_override"] = Path(env_kb).resolve()
        else:
            ctx.obj["kb_dir_override"] = None


@cli.command()
@click.argument("path", default=".")
def use(path):
    """Set PATH as the default knowledge base."""
    target = Path(path).resolve()
    if not (target / ".openkb").is_dir():
        click.echo(f"Not a knowledge base: {target}")
        return
    register_kb(target)
    click.echo(f"Default KB set to: {target}")


_LANGUAGE_MAX_LEN = 50


def _coerce_language(value: str | None) -> str | None:
    """Strip a language string; treat blanks as unset; reject unsafe values.

    The language string is interpolated into LLM system prompts (see
    ``_SYSTEM_TEMPLATE`` in ``openkb/agent/compiler.py`` and the query agent's
    instructions), so values with newlines or excessive length would let an
    external caller smuggle instructions into the prompt. Capping at
    ``_LANGUAGE_MAX_LEN`` and rejecting control characters is enough to close
    that vector while still allowing common forms ("en", "ko", "Korean",
    "Simplified Chinese").

    Returns the cleaned string, or ``None`` if the input was missing or blank
    after stripping. Raises ``click.BadParameter`` on unsafe input.
    """
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if len(value) > _LANGUAGE_MAX_LEN or any(c in value for c in "\n\r\t"):
        raise click.BadParameter(
            f"language must be {_LANGUAGE_MAX_LEN} characters or fewer "
            "with no control characters",
            param_hint="'--language'",
        )
    return value


def _language_option_callback(_ctx, _param, value):
    return _coerce_language(value)


def _stdin_is_tty() -> bool:
    """Return True when stdin is a real terminal.

    Used to skip optional ``openkb init`` prompts when input is piped or
    redirected, so existing automation (e.g. ``printf '\\n\\n' | openkb init``)
    keeps working as new prompts are added. Mirrors ``_stream_to_tty`` from #45.
    """
    return sys.stdin.isatty()


@cli.command()
@click.option(
    "--language", "-l", "language",
    default=None, metavar="LANG",
    callback=_language_option_callback,
    help="Wiki output language (e.g. 'en', 'ko'). Skips the interactive prompt when set.",
)
def init(language):
    """Initialise a new knowledge base in the current directory."""
    openkb_dir = Path(".openkb")
    if openkb_dir.exists():
        click.echo("Knowledge base already initialized.")
        return

    # Interactive prompts
    click.echo("Pick an LLM in `provider/model` LiteLLM format:")
    click.echo("  OpenAI:    gpt-5.4-mini, gpt-5.4")
    click.echo("  Anthropic: anthropic/claude-sonnet-4-6, anthropic/claude-opus-4-6")
    click.echo("  Gemini:    gemini/gemini-3.1-pro-preview, gemini/gemini-3-flash-preview")
    click.echo("  Others:    see https://docs.litellm.ai/docs/providers")
    click.echo()
    model = click.prompt(
        f"Model (enter for default {DEFAULT_CONFIG['model']})",
        default=DEFAULT_CONFIG["model"],
        show_default=False,
    )
    api_key = click.prompt(
        "LLM API Key (saved to .env, enter to skip)",
        default="",
        hide_input=True,
        show_default=False,
    ).strip()
    if language is None and _stdin_is_tty():
        language = _coerce_language(click.prompt(
            f"Wiki language (enter for default {DEFAULT_CONFIG['language']})",
            default=DEFAULT_CONFIG["language"],
            show_default=False,
        ))
    if not language:
        language = DEFAULT_CONFIG["language"]
    # Create directory structure
    Path("raw").mkdir(exist_ok=True)
    Path("wiki/sources/images").mkdir(parents=True, exist_ok=True)
    Path("wiki/summaries").mkdir(parents=True, exist_ok=True)
    Path("wiki/concepts").mkdir(parents=True, exist_ok=True)

    # Write wiki files
    Path("wiki/AGENTS.md").write_text(AGENTS_MD, encoding="utf-8")
    Path("wiki/index.md").write_text(
        "# Knowledge Base Index\n\n## Documents\n\n## Concepts\n\n## Explorations\n",
        encoding="utf-8",
    )
    Path("wiki/log.md").write_text("# Operations Log\n\n", encoding="utf-8")

    # Create .openkb/ state directory
    openkb_dir.mkdir()
    config = {
        "model": model,
        "language": language,
        "pageindex_threshold": DEFAULT_CONFIG["pageindex_threshold"],
    }
    save_config(openkb_dir / "config.yaml", config)
    (openkb_dir / "hashes.json").write_text(json.dumps({}), encoding="utf-8")

    # Write API key to KB-local .env (0600) if the user provided one
    if api_key:
        env_path = Path(".env")
        if env_path.exists():
            click.echo(".env already exists, skipping write. Add LLM_API_KEY manually if needed.")
        else:
            env_path.write_text(f"LLM_API_KEY={api_key}\n", encoding="utf-8")
            os.chmod(env_path, 0o600)
            click.echo("Saved LLM API key to .env.")

    # Register this KB in the global config
    register_kb(Path.cwd())

    click.echo("Knowledge base initialized.")


@cli.command()
@click.argument("path")
@click.pass_context
def add(ctx, path):
    """Add a document or directory of documents at PATH to the knowledge base.

    PATH may be a local file, a local directory (which is walked
    recursively for supported extensions), or an http(s) URL. URLs are
    fetched into ``raw/`` first: PDF responses (by Content-Type and
    magic-byte sniff) are saved as ``.pdf``; HTML responses are run
    through trafilatura's main-content extractor and saved as ``.md``.
    """
    kb_dir = _find_kb_dir(ctx.obj.get("kb_dir_override"))
    if kb_dir is None:
        click.echo("No knowledge base found. Run `openkb init` first.")
        return

    # URL ingest: download into raw/ first, then call add_single_file
    # explicitly so we can clean up the just-downloaded file if it
    # turns out to be a duplicate (registry already has its hash).
    # Without this, re-adding the same URL leaves an orphan in raw/
    # that the registry can't reach via openkb remove.
    from openkb.url_ingest import looks_like_url, fetch_url_to_raw
    if looks_like_url(path):
        fetched = fetch_url_to_raw(path, kb_dir)
        if fetched is None:
            return
        outcome = add_single_file(fetched, kb_dir)
        # Only clean up on dedup-skip. On "failed" we keep the file so
        # the user can retry (e.g. transient LLM error during compile)
        # without re-downloading — and so they don't lose data when
        # indexing has already succeeded but compilation didn't.
        if outcome == "skipped":
            fetched.unlink(missing_ok=True)
        return

    target = Path(path)
    if not target.exists():
        click.echo(f"Path does not exist: {path}")
        return

    if target.is_dir():
        files = [
            f for f in sorted(target.rglob("*"))
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
        ]
        if not files:
            click.echo(f"No supported files found in {path}.")
            return
        total = len(files)
        click.echo(f"Found {total} supported file(s) in {path}.")
        for i, f in enumerate(files, 1):
            click.echo(f"\n[{i}/{total}] ", nl=False)
            add_single_file(f, kb_dir)
    else:
        if target.suffix.lower() not in SUPPORTED_EXTENSIONS:
            click.echo(
                f"Unsupported file type: {target.suffix}. "
                f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
            )
            return
        add_single_file(target, kb_dir)


def _stream_to_tty() -> bool:
    """Return True when stdout is a real terminal.

    Used to auto-disable streaming output when ``openkb query`` is piped,
    redirected to a file, or run as a subprocess — streaming output emits
    interleaved tool-call lines that are noisy for non-interactive callers,
    and the non-streaming branch returns just the final answer string.
    """
    return sys.stdout.isatty()


@cli.command()
@click.argument("question")
@click.option("--save", is_flag=True, default=False, help="Save the answer to wiki/explorations/.")
@click.option(
    "--raw", "raw",
    is_flag=True, default=False,
    help="Show raw markdown source instead of rendered output (keeps tool-call colors).",
)
@click.pass_context
def query(ctx, question, save, raw):
    """Query the knowledge base with QUESTION."""
    kb_dir = _find_kb_dir(ctx.obj.get("kb_dir_override"))
    if kb_dir is None:
        click.echo("No knowledge base found. Run `openkb init` first.")
        return

    from openkb.agent.query import run_query

    openkb_dir = kb_dir / ".openkb"
    config = load_config(openkb_dir / "config.yaml")
    _setup_llm_key(kb_dir)
    model: str = config.get("model", DEFAULT_CONFIG["model"])

    stream = _stream_to_tty()
    try:
        answer = asyncio.run(run_query(question, kb_dir, model, stream=stream, raw=raw))
        if not stream and answer:
            click.echo(answer)
    except Exception as exc:
        click.echo(f"[ERROR] Query failed: {exc}")
        return

    append_log(kb_dir / "wiki", "query", question)

    if save and answer:
        import re
        from openkb.lint import list_existing_wiki_targets, strip_ghost_wikilinks
        slug = re.sub(r"[^a-z0-9]+", "-", question.lower()).strip("-")[:60]
        explore_dir = kb_dir / "wiki" / "explorations"
        explore_dir.mkdir(parents=True, exist_ok=True)
        explore_path = explore_dir / f"{slug}.md"
        # Strip ghost wikilinks the agent may have emitted to non-existent
        # concept/summary pages — the schema_md in the agent's instructions
        # encourages [[wikilinks]] but the agent's view of "which pages
        # exist" can drift from disk reality.
        known = list_existing_wiki_targets(kb_dir / "wiki")
        cleaned_answer, _ = strip_ghost_wikilinks(answer, known)
        explore_path.write_text(
            f"---\nquery: \"{question}\"\n---\n\n{cleaned_answer}\n",
            encoding="utf-8",
        )
        click.echo(f"\nSaved to {explore_path}")


def _cleanup_pageindex(
    openkb_dir: Path, kb_dir: Path, doc_name: str, doc_id: str | None,
) -> tuple[bool, str]:
    """Drop a long-doc entry from PageIndex's local SQLite + remove its
    managed files. Returns ``(did_cleanup, message)``.

    No-op (returns ``(False, "no PageIndex state")``) when no
    ``pageindex.db`` exists — short-doc-only KBs never created any.

    Falls back to matching by ``doc_name`` via ``list_documents()`` when
    the registry entry pre-dates PR #51's ``doc_id`` field. Ambiguous
    multi-match cases are skipped with a warning rather than guessed.
    """
    if not (openkb_dir / "pageindex.db").exists():
        return False, "no PageIndex state"

    from pageindex import PageIndexClient

    _setup_llm_key(kb_dir)
    config = load_config(openkb_dir / "config.yaml")
    model = config.get("model", DEFAULT_CONFIG.get("model", "gpt-4o-mini"))
    client = PageIndexClient(model=model, storage_path=str(openkb_dir))
    col = client.collection()

    if doc_id is None:
        candidates = [d for d in col.list_documents() if d.get("doc_name") == doc_name]
        if not candidates:
            return False, "no PageIndex doc to delete"
        if len(candidates) > 1:
            return False, (
                f"{len(candidates)} PageIndex docs match doc_name='{doc_name}'; "
                "skipping (re-add to refresh)"
            )
        doc_id = candidates[0]["doc_id"]

    col.delete_document(doc_id)
    return True, f"deleted PageIndex doc ({doc_id[:12]}…)"


def _resolve_doc_identifier(registry, identifier: str) -> list[tuple[str, dict]]:
    """Find registry entries matching ``identifier``.

    Match precedence (returns immediately on the first non-empty bucket):
      1. Exact match on ``metadata['name']`` (the original filename).
      2. Exact match on ``metadata['doc_name']`` (the slug).
      3. Case-insensitive substring match on either field.

    Returns ``[(file_hash, metadata), ...]``. Callers handle the empty,
    single, and multi-match cases.
    """
    entries = registry.all_entries()

    exact_name = [(h, m) for h, m in entries.items() if m.get("name") == identifier]
    if exact_name:
        return exact_name

    exact_slug = [(h, m) for h, m in entries.items() if m.get("doc_name") == identifier]
    if exact_slug:
        return exact_slug

    needle = identifier.lower()
    fuzzy = [
        (h, m) for h, m in entries.items()
        if needle in (m.get("name") or "").lower()
        or needle in (m.get("doc_name") or "").lower()
    ]
    return fuzzy


@cli.command()
@click.argument("identifier")
@click.option("--keep-raw", is_flag=True, default=False,
              help="Don't delete the original file from raw/.")
@click.option("--keep-empty-concepts", is_flag=True, default=False,
              help="Keep concept pages whose only source was the removed doc "
                   "(with empty sources frontmatter). Useful when replacing "
                   "the doc with a newer version.")
@click.option("--dry-run", is_flag=True, default=False,
              help="Print what would be done without modifying anything.")
@click.option("--yes", "-y", is_flag=True, default=False,
              help="Skip the confirmation prompt.")
@click.pass_context
def remove(ctx, identifier, keep_raw, keep_empty_concepts, dry_run, yes):
    """Remove a document from the knowledge base.

    IDENTIFIER may be the original filename ("paper.pdf"), the doc_name
    slug ("paper-a1b2c3d4e5f6"), or a substring that uniquely matches one.

    Deletes the doc's summary and source files, prunes the doc from
    concept-page frontmatter and Related Documents sections, drops the
    Documents entry from index.md, removes the hash entry, and finally
    runs `lint --fix` to clean any dangling wikilinks.

    Concept pages whose only source was this doc are deleted by default;
    use --keep-empty-concepts to retain them.
    """
    from openkb.agent.compiler import (
        remove_doc_from_concept_pages,
        remove_doc_from_index,
    )
    from openkb.lint import fix_broken_links
    from openkb.state import HashRegistry

    kb_dir = _find_kb_dir(ctx.obj.get("kb_dir_override"))
    if kb_dir is None:
        click.echo("No knowledge base found. Run `openkb init` first.")
        return

    openkb_dir = kb_dir / ".openkb"
    registry = HashRegistry(openkb_dir / "hashes.json")

    matches = _resolve_doc_identifier(registry, identifier)
    if not matches:
        click.echo(f"No document matching '{identifier}' found in the KB.")
        click.echo("Try `openkb list` to see indexed documents.")
        return
    if len(matches) > 1:
        click.echo(f"'{identifier}' matches multiple documents:")
        for _, m in matches:
            click.echo(f"  - {m.get('name', '?')}  (doc_name: {m.get('doc_name', '?')})")
        click.echo("Use a more specific name or the exact doc_name slug.")
        return

    file_hash, meta = matches[0]
    name = meta.get("name", "?")
    doc_name = meta.get("doc_name") or Path(name).stem
    doc_type = meta.get("type", "")
    wiki_dir = kb_dir / "wiki"

    # ----- Build the plan (no side effects) -----
    actions: list[tuple[str, str]] = []

    summary_path = wiki_dir / "summaries" / f"{doc_name}.md"
    if summary_path.exists():
        actions.append(("DELETE", str(summary_path.relative_to(kb_dir))))

    source_md = wiki_dir / "sources" / f"{doc_name}.md"
    source_json = wiki_dir / "sources" / f"{doc_name}.json"
    if source_md.exists():
        actions.append(("DELETE", str(source_md.relative_to(kb_dir))))
    if source_json.exists():
        actions.append(("DELETE", str(source_json.relative_to(kb_dir))))

    # Per-doc extracted-images directory (PDF page images + base64 images
    # from docx/pptx + copied relative refs from .md inputs). Created by
    # openkb.images during ingest, keyed by doc_name.
    images_dir = wiki_dir / "sources" / "images" / doc_name
    if images_dir.is_dir():
        actions.append((
            "DELETE",
            f"{images_dir.relative_to(kb_dir)}/  (images directory)",
        ))

    # Scan concept pages to predict which will be edited vs. deleted.
    # Only frontmatter ``sources:`` membership drives the plan — body-only
    # references (e.g. a stray ``See also:`` line a user added by hand
    # without updating sources) are cleaned by the executor but don't
    # affect the delete/edit classification, so the plan reflects what
    # the executor will actually do.
    source_file_marker = f"summaries/{doc_name}.md"
    affected_concepts: list[tuple[str, int]] = []  # (slug, remaining_sources)
    concepts_dir = wiki_dir / "concepts"
    if concepts_dir.is_dir():
        for path in sorted(concepts_dir.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            if not text.startswith("---"):
                continue
            fm_end = text.find("---", 3)
            if fm_end == -1:
                continue
            sources_count = 0
            source_in_frontmatter = False
            for line in text[:fm_end].split("\n"):
                if line.lstrip().startswith("sources:"):
                    lb = line.find("[")
                    rb = line.rfind("]")
                    if lb != -1 and rb != -1 and rb > lb:
                        items = [s.strip() for s in line[lb + 1:rb].split(",") if s.strip()]
                        sources_count = len(items)
                        source_in_frontmatter = source_file_marker in items
                    break
            if not source_in_frontmatter:
                continue
            remaining = max(sources_count - 1, 0)
            affected_concepts.append((path.stem, remaining))

    concept_deletes = [s for s, r in affected_concepts if r == 0 and not keep_empty_concepts]
    concept_edits = [s for s, r in affected_concepts if r > 0 or keep_empty_concepts]
    for slug in concept_deletes:
        actions.append(("DELETE", f"wiki/concepts/{slug}.md  (only source: this doc)"))
    for slug in concept_edits:
        actions.append(("MODIFY", f"wiki/concepts/{slug}.md  (drop this doc from sources)"))

    if (wiki_dir / "index.md").exists():
        actions.append(("MODIFY", "wiki/index.md  (remove Documents entry)"))

    actions.append(("REGISTRY", f"remove hash entry  ({file_hash[:12]}…)"))

    # Long PDFs leave state in PageIndex's local store (`.openkb/pageindex.db`
    # row + `.openkb/files/<collection>/<doc_id>.pdf` + extracted images).
    # Only flag this when both the registry says long_pdf and PageIndex
    # state exists on disk — short-doc-only KBs never created any.
    pageindex_doc_id = meta.get("doc_id")
    pageindex_state_exists = (openkb_dir / "pageindex.db").exists()
    cleanup_pageindex = doc_type == "long_pdf" and pageindex_state_exists
    if cleanup_pageindex:
        if pageindex_doc_id:
            actions.append((
                "PAGEINDEX", f"delete document ({pageindex_doc_id[:12]}…)",
            ))
        else:
            actions.append((
                "PAGEINDEX", f"delete document (lookup by doc_name; legacy entry)",
            ))

    raw_path = None
    if not keep_raw:
        raw_dir = kb_dir / "raw"
        candidate = raw_dir / name
        if candidate.exists():
            raw_path = candidate
            actions.append(("DELETE", str(candidate.relative_to(kb_dir))))

    # ----- Print the plan -----
    click.echo(f"Removing '{name}' (doc_name: {doc_name}, type: {doc_type or '?'}).")
    click.echo("")
    for tag, target in actions:
        click.echo(f"  {tag:<8} {target}")
    if concept_deletes:
        click.echo("")
        click.echo(
            f"  {len(concept_deletes)} concept(s) will be DELETED because this is their only source."
        )
        click.echo("  Pass --keep-empty-concepts to retain them instead.")
    click.echo("")

    if dry_run:
        click.echo("(dry-run — nothing modified)")
        return

    if not yes:
        if not click.confirm("Proceed?", default=False):
            click.echo("Aborted.")
            return

    # ----- Execute -----
    # Ordering rationale: every step before the registry write is
    # idempotent (``unlink(missing_ok=True)``, ``shutil.rmtree(
    # ignore_errors=True)``, concept/index helpers that no-op on
    # already-clean state, and PageIndex's own delete-by-doc_id which
    # uses ``missing_ok`` + ``if dir.exists()`` internally). The
    # registry write is therefore the *commit point*: if anything
    # before it raises (including PageIndex), the entry plus its
    # ``doc_id`` survive and the user can simply re-run ``openkb
    # remove`` to retry from a clean slate.
    summary_path.unlink(missing_ok=True)
    source_md.unlink(missing_ok=True)
    source_json.unlink(missing_ok=True)
    if images_dir.is_dir():
        shutil.rmtree(images_dir, ignore_errors=True)

    concept_result = remove_doc_from_concept_pages(
        wiki_dir, doc_name, keep_empty=keep_empty_concepts,
    )

    remove_doc_from_index(wiki_dir, doc_name, concept_result["deleted"])

    # Strip dangling wikilinks now so a retry (after a PageIndex
    # failure below) finds a clean wiki — no point in re-running this
    # on every attempt.
    files_changed, ghosts = fix_broken_links(wiki_dir)
    if files_changed:
        click.echo(f"  lint --fix cleaned {ghosts} dangling wikilink(s) in {files_changed} file(s)")

    # Free PageIndex's local managed state for long PDFs *before* the
    # registry write so the user can retry on failure — leaving the
    # entry intact preserves the ``doc_id`` we need for the second
    # attempt. PageIndex's local dedup is SHA-256 based, so a stale row
    # left behind here would silently re-bind on the next ``openkb
    # add`` and the user would get the old parse back without warning.
    if cleanup_pageindex:
        try:
            cleaned, msg = _cleanup_pageindex(
                openkb_dir, kb_dir, doc_name, pageindex_doc_id,
            )
            click.echo(f"  PageIndex: {msg}")
        except Exception as exc:
            click.echo(
                f"  [WARN] PageIndex cleanup failed: {exc} "
                f"— registry entry kept; re-run `openkb remove {name}` to retry"
            )
            logging.getLogger(__name__).debug(
                "PageIndex cleanup traceback:", exc_info=True,
            )
            return

    # ----- Commit point -----
    registry.remove_by_doc_name(doc_name)

    if raw_path is not None:
        raw_path.unlink(missing_ok=True)

    append_log(wiki_dir, "remove", name)
    click.echo(f"  [OK] {name} removed from knowledge base.")


@cli.command()
@click.option(
    "--resume", "-r", "resume",
    is_flag=False, flag_value="__latest__", default=None, metavar="[ID]",
    help="Resume the latest chat session, or a specific one by id or prefix.",
)
@click.option(
    "--list", "list_sessions_flag",
    is_flag=True, default=False,
    help="List chat sessions.",
)
@click.option(
    "--delete", "delete_id",
    default=None, metavar="ID",
    help="Delete a chat session by id or prefix.",
)
@click.option(
    "--no-color", "no_color",
    is_flag=True, default=False,
    help="Disable colored output.",
)
@click.option(
    "--raw", "raw",
    is_flag=True, default=False,
    help="Show raw markdown source instead of rendered output (keeps prompt and tool-call colors).",
)
@click.pass_context
def chat(ctx, resume, list_sessions_flag, delete_id, no_color, raw):
    """Start an interactive chat with the knowledge base."""
    kb_dir = _find_kb_dir(ctx.obj.get("kb_dir_override"))
    if kb_dir is None:
        click.echo("No knowledge base found. Run `openkb init` first.")
        return

    from openkb.agent.chat_session import (
        ChatSession,
        delete_session,
        list_sessions,
        load_session,
        relative_time,
        resolve_session_id,
    )

    if list_sessions_flag:
        sessions = list_sessions(kb_dir)
        if not sessions:
            click.echo("No chat sessions yet.")
            return
        click.echo(f"  {'ID':<22} {'TURNS':<6} {'UPDATED':<12} TITLE")
        click.echo(f"  {'-'*22} {'-'*6} {'-'*12} {'-'*30}")
        for s in sessions:
            rel = relative_time(s.get("updated_at", ""))
            title = s.get("title") or "(empty)"
            click.echo(
                f"  {s['id']:<22} {s['turn_count']:<6} {rel:<12} {title}"
            )
        click.echo(
            f"\n{len(sessions)} session(s) in {kb_dir / '.openkb' / 'chats'}"
        )
        return

    if delete_id is not None:
        try:
            resolved = resolve_session_id(kb_dir, delete_id)
        except ValueError as exc:
            click.echo(f"[ERROR] {exc}")
            return
        if not resolved:
            click.echo(f"No matching session: {delete_id}")
            return
        if delete_session(kb_dir, resolved):
            click.echo(f"Deleted session {resolved}")
        else:
            click.echo(f"Could not delete session: {resolved}")
        return

    openkb_dir = kb_dir / ".openkb"
    config = load_config(openkb_dir / "config.yaml")
    _setup_llm_key(kb_dir)

    if resume is not None:
        try:
            resolved = resolve_session_id(kb_dir, resume)
        except ValueError as exc:
            click.echo(f"[ERROR] {exc}")
            return
        if not resolved:
            if resume == "__latest__":
                click.echo("No previous chat sessions to resume.")
            else:
                click.echo(f"No matching session: {resume}")
            return
        session = load_session(kb_dir, resolved)
    else:
        model: str = config.get("model", DEFAULT_CONFIG["model"])
        language: str = config.get("language", "en")
        session = ChatSession.new(kb_dir, model, language)

    from openkb.agent.chat import run_chat

    try:
        asyncio.run(run_chat(kb_dir, session, no_color=no_color, raw=raw))
    except Exception as exc:
        click.echo(f"[ERROR] Chat failed: {exc}")


@cli.command()
@click.pass_context
def watch(ctx):
    """Watch the raw/ directory for new documents and process them automatically."""
    kb_dir = _find_kb_dir(ctx.obj.get("kb_dir_override"))
    if kb_dir is None:
        click.echo("No knowledge base found. Run `openkb init` first.")
        return

    from openkb.watcher import watch_directory

    raw_dir = kb_dir / "raw"
    raw_dir.mkdir(exist_ok=True)

    def on_new_files(paths):
        for p in paths:
            fp = Path(p)
            if fp.suffix.lower() not in SUPPORTED_EXTENSIONS:
                click.echo(
                    f"Skipping unsupported file type: {fp.suffix}. "
                    f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
                )
                continue
            add_single_file(fp, kb_dir)

    click.echo(f"Watching {raw_dir} for new documents. Press Ctrl+C to stop.")
    watch_directory(raw_dir, on_new_files)


async def run_lint(kb_dir: Path) -> Path | None:
    """Run structural + knowledge lint, write report, return report path.

    Returns ``None`` if the KB has no indexed documents (nothing to lint).
    Async because knowledge lint uses an LLM agent. Usable from CLI
    (via ``asyncio.run``) and directly from the chat REPL.
    """
    from openkb.lint import run_structural_lint
    from openkb.agent.linter import run_knowledge_lint

    openkb_dir = kb_dir / ".openkb"

    # Skip lint entirely when the KB has no indexed documents
    hashes_file = openkb_dir / "hashes.json"
    if hashes_file.exists():
        hashes = json.loads(hashes_file.read_text(encoding="utf-8"))
    else:
        hashes = {}
    if not hashes:
        click.echo("Nothing to lint — no documents indexed yet. Run `openkb add` first.")
        return

    config = load_config(openkb_dir / "config.yaml")
    _setup_llm_key(kb_dir)
    model: str = config.get("model", DEFAULT_CONFIG["model"])

    click.echo("Running structural lint...")
    structural_report = run_structural_lint(kb_dir)
    click.echo(structural_report)

    click.echo("Running knowledge lint...")
    try:
        knowledge_report = await run_knowledge_lint(kb_dir, model)
    except Exception as exc:
        knowledge_report = f"Knowledge lint failed: {exc}"
    click.echo(knowledge_report)

    # Write combined report
    reports_dir = kb_dir / "wiki" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    import datetime
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = reports_dir / f"lint_{timestamp}.md"
    report_content = f"# Lint Report — {timestamp}\n\n## Structural\n\n{structural_report}\n\n## Semantic\n\n{knowledge_report}\n"
    report_path.write_text(report_content, encoding="utf-8")
    append_log(kb_dir / "wiki", "lint", f"report → {report_path.name}")
    click.echo(f"\nReport written to {report_path}")
    return report_path


@cli.command()
@click.option("--fix", is_flag=True, default=False,
              help="Rewrite broken [[wikilinks]] in place (fuzzy match) or "
                   "strip to plain text when no match. Runs before the report.")
@click.pass_context
def lint(ctx, fix):
    """Lint the knowledge base for structural and semantic inconsistencies."""
    kb_dir = _find_kb_dir(ctx.obj.get("kb_dir_override"))
    if kb_dir is None:
        click.echo("No knowledge base found. Run `openkb init` first.")
        return
    if fix:
        from openkb.lint import fix_broken_links
        files_changed, ghosts = fix_broken_links(kb_dir / "wiki")
        if files_changed:
            click.echo(
                f"Fixed {ghosts} wikilink(s) across {files_changed} file(s)."
            )
        else:
            click.echo("Nothing to fix — all wikilinks resolve.")
    asyncio.run(run_lint(kb_dir))


def print_list(kb_dir: Path) -> None:
    """Print all documents in the knowledge base. Usable from CLI and chat REPL."""
    openkb_dir = kb_dir / ".openkb"
    hashes_file = openkb_dir / "hashes.json"
    if not hashes_file.exists():
        click.echo("No documents indexed yet.")
        return

    hashes = json.loads(hashes_file.read_text(encoding="utf-8"))
    if not hashes:
        click.echo("No documents indexed yet.")
        return

    # Display documents table with count in header
    doc_count = len(hashes)
    click.echo(f"Documents ({doc_count}):")
    click.echo(f"  {'Name':<40} {'Type':<12} {'Pages':<8}")
    click.echo(f"  {'-'*40} {'-'*12} {'-'*8}")
    for file_hash, meta in hashes.items():
        name = meta.get("name", "unknown")
        raw_type = meta.get("type", "unknown")
        display = _display_type(raw_type)
        pages = meta.get("pages", "")
        pages_str = str(pages) if pages else ""
        click.echo(f"  {name:<40} {display:<12} {pages_str:<8}")

    # Display summaries
    summaries_dir = kb_dir / "wiki" / "summaries"
    if summaries_dir.exists():
        summaries = sorted(p.stem for p in summaries_dir.glob("*.md"))
        if summaries:
            click.echo(f"\nSummaries ({len(summaries)}):")
            for s in summaries:
                click.echo(f"  - {s}")

    # Display concepts
    concepts_dir = kb_dir / "wiki" / "concepts"
    if concepts_dir.exists():
        concepts = sorted(p.stem for p in concepts_dir.glob("*.md"))
        if concepts:
            click.echo(f"\nConcepts ({len(concepts)}):")
            for c in concepts:
                click.echo(f"  - {c}")

    # Display reports
    reports_dir = kb_dir / "wiki" / "reports"
    if reports_dir.exists():
        reports = sorted(p.name for p in reports_dir.glob("*.md"))
        if reports:
            click.echo(f"\nReports ({len(reports)}):")
            for r in reports:
                click.echo(f"  - {r}")


@cli.command(name="list")
@click.pass_context
def list_cmd(ctx):
    """List all documents in the knowledge base."""
    kb_dir = _find_kb_dir(ctx.obj.get("kb_dir_override"))
    if kb_dir is None:
        click.echo("No knowledge base found. Run `openkb init` first.")
        return
    print_list(kb_dir)


def print_status(kb_dir: Path) -> None:
    """Print knowledge base status. Usable from CLI and chat REPL."""
    wiki_dir = kb_dir / "wiki"
    subdirs = ["sources", "summaries", "concepts", "reports"]

    # Print the active KB path as the first line. Agents and scripts
    # parse this to locate the wiki without assuming cwd == KB root.
    click.echo(f"Knowledge base: {kb_dir}")
    click.echo("")
    click.echo("Knowledge Base Status:")
    click.echo(f"  {'Directory':<20} {'Files':<10}")
    click.echo(f"  {'-'*20} {'-'*10}")

    for subdir in subdirs:
        path = wiki_dir / subdir
        if path.exists():
            count = len(list(path.glob("*.md")))
        else:
            count = 0
        click.echo(f"  {subdir:<20} {count:<10}")

    # Raw files
    raw_dir = kb_dir / "raw"
    if raw_dir.exists():
        raw_count = len([f for f in raw_dir.iterdir() if f.is_file()])
        click.echo(f"  {'raw':<20} {raw_count:<10}")

    # Hash registry summary
    openkb_dir = kb_dir / ".openkb"
    hashes_file = openkb_dir / "hashes.json"
    if hashes_file.exists():
        hashes = json.loads(hashes_file.read_text(encoding="utf-8"))
        click.echo(f"\n  Total indexed: {len(hashes)} document(s)")

    # Last compile time: newest file in wiki/summaries/
    summaries_dir = wiki_dir / "summaries"
    if summaries_dir.exists():
        summaries = list(summaries_dir.glob("*.md"))
        if summaries:
            newest_summary = max(summaries, key=lambda p: p.stat().st_mtime)
            import datetime
            mtime = datetime.datetime.fromtimestamp(newest_summary.stat().st_mtime)
            click.echo(f"  Last compile:  {mtime.strftime('%Y-%m-%d %H:%M:%S')}")

    # Last lint time: newest file in wiki/reports/
    reports_dir = wiki_dir / "reports"
    if reports_dir.exists():
        reports = list(reports_dir.glob("*.md"))
        if reports:
            newest_report = max(reports, key=lambda p: p.stat().st_mtime)
            import datetime
            mtime = datetime.datetime.fromtimestamp(newest_report.stat().st_mtime)
            click.echo(f"  Last lint:     {mtime.strftime('%Y-%m-%d %H:%M:%S')}")


@cli.command()
@click.pass_context
def status(ctx):
    """Show the current status of the knowledge base.

    Output starts with a ``Knowledge base: <path>`` line so agents and
    scripts can locate the wiki without assuming cwd == KB root.
    """
    kb_dir = _find_kb_dir(ctx.obj.get("kb_dir_override"))
    if kb_dir is None:
        click.echo("No knowledge base found. Run `openkb init` first.")
        return
    print_status(kb_dir)


# ---------------------------------------------------------------------------
# feedback
# ---------------------------------------------------------------------------

_FEEDBACK_REPO = "VectifyAI/OpenKB"
_FEEDBACK_TYPES = ("bug", "feature", "question", "other")
_FEEDBACK_LABEL_MAP = {
    "bug": "bug",
    "feature": "enhancement",
    "question": "question",
    "other": "",
}


def _openkb_version() -> str:
    """Return the installed openkb package version.

    Delegates to ``openkb.__version__`` so the chat REPL, feedback issue
    body, and any future caller all surface the same fallback string
    (``0.0.0+unknown`` from ``openkb/__init__.py``). Mirrors
    ``openkb.agent.chat._openkb_version``.
    """
    from openkb import __version__
    return __version__


def _collect_feedback_diagnostics(ctx) -> dict[str, str]:
    """Auto-collect non-sensitive environment info to attach to a feedback
    issue. Kept deliberately small — no paths, no API keys, no usernames.
    """
    import platform

    kb_dir = _find_kb_dir(ctx.obj.get("kb_dir_override") if ctx.obj else None)
    return {
        "openkb": _openkb_version(),
        "python": platform.python_version(),
        "platform": f"{platform.system()} {platform.release()}",
        "kb_initialised": "yes" if kb_dir else "no",
    }


def _build_feedback_url(
    message: str, feedback_type: str, diagnostics: dict[str, str],
) -> str:
    """Build a GitHub issue URL with title / body / labels prefilled."""
    from urllib.parse import urlencode

    first_line = message.splitlines()[0] if message else ""
    truncated = first_line[:60] + ("…" if len(first_line) > 60 else "")
    title_prefix = f"[{feedback_type}] " if feedback_type != "other" else ""
    title = f"{title_prefix}{truncated}" if truncated else f"{title_prefix}Feedback from CLI"

    if diagnostics:
        diag_block = "\n".join(f"- **{k}**: {v}" for k, v in diagnostics.items())
        body = (
            f"{message}\n\n"
            "---\n\n"
            "<details>\n"
            "<summary>Diagnostics (auto-collected by <code>openkb feedback</code>)</summary>\n\n"
            f"{diag_block}\n"
            "</details>\n"
        )
    else:
        body = message

    params = {"title": title, "body": body}
    label = _FEEDBACK_LABEL_MAP.get(feedback_type, "")
    if label:
        params["labels"] = label

    return f"https://github.com/{_FEEDBACK_REPO}/issues/new?{urlencode(params)}"


@cli.command()
@click.argument("message", required=False)
@click.option(
    "--type", "feedback_type",
    type=click.Choice(_FEEDBACK_TYPES),
    default=None,
    help="Feedback type — sets the GitHub issue label.",
)
@click.pass_context
def feedback(ctx, message, feedback_type):
    """Submit feedback by opening a prefilled GitHub issue.

    Examples:

      \b
      openkb feedback                              # interactive
      openkb feedback "openkb add hangs on .docx"  # one-line bug report
      openkb feedback --type feature "..."         # tags the issue 'enhancement'

    The command does not send anything to OpenKB maintainers directly —
    it opens GitHub in your browser with title, body, and label prefilled.
    You log in with your own GitHub account and submit the issue.
    """
    if not message:
        click.echo(
            "What's your feedback? End with an empty line + Ctrl-D "
            "(Unix) or Ctrl-Z+Enter (Windows). Ctrl-C cancels."
        )
        message = sys.stdin.read().strip()

    if not message:
        click.echo("No feedback provided. Aborted.")
        ctx.exit(1)
        return

    if feedback_type is None:
        # Skip the prompt in non-TTY contexts (CI / piped stdin) so
        # ``echo "msg" | openkb feedback`` doesn't hang on the second
        # prompt after consuming all piped input for the message body.
        # Mirrors the ``_stdin_is_tty()`` gate added in PR #48.
        if _stdin_is_tty():
            feedback_type = click.prompt(
                "Type",
                default="other",
                type=click.Choice(_FEEDBACK_TYPES),
                show_default=True,
                show_choices=True,
            )
        else:
            feedback_type = "other"

    diagnostics = _collect_feedback_diagnostics(ctx)
    url = _build_feedback_url(message, feedback_type, diagnostics)

    click.echo("Copy this URL into a browser if the auto-open below fails:")
    click.echo(f"  {url}")

    import webbrowser
    try:
        opened = webbrowser.open(url)
    except Exception as exc:
        # webbrowser.open rarely raises but be defensive — the printed URL
        # above is the fallback path.
        click.echo(f"  (browser auto-open failed: {exc})", err=True)
        return

    # ``webbrowser.open`` returns False on headless boxes (no GUI, no
    # ``BROWSER`` env) without raising. Without this check we'd silently
    # print "Opened" and the user would think the issue was filed.
    if opened:
        click.echo("Opened GitHub in your browser.")
    else:
        click.echo(
            "  (no browser available — copy the URL above to file the issue)",
            err=True,
        )
