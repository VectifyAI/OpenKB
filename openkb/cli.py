import json
from pathlib import Path

import click

from openkb.config import DEFAULT_CONFIG, save_config
from openkb.schema import SCHEMA_MD


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

    # Create directory structure
    Path("raw").mkdir(exist_ok=True)
    Path("wiki/sources/images").mkdir(parents=True, exist_ok=True)
    Path("wiki/summaries").mkdir(parents=True, exist_ok=True)
    Path("wiki/concepts").mkdir(parents=True, exist_ok=True)
    Path("wiki/reports").mkdir(parents=True, exist_ok=True)

    # Write wiki files
    Path("wiki/SCHEMA.md").write_text(SCHEMA_MD, encoding="utf-8")
    Path("wiki/index.md").write_text(
        "# Knowledge Base Index\n\n## Documents\n\n## Concepts\n",
        encoding="utf-8",
    )

    # Create .okb/ state directory
    okb_dir.mkdir()
    save_config(okb_dir / "config.yaml", DEFAULT_CONFIG)
    (okb_dir / "hashes.json").write_text(json.dumps({}), encoding="utf-8")

    click.echo("Knowledge base initialised.")


@cli.command()
@click.argument("path")
def add(path):
    """Add a document at PATH to the knowledge base."""
    click.echo("Not implemented yet.")


@cli.command()
@click.argument("question")
def query(question):
    """Query the knowledge base with QUESTION."""
    click.echo("Not implemented yet.")


@cli.command()
def watch():
    """Watch the raw/ directory for new documents and process them automatically."""
    click.echo("Not implemented yet.")


@cli.command()
@click.option("--fix", is_flag=True, default=False, help="Automatically fix lint issues.")
def lint(fix):
    """Lint the knowledge base for inconsistencies."""
    click.echo("Not implemented yet.")


@cli.command(name="list")
def list_cmd():
    """List all documents in the knowledge base."""
    click.echo("Not implemented yet.")


@cli.command()
def status():
    """Show the current status of the knowledge base."""
    click.echo("Not implemented yet.")
