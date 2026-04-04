import click


@click.group()
def cli():
    """OpenKB — Karpathy's LLM Knowledge Base workflow, powered by PageIndex."""


@cli.command()
def init():
    """Initialise a new knowledge base in the current directory."""
    click.echo("Not implemented yet.")


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
