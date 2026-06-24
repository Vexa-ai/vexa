# unit.v1 — the agent-runtime-unit invocation (the universal trigger envelope) — UNSEALED

The one control-plane envelope that fires **any** unit. Chat, scheduled routine, event worker, and the
live in-meeting agent are the **same** unit; they differ only by `trigger`, `context`, and `lifecycle`.
The dispatcher emits an `Invocation` (from any trigger source) and `agent-api` turns it into a
`runtime.v1` agent worker (profile `agent`, the per-person workspace mounted, claude-in-container).

This **supersedes the sealed, meeting-shaped `invoke.v1`** (which required `meeting`). `invoke.v1` stays
frozen for the meetings path (`bridge.py`) and is retired once that path migrates; `dispatch.py` emits
`unit.v1`. See `clients/terminal/docs/FOUNDATION.md`.

- **`Trigger`** — `message` (chat turn) · `scheduled` (a `schedule.v1` cron fired) · `event` (an
  event-source published, e.g. `email.received`) · `transcription` (a live transcript beat, or
  `session_end`). **All four are frozen now** so MVP stages add behavior, not enum members.
- **`Context`** — `kind` discriminates: `none` (a bare chat) · `meeting` (`MeetingRef`, by id —
  the `meetings ⊥ agent` boundary) · `email`/`generic` (an opaque `SourceRef` a **tool** resolves —
  email/calendar/tasks are tools+event-sources, **not** platform domains).
- **`subject`** — the `identity.v1` subject = the **person** = the quota owner (`VEXA_OWNER`) and the
  cred-brokerage key. The workspace is per-person (`workspace_repo`).
- **`plan`** (path and/or prompt) · **`lifecycle`** (`oneshot`/`warm` → `runtime.v1` idle/maxlife) ·
  **`output`** (the `ws.v1` per-unit topic + modes) · **`tools`** (the scoped `tool.v1` allow-set →
  `--allowedTools`).

The envelope carries *why + what + who + where*, **never domain bytes** (transcripts cross as
`transcript.v1`; emails/docs ride as opaque `SourceRef`s). Every optional field is present from day one;
MVPs **populate** them — they must never re-cut this envelope. No tenancy beyond `subject` (ADR-0003).

**Status: UNSEALED** (in development) — not yet pinned in `contracts.seal.json`; `gate:schema` validates
its goldens, `gate:contract-version` reports it unsealed. Sealed via `pnpm seal:contracts` on a
`lane:contract` review at the end of the Foundation phase.
