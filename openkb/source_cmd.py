"""``openkb source`` — bind a remote prefix and sync it into the knowledge base."""

from __future__ import annotations

from pathlib import Path

import click

from openkb.connectors.backend import ProviderError
from openkb.connectors.credentials import s3_credentials
from openkb.connectors.gdrive import prepare_gdrive_binding
from openkb.connectors.model import parse_s3_binding
from openkb.connectors.onedrive import prepare_onedrive_binding
from openkb.connectors.state import KbBusyError
from openkb.connectors.store import remove_binding, upsert_binding
from openkb.connectors.sync import binding_views, sync_bindings


def _kb_dir(ctx: click.Context) -> Path:
    from openkb.cli import _find_kb_dir

    root = ctx.find_root()
    override = None
    if isinstance(root.obj, dict):
        override = root.obj.get("kb_dir_override")
    found = _find_kb_dir(override)
    if found is None:
        raise click.ClickException("No knowledge base found. Run `openkb init` or pass --kb-dir.")
    return found


@click.group()
def source() -> None:
    """Bind a remote data source and sync it into this knowledge base."""


def _announce(url: str) -> None:
    """Print the sign-in URL and open a browser. A headless session still waits."""
    click.echo(f"Open this URL to sign in:\n{url}")
    import webbrowser

    try:
        webbrowser.open(url)
    except Exception:
        return


@source.command("add")
@click.argument("provider", type=click.Choice(["s3", "gdrive", "onedrive"]))
@click.option("--name", required=True, help="Local name for this binding.")
@click.option("--region", default="", help="AWS region, for example us-east-1.")
@click.option("--bucket", default="", help="S3 bucket name.")
@click.option("--prefix", default="", help="Key prefix. Empty means the bucket root.")
@click.option("--folder", default="", help="Google Drive folder URL or folder id.")
@click.option("--path", "drive_path", default="", help="OneDrive path from the drive root.")
@click.option(
    "--non-interactive",
    is_flag=True,
    default=False,
    help="Never open a browser. Drive and OneDrive then require a stored refresh token.",
)
@click.pass_context
def add_cmd(
    ctx, provider, name, region, bucket, prefix, folder, drive_path, non_interactive
) -> None:
    """Save a binding. Credentials must already be available."""
    kb_dir = _kb_dir(ctx)
    try:
        if provider == "s3":
            if s3_credentials(kb_dir) is None:
                raise click.ClickException(
                    "S3 credentials are missing. Set OPENKB_S3_ACCESS_KEY_ID and "
                    "OPENKB_S3_SECRET_ACCESS_KEY in the environment or <kb>/.env."
                )
            binding = parse_s3_binding(name=name, region=region, bucket=bucket, prefix=prefix)
            message = upsert_binding(kb_dir, binding)
        elif provider == "gdrive":
            message = prepare_gdrive_binding(
                kb_dir,
                name=name,
                folder=folder,
                non_interactive=non_interactive,
                announce=None if non_interactive else _announce,
            )
        else:
            message = prepare_onedrive_binding(
                kb_dir,
                name=name,
                path=drive_path,
                non_interactive=non_interactive,
                announce=None if non_interactive else _announce,
            )
    except (ValueError, KbBusyError, ProviderError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(message)


@source.command("list")
@click.pass_context
def list_cmd(ctx) -> None:
    """List bindings and the last sync status."""
    kb_dir = _kb_dir(ctx)
    views = binding_views(kb_dir)
    if not views:
        click.echo("No remote bindings.")
        return
    for view in views:
        status = "not synced since this change" if view["stale"] else "synced"
        click.echo(f"{view['name']}\t{view['provider']}\t{view['description']}\t{status}")


@source.command("remove")
@click.argument("name")
@click.pass_context
def remove_cmd(ctx, name) -> None:
    """Remove a binding. Compiled documents stay in the wiki."""
    kb_dir = _kb_dir(ctx)
    try:
        message = remove_binding(kb_dir, name)
    except KeyError as exc:
        raise click.ClickException(f"No binding named {name!r}.") from exc
    except KbBusyError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(message)


@source.command("sync")
@click.argument("name", required=False)
@click.option(
    "--non-interactive",
    is_flag=True,
    default=False,
    help="Never open a browser. A missing credential fails that provider.",
)
@click.pass_context
def sync_cmd(ctx, name, non_interactive) -> None:
    """Download new and changed objects and compile them."""
    kb_dir = _kb_dir(ctx)
    try:
        outcome = sync_bindings(
            kb_dir,
            name=name,
            non_interactive=non_interactive,
            announce=None if non_interactive else _announce,
        )
    except KbBusyError as exc:
        raise click.ClickException(str(exc)) from exc
    for report in outcome.reports:
        if report.error and not report.ok and report.added == 0 and report.failed == 0:
            click.echo(f"{report.name}: {report.error}", err=True)
            continue
        click.echo(
            f"{report.name}: added {report.added}, skipped {report.skipped}, "
            f"updated {report.updated}, failed {report.failed}"
        )
        if report.error:
            click.echo(f"  {report.error}", err=True)
    ctx.exit(outcome.exit_code)
