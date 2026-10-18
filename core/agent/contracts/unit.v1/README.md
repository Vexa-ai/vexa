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

## The live unit's input stream (`InputEntry`)
A warm unit takes the person's next message from `unit:<id>:in`. agent-api is the stream's only writer,
and Redis cannot say who wrote an entry, so each entry is signed:

| Field | Value |
|---|---|
| `turn` | the message, as a JSON string |
| `sig` | HMAC-SHA256 of the `turn` string, keyed with the unit's input key, 64 lowercase hex digits |

The unit key is HMAC-SHA256(`INTERNAL_API_SECRET`, `"vexa-unit-input.v1:" + <unit id>`), hex. agent-api
derives it per unit and hands it to that unit's worker as `VEXA_UNIT_IN_KEY`; no other worker holds it.
A worker runs only entries that verify under its key, and none when it has no key; the chat's pending
list shows only verifying entries too. The implementation is `core/agent/shared/unit_input.py`;
`InputVector.chat-follow-up.json` pins it, and `validate.mjs` re-derives it in Node.

**Upgrade agent-api and the worker image together** (v0.13.2 or later on both). A v0.13.2 worker under
an older agent-api finds every follow-up unsigned and drops it, silently. Compose's
`AGENT_WORKER_IMAGE` can pin the worker apart from agent-api; when it is pinned, move it in the same
upgrade.

## The unit's typed faults (`Fault`)
When something on the chat path fails, the person is told WHO failed (`source`), HOW (`kind`), one safe
sentence (`detail`) and what can be done (`remedy`) — P18. The fault crosses two process boundaries,
worker → agent-api → terminal, on the unit's output stream and around it:

| Carrier | Writer | `$def` |
|---|---|---|
| a failed turn's `done` on `unit:<id>:out`, relayed to the chat stream | the worker — `llm/faults.py` (every harness), `worker/tool_access.py`, `llm/claude_code.py` | `DoneFrame` |
| the chat stream's `error` frame | agent-api's relay (`control_plane/unit_faults.error_event`); the terminal's proxy (`src/app/api/chat/route.ts`) | `ErrorFrame` |
| agent-api's 502/503 answer to a refused dispatch | `control_plane/unit_faults.answer` | `DispatchRefusal` |
| a blocked inbox row and the pending list's banner (`unit:<id>:fault`, with `at`) | agent-api | `Fault` |

`FaultSource` names every source; each source's kinds are the `$def` named after it
(`model-provider` → `ModelProviderFaultKind`), and a `kind` outside its source's vocabulary does not
conform. The kinds are listed once, in the schema (and, held to it by `gate:fact-parity`, in
`docs/docs/api/agent.mdx`).

| source | who failed |
|---|---|
| `runtime` | the agent runtime agent-api asks to start a worker |
| `model-provider` | the model's provider |
| `vexa-tools` | the turn's Vexa tool access |
| `agent-worker` | the worker, refusing to start a turn in the environment it was given (tools it cannot confine, a model credential it cannot remove) |
| `agent-api` | agent-api, answering a 5xx with no typed fault (named by the terminal's proxy) |
| `gateway` | the gateway, unreachable from the terminal |

**No side spells a kind.** `gen-faults.mjs` generates the vocabulary into `core/agent/llm/fault_wire.py`
(the model provider's side — `llm/` imports nothing from product code), `core/agent/shared/fault_wire.py`
(the runtime's and the tool-access side) and `clients/terminal/src/surfaces/faultWire.ts`, whose types
key the terminal's label tables. A kind added here reaches every side on the next
`node gen-faults.mjs`; a generated file that no longer matches fails `validate.mjs --check`
(gate:schema), as does a kind with no `Fault.<source>.<kind>.json` golden.

**Status: sealed** in `contracts.seal.json` (gate:contract-version). An additive change re-seals with
`pnpm seal:contracts` in a `lane:contract` PR; a breaking one is `unit.v2`.
