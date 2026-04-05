"""Interactive REPL — multi-turn conversation."""

import asyncio
import signal
from typing import Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console

from vexa_cli.client import VexaClient
from vexa_cli.renderer import TerminalRenderer


HELP_TEXT = """
Commands:
  /reset           Start a new session
  /rename <name>   Rename current session
  /sessions        List sessions
  /session <id>    Switch to session
  /files           List workspace files
  /cat <path>      Read workspace file
  /status          Show connection status
  /help            Show this help
  /exit            Quit (or Ctrl+D)
""".strip()

KNOWN_COMMANDS = {
    "/exit", "/help", "/reset", "/rename", "/sessions", "/session",
    "/files", "/cat", "/status",
}


async def run_repl(
    client: VexaClient,
    user_id: str,
    model: Optional[str] = None,
    session_id: Optional[str] = None,
    cli_flags: Optional[list] = None,
):
    """Main REPL loop."""
    console = Console()
    history_path = client.endpoint.replace("://", "_").replace("/", "_").replace(":", "_")
    history_file = "/tmp/.vexa_history_%s" % history_path
    prompt_session = PromptSession(history=FileHistory(history_file))

    current_session = session_id
    interrupted = False

    def _sigint_handler(sig, frame):
        nonlocal interrupted
        interrupted = True

    console.print("[bold]vexa[/] — interactive mode (type /help for commands, /exit to quit)")
    console.print()

    while True:
        interrupted = False
        try:
            with patch_stdout():
                text = await prompt_session.prompt_async("vexa> ")
        except EOFError:
            console.print("\n[dim]Goodbye.[/]")
            break
        except KeyboardInterrupt:
            console.print()
            continue

        text = text.strip()
        if not text:
            continue

        # Commands
        if text == "/exit":
            console.print("[dim]Goodbye.[/]")
            break
        if text == "/help":
            console.print(HELP_TEXT)
            continue
        if text == "/reset":
            await client.reset_session(user_id)
            current_session = None
            console.print("[dim]Session reset.[/]")
            continue
        if text.startswith("/rename"):
            parts = text.split(None, 1)
            if len(parts) < 2 or not parts[1].strip():
                console.print("[dim]Usage: /rename <name>[/]")
            elif not current_session:
                console.print("[dim]No active session to rename.[/]")
            else:
                name = parts[1].strip()
                try:
                    await client.rename_session(user_id, current_session, name)
                    console.print(f"[dim]Session renamed to: {name}[/]")
                except Exception as e:
                    console.print(f"[red]Error: {e}[/]")
            continue
        if text == "/sessions":
            sessions = await client.list_sessions(user_id)
            if not sessions:
                console.print("[dim]No sessions.[/]")
            else:
                for s in sessions:
                    sid = s.get("id", "?")
                    name = s.get("name", "")
                    marker = " [bold green]*[/]" if sid == current_session else ""
                    console.print(f"  {sid[:12]}  {name}{marker}")
            continue
        if text.startswith("/session "):
            parts = text.split(None, 1)
            current_session = parts[1].strip() if len(parts) > 1 else ""
            console.print(f"[dim]Switched to session {current_session[:12]}...[/]")
            continue
        if text == "/files":
            try:
                files = await client.workspace_files(user_id)
                for f in files:
                    console.print(f"  {f}")
                if not files:
                    console.print("[dim]Empty workspace.[/]")
            except Exception as e:
                console.print(f"[red]Error: {e}[/]")
            continue
        if text.startswith("/cat "):
            parts = text.split(None, 1)
            path = parts[1].strip() if len(parts) > 1 else ""
            try:
                content = await client.workspace_read(user_id, path)
                console.print(content)
            except Exception as e:
                console.print(f"[red]Error: {e}[/]")
            continue
        if text == "/status":
            try:
                st = await client.status(user_id)
                console.print(f"  endpoint: {client.endpoint}")
                console.print(f"  user_id:  {user_id}")
                h = st.get("health", {})
                console.print(f"  health:   {h.get('status', '?')} ({h.get('containers', '?')} containers)")
                ws = st.get("workspace", {})
                console.print(f"  workspace: {'exists' if ws.get('workspace_in_storage') else 'empty'}")
                console.print(f"  container: {'running' if ws.get('container_running') else 'stopped'}")
            except Exception as e:
                console.print(f"[red]Error: {e}[/]")
            continue

        # Chat turn — unknown /commands pass through to the agent
        renderer = TerminalRenderer(console)
        old_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, _sigint_handler)

        try:
            async for event in client.chat_stream(
                user_id=user_id,
                message=text,
                session_id=current_session,
                model=model,
                cli_flags=cli_flags,
            ):
                if interrupted:
                    await client.interrupt(user_id)
                    console.print("\n[dim]Interrupted.[/]")
                    break
                renderer.render_event(event)
        except Exception as e:
            console.print(f"\n[red]Error: {e}[/]")
        finally:
            signal.signal(signal.SIGINT, old_handler)

        # Track session for continuity
        if renderer.session_id:
            current_session = renderer.session_id

        console.print()
