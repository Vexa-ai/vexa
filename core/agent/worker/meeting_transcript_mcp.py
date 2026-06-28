"""meeting_transcript_mcp.py — SCAFFOLD / TODO(phase-5): the meeting-read MCP server.

This is the runtime piece deferred in CONTROL-PLANE.md §6. The `meeting.read_transcript` tool descriptor
(tools-seed/meeting-read-transcript.json) + the dispatch grant + the `VEXA_MEETING_TOKEN`/native-id env
are already wired (CP6). What remains, built here in Phase 5:

  - an MCP server that, on a tool call, fetches the transcript FRESH from meetings' published REST
    contract: `GET {VEXA_GATEWAY_URL}/transcripts/{platform}/{native}` (cross-domain, legal read — P23/P3).
  - it presents `VEXA_MEETING_TOKEN` (the scoped, brokered read:meeting token) as auth.
  - meeting-api verifies that meeting-scoped token (the other half, in the meetings domain).

The chat agent gets this granted server-side ONLY when `active={kind:meeting}` — so a meeting chat reads
the live transcript through the contract on demand, never a file, never the user's real key (P15).
"""
from __future__ import annotations

import os


def serve() -> None:
    """Run the meeting-read MCP server (stdio/http per the tool descriptor). TODO(phase-5)."""
    raise NotImplementedError(
        "meeting-read MCP server not built yet (CP6 deferred, CONTROL-PLANE.md §6) — Phase 5: "
        "front GET {VEXA_GATEWAY_URL}/transcripts/{platform}/{native} with VEXA_MEETING_TOKEN auth."
    )


def _target_url(platform: str, native: str) -> str:
    """The published meetings REST endpoint this server reads on a tool call."""
    base = os.environ.get("VEXA_GATEWAY_URL", "").rstrip("/")
    return f"{base}/transcripts/{platform}/{native}"


if __name__ == "__main__":  # pragma: no cover
    serve()
