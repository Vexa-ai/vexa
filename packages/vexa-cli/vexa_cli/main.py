"""Vexa CLI — local terminal client for the Vexa agent runtime."""

import asyncio
import sys
from datetime import datetime
from typing import Optional

import click
from rich.console import Console

from vexa_cli import config as cfg
from vexa_cli.client import VexaClient
from vexa_cli.renderer import TerminalRenderer

console = Console()


def _get_client():
    """Load config, build client. Exit if misconfigured."""
    conf = cfg.load()
    if not conf.get("api_key"):
        console.print("[red]No API key configured.[/] Run [bold]vexa config[/] first.")
        sys.exit(1)
    if not conf.get("user_id"):
        console.print("[red]No user_id configured.[/] Run [bold]vexa config[/] first.")
        sys.exit(1)
    return VexaClient(conf["endpoint"], conf["api_key"]), conf


# ── CLI group ────────────────────────────────────────────────────────────────


def _parse_flags(flags_str):
    """Parse a --flags string into a list of CLI args."""
    if not flags_str:
        return None
    import shlex
    return shlex.split(flags_str)


@click.group(invoke_without_command=True)
@click.option("-p", "--prompt", "prompt_text", default=None, help="One-shot prompt (non-interactive)")
@click.option("--model", default=None, help="Model override")
@click.option("--session", "session_id", default=None, help="Resume specific session")
@click.option("--user", "user_id_override", default=None, help="Override user_id")
@click.option("--flags", "cli_flags_str", default=None,
              help='Extra flags forwarded to claude CLI (e.g. --flags "--effort high --permission-mode auto")')
@click.pass_context
def cli(ctx, prompt_text, model, session_id, user_id_override, cli_flags_str):
    """Vexa CLI — agent runtime from your terminal.

    \b
    Examples:
      vexa -p "hello"                                        # one-shot
      vexa -p "hello" --flags "--effort high"                # with claude flag
      vexa --flags "--permission-mode auto"                  # interactive + claude flag
      vexa -p "review" --flags "--allowedTools Read,Grep"
    """
    if ctx.invoked_subcommand is not None:
        return

    conf = cfg.load()

    api_key = conf.get("api_key", "")
    user_id = user_id_override or conf.get("user_id", "")
    endpoint = conf.get("endpoint", "http://localhost:8100")
    model = model or conf.get("default_model")

    if not api_key:
        console.print("[red]No API key.[/] Run [bold]vexa config[/] or set VEXA_API_KEY.")
        sys.exit(1)
    if not user_id:
        console.print("[red]No user_id.[/] Run [bold]vexa config[/] or set VEXA_USER_ID.")
        sys.exit(1)

    cli_flags = _parse_flags(cli_flags_str)
    client = VexaClient(endpoint, api_key)

    if prompt_text:
        asyncio.run(_one_shot(client, user_id, prompt_text, model, session_id, cli_flags))
    else:
        from vexa_cli.repl import run_repl
        asyncio.run(run_repl(client, user_id, model=model, session_id=session_id, cli_flags=cli_flags))


async def _one_shot(
    client: VexaClient,
    user_id: str,
    message: str,
    model: Optional[str] = None,
    session_id: Optional[str] = None,
    cli_flags: Optional[list] = None,
):
    """Run a single prompt, stream output, exit."""
    renderer = TerminalRenderer(console)
    try:
        async for event in client.chat_stream(
            user_id=user_id,
            message=message,
            session_id=session_id,
            model=model,
            cli_flags=cli_flags,
        ):
            renderer.render_event(event)
    except KeyboardInterrupt:
        console.print("\n[dim]Interrupted.[/]")
        sys.exit(130)
    except Exception as e:
        err = Console(stderr=True)
        err.print(f"[red]Error:[/] {e}", highlight=False)
        sys.exit(1)

    if renderer.cost_usd is None and renderer.session_id is None:
        sys.exit(1)


