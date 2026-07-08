# ADR 0027 — Meeting processing: one fact, one carrier (SSOT for the real-time pipeline)

**Status:** accepted · 2026-07-08 · applies **P4/P8/P22/P23** to the live-meeting processing path ·
extends ADR-0024's guarantee-teardown discipline to agent workers

## Context

Live debugging of meeting row 46 (eyeball, 2026-07-08 12:10 UTC) proved three user-visible defects —
slow processed-notes delivery, a full-history backfill that never ran, and processed notes vanishing
after bot stop — and traced every one of them to the same structural disease: **each fact in the
pipeline has several competing homes, and no layer owns the truth.**

The evidence (redis stream ids are ms timestamps, so the timeline is exact):

- **Dispatch semantics were fiction.** runtime.v1 documents `workloadId` as a "caller-assigned id
  (idempotency key)", and the transcription watcher's re-arm is written against "spawns if reaped,
  touches if running" — but no layer implements a touch. `Kernel.create` always spawns;
  `DockerBackend.start` resolves the name conflict by **force-deleting the RUNNING container**. Run 46
  had **4 copilot spawns in 32s**: the toggle's full-history backfill dispatch was killed ~1s after
  spawn by the watcher's tail-armed dispatch; the 30s keep-alive then killed each successor mid-beat,
  the last one 1 second before session_end. Two components, each certain the other holds an invariant
  nobody implements (the exact ADR-0024 shape, on the worker side).
- **The durable flush races the final beat.** On `session_end` the worker runs one last polish beat
  (~10s of LLM latency) — but meeting-api's `finalize_meeting` drains `proc:meeting:{row}` inline at
  the FSM terminal callback, and the meeting then leaves the db-writer's 10s sweep entirely. Run 46's
  durable `source_cursor` froze at `1783512746260-0` while the stream tail reached `1783512757882-0`:
  the final polished notes exist only in redis (no TTL, no re-drain — stranded forever).
- **The live view invents its own end.** The SSE ends on a 1.5s quiet poll after `session_end` —
  before the final beat lands — and never reads `proc:meeting:{row}` at all, so the instant baseline
  notes (emitted ~2s after a segment) never reach the UI; only LLM beat notes ride the out-stream,
  6–12s+ behind speech. The terminal compensates with a named retry budget and a stale-liveness
  arbitration function — the backend exported its consistency problem to the client.

The inventory behind those symptoms:

| fact | homes today |
|---|---|
| processed notes | `proc:meeting:{row}` stream · `unit:agent-meet-{sid}:out` note events · envelope JSON file · workspace markdown file · `meetings.data.processed.views[]` |
| processing opt-in | `proc:…:on` flag (never reaped) · a dispatched worker · the live-registry entry · the terminal toggle — with **two** independent dispatchers (the `/process` handler and the watcher) racing on the resume position |
| meeting liveness | meetings FSM row · `session_end` marker · in-memory live registry · SSE `ending` heuristic · terminal staleness patch |

P23 ("one writer per carrier") was satisfied **by proliferating carriers** — each home has one writer,
so `gate:dataflow` stayed green while the same fact diverged across five of them.

## Decision

**One fact, one authoritative carrier; every other home is a declared derivation or is deleted.**
Concretely, three moves:

1. **Make runtime.v1's documented idempotency real (P22 for workers).** `Kernel.create` returns the
   existing status when the workload is `starting`/`running` (a touch: no spec overwrite, no quota
   count, no spawn); only an absent/exited workload spawns, where the 409-replace remains as
   stale-container cleanup. The watcher becomes the **single dispatch arbiter** (a reconciler:
   desired state = the `:on` flag, written by `/api/meeting/process`, which no longer dispatches),
   always resuming from the one cursor (`proc:meeting:{row}:cursor`, else `0-0`), and reaping the
   flag on `session_end`. Fake-backed tests pin the touch, the quota interplay, and the previously
   uncovered 409 branch.
2. **Publish the processed-notes seam and give processing an explicit end (P4 + P22).** The
   `proc:meeting:{row}` stream already crosses the agent→meetings process boundary unschema'd; it
   becomes **`processed-notes.v1`** (schema + goldens: `Note`, `ViewEnd`). The worker emits a terminal
   **`view_end` marker** after its final beat; meeting-api's finalize drains, and if the marker hasn't
   arrived, parks the row in a deadline-bounded `processed_pending` sweep (marker = graceful fast
   path, deadline ≈ 120s = hard guarantee — ADR-0024's pairing). The durable
   `data.processed.views[]` is then a *complete* derivation, not a racing copy.
3. **Collapse the note carriers (P23 by subtraction).** The SSE tails `proc:meeting:{row}` directly
   (baseline notes reach the UI in seconds) and **closes on `view_end`**, not a quiet-poll guess; the
   out-stream is demoted to what its name says (cards + agent activity); the envelope file loses its
   per-note writes (it has no reader) and the markdown mirror moves to per-beat cadence. Liveness
   stays owned by the meetings FSM with `session_end` as its only event.

## Consequences

- The three defects close structurally, not by patching: churn/backfill (move 1), stranded final
  notes (move 2), laggy/truncated live view (move 3). Verified per train on the eyeball with the same
  stream-timestamp probes that produced the run-46 evidence.
- The terminal keeps its finalizer retry budget (durable completion can now trail a stop by up to the
  pending deadline) but sheds the stale-liveness inconsistency (one shared `deriveProcessingView`).
- Trade-offs: a touched dispatch ignores the new invocation's env (the running worker keeps its
  original brief — correct, since the transcript stream carries the new data anyway); durable
  completion is marker-or-deadline rather than instant (bounded ≤ ~120s post-stop, vs. wrong forever
  before); the SSE cursor grows a third component (decode is pad-tolerant, so old `t|o` ids resume
  with an idempotent proc replay).
- Deferred, recorded here so they don't get lost: the meetings-internal raw-segment carrier collapse
  (`meeting:{id}:segments` hash and the `tc:…:mutable` pubsub are the same disease intra-domain),
  proc-stream TTL/GC after durable completion, and retirement of the legacy native-keyed
  `/api/meeting/start` shape.
- **Generalizes:** "one writer per carrier" is necessary but not sufficient — SSOT also requires *one
  carrier per fact*, with every additional home explicitly declared as a derivation (and its
  completeness guaranteed by a marker or a deadline, never by timing luck). When two components each
  rely on an invariant "someone else" implements, the invariant belongs in a contract with a test at
  the seam.
