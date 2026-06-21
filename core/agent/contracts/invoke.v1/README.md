# invoke.v1 — the meeting → agent trigger (UNSEALED)

The control-plane edge that fires an agent run from a meeting. The transcript **bridge** subscribes the
meetings transcript egress and, on a configured trigger, emits an `Invocation` that `agent-api` turns
into a `runtime.v1` agent worker.

- **`Trigger`** (`on`) — *why* the run fires: `meeting.completed` (post-session summary) ·
  `chat_invocation` (a user asked the agent something) · `scheduled` (a `runtime.v1` schedule fired).
- **`MeetingRef`** (`meeting`) — *which* meeting, by `meeting_id` + `session_uid` (the same ids that key
  `transcript.v1`). The meeting is referenced **by id, never by importing meetings code** — the
  `meetings ⊥ agent` boundary.
- **`workspace_repo` / `workspace_ref`** — the user `workspace.v1` git repo the run commits to.
- **`prompt`** — free-text ask, present for `chat_invocation`, null/absent otherwise.

Note this envelope carries *why + which*, **never the transcript bytes** — those cross the seam as
`transcript.v1`. No tenancy fields (deferred, ADR-0003).

**Status: UNSEALED** (in development) — not yet pinned in `contracts.seal.json`; `gate:contract-version`
reports it as unsealed, `gate:schema` validates its goldens.
