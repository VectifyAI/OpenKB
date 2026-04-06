"""OpenKB CLI — command-line interface for the knowledge base workflow."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import click
from dotenv import load_dotenv

from openkb.config import DEFAULT_CONFIG, load_config, save_config
from openkb.converter import convert_document
from openkb.log import append_log
from openkb.schema import AGENTS_MD

load_dotenv()

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

def _find_kb_dir() -> Path | None:
    """Return the knowledge-base root if .okb/ exists in cwd, else None."""
    candidate = Path(".okb")
    if candidate.exists() and candidate.is_dir():
        return Path(".")
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

    okb_dir = kb_dir / ".okb"
    config = load_config(okb_dir / "config.yaml")
    model: str = config.get("model", DEFAULT_CONFIG["model"])
    registry = HashRegistry(okb_dir / "hashes.json")

    # 2. Convert document
    click.echo(f"Adding: {file_path.name}")
    try:
        result = convert_document(file_path, kb_dir)
    except Exception as exc:
        click.echo(f"  [ERROR] Conversion failed: {exc}")
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
            return

        summary_path = kb_dir / "wiki" / "summaries" / f"{doc_name}.md"
        click.echo(f"  Compiling long doc (doc_id={index_result.doc_id})…")
        for attempt in range(2):
            try:
                asyncio.run(
                    compile_long_doc(doc_name, summary_path, index_result.doc_id, kb_dir, model)
                )
                break
            except Exception as exc:
                if attempt == 0:
                    click.echo(f"  Retrying compilation in 2s...")
                    time.sleep(2)
                else:
                    click.echo(f"  [ERROR] Compilation failed: {exc}")
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
def cli():
    """OpenKB — Karpathy's LLM Knowledge Base workflow, powered by PageIndex."""


@cli.command()
def init():
    """Initialise a new knowledge base in the current directory."""
    okb_dir = Path(".okb")
    if okb_dir.exists():
        click.echo("Knowledge base already initialized.")
        return

    # Interactive prompts
    model = click.prompt("Model", default=DEFAULT_CONFIG["model"])
    api_key_env = click.prompt("API key env var", default=DEFAULT_CONFIG["api_key_env"])
    language = click.prompt("Language", default=DEFAULT_CONFIG["language"])
    pageindex_threshold = click.prompt(
        "PageIndex threshold (pages)",
        default=DEFAULT_CONFIG["pageindex_threshold"],
        type=int,
    )
    pageindex_api_key_env = click.prompt(
        "PageIndex cloud API key env var (leave empty for local)",
        default=DEFAULT_CONFIG["pageindex_api_key_env"],
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

    # Create .okb/ state directory
    okb_dir.mkdir()
    config = {
        "model": model,
        "api_key_env": api_key_env,
        "language": language,
        "pageindex_threshold": pageindex_threshold,
        "pageindex_api_key_env": pageindex_api_key_env,
    }
    save_config(okb_dir / "config.yaml", config)
    (okb_dir / "hashes.json").write_text(json.dumps({}), encoding="utf-8")

    click.echo("Knowledge base initialised.")


@cli.command()
@click.argument("path")
def add(path):
    """Add a document or directory of documents at PATH to the knowledge base."""
    kb_dir = _find_kb_dir()
    if kb_dir is None:
        click.echo("No knowledge base found. Run `okb init` first.")
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
def query(question, save):
    """Query the knowledge base with QUESTION."""
    kb_dir = _find_kb_dir()
    if kb_dir is None:
        click.echo("No knowledge base found. Run `okb init` first.")
        return

    from openkb.agent.query import run_query

    okb_dir = kb_dir / ".okb"
    config = load_config(okb_dir / "config.yaml")
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
def watch():
    """Watch the raw/ directory for new documents and process them automatically."""
    kb_dir = _find_kb_dir()
    if kb_dir is None:
        click.echo("No knowledge base found. Run `okb init` first.")
        return

    from openkb.watcher import watch_directory

    raw_dir = kb_dir / "raw"
    raw_dir.mkdir(exist_ok=True)

    def on_new_files(paths):
        for p in paths:
            _add_single_file(Path(p), kb_dir)

    click.echo(f"Watching {raw_dir} for new documents. Press Ctrl+C to stop.")
    watch_directory(raw_dir, on_new_files)


@cli.command()
@click.option("--fix", is_flag=True, default=False, help="Automatically fix lint issues.")
def lint(fix):
    """Lint the knowledge base for structural and semantic inconsistencies."""
    kb_dir = _find_kb_dir()
    if kb_dir is None:
        click.echo("No knowledge base found. Run `okb init` first.")
        return

    from openkb.lint import run_structural_lint
    from openkb.agent.linter import run_knowledge_lint

    okb_dir = kb_dir / ".okb"
    config = load_config(okb_dir / "config.yaml")
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
def list_cmd():
    """List all documents in the knowledge base."""
    kb_dir = _find_kb_dir()
    if kb_dir is None:
        click.echo("No knowledge base found. Run `okb init` first.")
        return

    okb_dir = kb_dir / ".okb"
    hashes_file = okb_dir / "hashes.json"
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

    # Display concepts
    concepts_dir = kb_dir / "wiki" / "concepts"
    if concepts_dir.exists():
        concepts = sorted(p.stem for p in concepts_dir.glob("*.md"))
        if concepts:
            click.echo("\nConcepts:")
            for c in concepts:
                click.echo(f"  - {c}")


@cli.command()
def status():
    """Show the current status of the knowledge base."""
    kb_dir = _find_kb_dir()
    if kb_dir is None:
        click.echo("No knowledge base found. Run `okb init` first.")
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
    okb_dir = kb_dir / ".okb"
    hashes_file = okb_dir / "hashes.json"
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