# ── Subcommands ──────────────────────────────────────────────────────────────


@cli.command("config")
def config_cmd():
    """Configure endpoint, API key, and user_id."""
    conf = cfg.load()

    endpoint = click.prompt("Endpoint", default=conf.get("endpoint", "http://localhost:8100"))
    api_key = click.prompt("API key", default=conf.get("api_key", ""))
    user_id = click.prompt("User ID", default=conf.get("user_id", ""))
    default_model = click.prompt(
        "Default model (blank for none)",
        default=conf.get("default_model") or "",
        show_default=False,
    )

    conf.update({
        "endpoint": endpoint,
        "api_key": api_key,
        "user_id": user_id,
        "default_model": default_model or None,
    })
    cfg.save(conf)
    console.print(f"[green]Config saved to {cfg.CONFIG_FILE}[/]")


@cli.command()
@click.option("--new", "new_name", default=None, help="Create a new named session")
@click.option("--user", "user_id_override", default=None)
def sessions(new_name, user_id_override):
    """List or create sessions."""
    client, conf = _get_client()
    user_id = user_id_override or conf["user_id"]

    async def _run():
        if new_name:
            result = await client.create_session(user_id, new_name)
            console.print(f"[green]Created:[/] {result['session_id']}  {result['name']}")
        else:
            items = await client.list_sessions(user_id)
            if not items:
                console.print("[dim]No sessions.[/]")
                return
            for s in items:
                sid = s.get("id", "?")
                name = s.get("name", "")
                updated = s.get("updated_at", "")
                if isinstance(updated, (int, float)):
                    updated = datetime.fromtimestamp(updated).strftime("%Y-%m-%d %H:%M")
                console.print(f"  {sid[:12]}  {name:30s}  {updated}")

    asyncio.run(_run())


@cli.group()
def workspace():
    """Workspace file operations."""
    pass


@workspace.command(name="ls")
@click.option("--user", "user_id_override", default=None)
def workspace_ls(user_id_override):
    """List workspace files."""
    client, conf = _get_client()
    user_id = user_id_override or conf["user_id"]

    async def _run():
        files = await client.workspace_files(user_id)
        if not files:
            console.print("[dim]Empty workspace.[/]")
            return
        for f in files:
            console.print(f"  {f}")

    asyncio.run(_run())


@workspace.command(name="cat")
@click.argument("path")
@click.option("--user", "user_id_override", default=None)
def workspace_cat(path, user_id_override):
    """Read a workspace file."""
    client, conf = _get_client()
    user_id = user_id_override or conf["user_id"]

    async def _run():
        content = await client.workspace_read(user_id, path)
        click.echo(content)

    asyncio.run(_run())


@workspace.command(name="write")
@click.argument("path")
@click.option("--user", "user_id_override", default=None)
def workspace_write(path, user_id_override):
    """Write stdin to a workspace file."""
    client, conf = _get_client()
    user_id = user_id_override or conf["user_id"]
    content = click.get_text_stream("stdin").read()

    async def _run():
        result = await client.workspace_write(user_id, path, content)
        console.print(f"[green]Written:[/] {result.get('path', path)}")

    asyncio.run(_run())


@cli.command()
@click.option("--user", "user_id_override", default=None)
def status(user_id_override):
    """Show connection and workspace status."""
    client, conf = _get_client()
    user_id = user_id_override or conf["user_id"]

    async def _run():
        st = await client.status(user_id)
        h = st.get("health", {})
        ws = st.get("workspace", {})
        console.print(f"  endpoint:  {client.endpoint}")
        console.print(f"  user_id:   {user_id}")
        console.print(f"  health:    {h.get('status', '?')} ({h.get('containers', '?')} containers)")
        console.print(f"  workspace: {'exists' if ws.get('workspace_in_storage') else 'empty'}")
        console.print(f"  container: {'running' if ws.get('container_running') else 'stopped'}")

    asyncio.run(_run())
