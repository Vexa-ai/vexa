"""SSE events -> terminal output using rich."""

import sys
import time
from typing import Optional

from rich.console import Console


class TerminalRenderer:
    """Renders SSE events to the terminal, mimicking claude CLI output."""

    def __init__(self, console: Optional[Console] = None):
        self.console = console or Console()
        self._text_buffer = ""
        self._in_text = False
        self._start_time = time.time()
        self.session_id: Optional[str] = None
        self.cost_usd: Optional[float] = None
        self.duration_ms: Optional[int] = None

    def render_event(self, event: dict):
        """Dispatch a single SSE event to the appropriate handler."""
        t = event.get("type", "")
        if t == "text_delta":
            self._on_text_delta(event.get("text", ""))
        elif t == "tool_use":
            self._flush_text()
            self._on_tool_use(event)
        elif t == "done":
            self._flush_text()
            self._on_done(event)
        elif t == "error":
            self._flush_text()
            self._on_error(event)
        elif t == "session_reset":
            self._on_session_reset(event)
        elif t == "reconnecting":
            self.console.print("[dim yellow]Reconnecting...[/]", highlight=False)

    def _on_text_delta(self, text: str):
        """Accumulate text and print incrementally."""
        if not self._in_text:
            self._in_text = True
            self.console.print()  # blank line before response
        sys.stdout.write(text)
        sys.stdout.flush()
        self._text_buffer += text

    def _flush_text(self):
        """End the current text block."""
        if self._in_text:
            sys.stdout.write("\n")
            sys.stdout.flush()
            self._in_text = False
            self._text_buffer = ""

    def _on_tool_use(self, event: dict):
        """Show tool use as a dim one-liner."""
        summary = event.get("summary", event.get("tool", "?"))
        self.console.print(f"  [dim]{summary}[/]", highlight=False)

    def _on_done(self, event: dict):
        """Show session info, cost, duration."""
        self.session_id = event.get("session_id")
        self.cost_usd = event.get("cost_usd")
        self.duration_ms = event.get("duration_ms")

        parts = []
        if self.session_id:
            parts.append(f"session: {self.session_id[:12]}...")
        if self.cost_usd is not None:
            parts.append(f"${self.cost_usd:.4f}")
        if self.duration_ms is not None:
            secs = self.duration_ms / 1000
            parts.append(f"{secs:.1f}s")

        if parts:
            self.console.print()
            self.console.print(f"[dim]{'  |  '.join(parts)}[/]", highlight=False)

    def _on_error(self, event: dict):
        """Print error to stderr."""
        msg = event.get("message", "Unknown error")
        err_console = Console(stderr=True)
        err_console.print(f"[bold red]Error:[/] {msg}", highlight=False)

    def _on_session_reset(self, event: dict):
        """Notify user about container recreation."""
        reason = event.get("reason", "Container was recreated")
        self.console.print(f"[yellow]{reason}[/]", highlight=False)
