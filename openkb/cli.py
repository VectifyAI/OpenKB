"""OpenKB CLI — command-line interface for the knowledge base workflow."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

import os

from agents import set_tracing_disabled
set_tracing_disabled(True)
# Use local model cost map — skip fetching from GitHub on every invocation
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import click
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


def _add_single_file(file_path: Path, kb_dir: Path) -> None:
    """Convert, index, and compile a single document into the knowledge base.

    Steps:
    1. Load config to get the model name.
    2. Convert the document (hash-check; skip if already known).
    3. If long doc: run PageIndex then compile_long_doc.
    4. Else: compile_short_doc.
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
        return

    if result.skipped:
        click.echo(f"  [SKIP] Already in knowledge base: {file_path.name}")
        return

    doc_name = file_path.stem

    # 3/4. Index and compile
    if result.is_long_doc:
        click.echo(f"  Long document detected — indexing with PageIndex…")
        try:
            from openkb.indexer import index_long_document
            index_result = index_long_document(result.raw_path, kb_dir)
        except Exception as exc:
            click.echo(f"  [ERROR] Indexing failed: {exc}")
            logger.debug("Indexing traceback:", exc_info=True)
            return

        summary_path = kb_dir / "wiki" / "summaries" / f"{doc_name}.md"
        click.echo(f"  Compiling long doc (doc_id={index_result.doc_id})…")
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
                    return
    else:
        click.echo(f"  Compiling short doc…")
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
                    return

    # Register hash only after successful compilation
    if result.file_hash:
        doc_type = "long_pdf" if result.is_long_doc else file_path.suffix.lstrip(".")
        registry.add(result.file_hash, {"name": file_path.name, "type": doc_type})

    append_log(kb_dir / "wiki", "ingest", file_path.name)
    click.echo(f"  [OK] {file_path.name} added to knowledge base.")


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


@cli.command()
def init():
    """Initialise a new knowledge base in the current directory."""
    openkb_dir = Path(".openkb")
    if openkb_dir.exists():
        click.echo("Knowledge base already initialized.")
        return

    # Interactive prompts
    model = click.prompt(
        f"Model (e.g. gpt-5.4-mini, anthropic/claude-sonnet-4-6) [default: {DEFAULT_CONFIG['model']}]",
        default=DEFAULT_CONFIG["model"],
        show_default=False,
    )
    language = click.prompt(
        f"Language [default: {DEFAULT_CONFIG['language']}]",
        default=DEFAULT_CONFIG["language"],
        show_default=False,
    )
    pageindex_threshold = click.prompt(
        f"PageIndex threshold (pages) [default: {DEFAULT_CONFIG['pageindex_threshold']}]",
        default=DEFAULT_CONFIG["pageindex_threshold"],
        type=int,
        show_default=False,
    )
    # Create directory structure
    Path("raw").mkdir(exist_ok=True)
    Path("wiki/sources/images").mkdir(parents=True, exist_ok=True)
    Path("wiki/summaries").mkdir(parents=True, exist_ok=True)
    Path("wiki/concepts").mkdir(parents=True, exist_ok=True)
    Path("wiki/explorations").mkdir(parents=True, exist_ok=True)
    Path("wiki/reports").mkdir(parents=True, exist_ok=True)

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
        "pageindex_threshold": pageindex_threshold,
    }
    save_config(openkb_dir / "config.yaml", config)
    (openkb_dir / "hashes.json").write_text(json.dumps({}), encoding="utf-8")

    # Register this KB in the global config
    register_kb(Path.cwd())

    click.echo("Knowledge base initialized.")


@cli.command()
@click.argument("path")
@click.pass_context
def add(ctx, path):
    """Add a document or directory of documents at PATH to the knowledge base."""
    kb_dir = _find_kb_dir(ctx.obj.get("kb_dir_override"))
    if kb_dir is None:
        click.echo("No knowledge base found. Run `openkb init` first.")
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
            _add_single_file(f, kb_dir)
    else:
        if target.suffix.lower() not in SUPPORTED_EXTENSIONS:
            click.echo(
                f"Unsupported file type: {target.suffix}. "
                f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
            )
            return
        _add_single_file(target, kb_dir)


@cli.command()
@click.argument("question")
@click.option("--save", is_flag=True, default=False, help="Save the answer to wiki/explorations/.")
@click.pass_context
def query(ctx, question, save):
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

    try:
        answer = asyncio.run(run_query(question, kb_dir, model, stream=True))
    except Exception as exc:
        click.echo(f"[ERROR] Query failed: {exc}")
        return

    append_log(kb_dir / "wiki", "query", question)

    if save and answer:
        import re
        slug = re.sub(r"[^a-z0-9]+", "-", question.lower()).strip("-")[:60]
        explore_dir = kb_dir / "wiki" / "explorations"
        explore_dir.mkdir(parents=True, exist_ok=True)
        explore_path = explore_dir / f"{slug}.md"
        explore_path.write_text(
            f"---\nquery: \"{question}\"\n---\n\n{answer}\n", encoding="utf-8"
        )
        click.echo(f"\nSaved to {explore_path}")


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
            _add_single_file(fp, kb_dir)

    click.echo(f"Watching {raw_dir} for new documents. Press Ctrl+C to stop.")
    watch_directory(raw_dir, on_new_files)


@cli.command()
@click.option("--fix", is_flag=True, default=False, help="Automatically fix lint issues (not yet implemented).")
@click.pass_context
def lint(ctx, fix):
    """Lint the knowledge base for structural and semantic inconsistencies."""
    if fix:
        click.echo("Warning: --fix is not yet implemented. Running lint in report-only mode.")
    kb_dir = _find_kb_dir(ctx.obj.get("kb_dir_override"))
    if kb_dir is None:
        click.echo("No knowledge base found. Run `openkb init` first.")
        return

    from openkb.lint import run_structural_lint
    from openkb.agent.linter import run_knowledge_lint

    openkb_dir = kb_dir / ".openkb"
    config = load_config(openkb_dir / "config.yaml")
    _setup_llm_key(kb_dir)
    model: str = config.get("model", DEFAULT_CONFIG["model"])

    # Structural lint
    click.echo("Running structural lint…")
    structural_report = run_structural_lint(kb_dir)
    click.echo(structural_report)

    # Knowledge lint (semantic)
    click.echo("Running knowledge lint…")
    try:
        knowledge_report = asyncio.run(run_knowledge_lint(kb_dir, model))
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


@cli.command(name="list")
@click.pass_context
def list_cmd(ctx):
    """List all documents in the knowledge base."""
    kb_dir = _find_kb_dir(ctx.obj.get("kb_dir_override"))
    if kb_dir is None:
        click.echo("No knowledge base found. Run `openkb init` first.")
        return

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


@cli.command()
@click.pass_context
def status(ctx):
    """Show the current status of the knowledge base."""
    kb_dir = _find_kb_dir(ctx.obj.get("kb_dir_override"))
    if kb_dir is None:
        click.echo("No knowledge base found. Run `openkb init` first.")
        return

    wiki_dir = kb_dir / "wiki"
    subdirs = ["sources", "summaries", "concepts", "reports"]

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
