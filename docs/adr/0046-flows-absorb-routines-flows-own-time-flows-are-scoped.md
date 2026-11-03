# ADR 0046 — Flows absorb routines; flows own time; flows are scoped to a user or the instance

**Status:** accepted · 2026-10-12 · founder: "implement" · [#1783](https://github.com/Vexa-ai/vexa/issues/1783)
· builds on ADR-0037 decisions 5 and 7 · applies P3, P11, P18, P20 and P23
· **amended 2026-10-13** (founder: "fix the old paths to make it clean"): the old routine paths are
removed in this release, not kept for one — decision 5, *Users of `schedule.v1`*, *Consequences*
and *Migration order* below

## Context

Two engines decide *when* something runs.

**Routines** live in agent-api. A routine is a `routines/<name>.md` file in a person's workspace
(`core/agent/control_plane/workspace_routines.py`) or a `POST /api/routines` call
(`routers/routines.py`). Either compiles (`routines.py`) to a `schedule.v1` cron job in the runtime's
scheduler (`core/runtime/src/runtime_kernel/scheduler.py`, a Redis sorted set), whose request POSTs a
signed `unit.v1` dispatch back to agent-api `/invocations`. `routine.v1` is an unsealed stub; its
`kind: event` was never built. The scheduler's job list is the routine registry, and a file
reconciler re-arms it every 60 s.

**Flows** live in `core/flows`. A flow is an event type and an ordered list of steps from a reviewed
vocabulary. Built-in flows ship in the image (`flows_defs`); runtime flows are rows in `flow_version`
(`flows_schema/models.py`) written through `POST /flows` / `flows_submit`, which only the operator
key opens. A fact becomes one `reaction` row per matching flow, and `next_run_at` on that row is
flows' own durable clock: waits, retries and escalation already live there, in Postgres.

So the product has two registries, two clocks and two retry models, and the runtime — which
ADR-0037 decision 7 says only spawns — holds a cron that decides when agents wake. `flow_version` has
no owner, so a person cannot have a flow of their own, and a routine cannot react to an event.

## Decision

1. **Each concern has one owner.**

   | concern | owner |
   |---|---|
   | WHEN — event and schedule triggers, scope, step order, waits, retries, durable state | flows |
   | WHAT TO THINK — one agent turn with an instruction over a workspace set | agent (`unit.v1` via the `agent_run` step) |
   | WHERE — spawning the workload | runtime |
   | WHAT IS ALLOWED — the steps a user flow may use | admin policy, `_global/POLICIES.md` |

2. **A routine is a user-scoped flow** with a schedule trigger and the `agent_run` step. Routines as
   a mechanism retire. "Routine" may stay as a word in the UI.

3. **Flows own all time.** A schedule trigger is a cron (5 fields, UTC) on the flow row, fired by the
   flows worker into an ordinary reaction keyed `schedule:<scope>/<name>:<slot>`, so a slot fires at
   most once by constraint. **Catch-up:** after downtime the worker fires the most recent missed slot
   once, if it is younger than `VEXA_FLOWS_SCHEDULE_CATCHUP_S`; older slots are skipped and counted.
   No slot before the version was created fires. The runtime scheduler (`schedule.v1`) retires once
   nothing calls it — see *Users of `schedule.v1`* below.

4. **`flow_version` is scoped.** `scope` is `instance` or `user:<uid>`, and the key becomes
   `(scope, name, version)`, so two people and the instance can each own a `morning-brief`. Existing
   rows migrate as `instance`. A user flow is admitted only for an event whose subject is its owner.
   Events from a shared workspace are an open question, below.

5. **One source of truth, one authoring path.** `flow_version` is the record. `POST /flows` and
   `flows_submit` take the scope from the caller: a person's own credential (through the gateway,
   ADR-0041) writes `user:<uid>`; the operator key, or the deployment's admin account asking for
   it (its own credential, or its chat's delegation), writes `instance`; the operator key may write for a named person (the routine migration).
   Nobody names another person's scope. Reads, retire and run history check ownership. Workspace
   pages are generated mirrors.

   **There is no file-based routine mechanism (amended 2026-10-13).** agent-api converts
   `routines/*.md` files and the routine jobs left on the runtime scheduler into the owners' flows
   **once**: the migration runs at startup and retries until one pass completes with nothing left
   to move, then stops for good. Files with one intent (each other's, or an existing flow's) merge
   into one flow — the flow kept, the newer schedule carried. A file nobody stood behind (written
   by an unwatched run) becomes a paused flow its owner resumes. A file that cannot be read, or that
   flows refuses, runs nothing and is shown on the Routines board with the exact reason. Every
   converted or refused file is then removed from the workspace, its copy kept outside every
   workspace mount (`<store>/.routines/retired/<subject>/`). A `routines/*.md` written afterwards
   does nothing. The file reconciler, `routine_resign.py`, the routine→`schedule.v1` compile path
   (`routines.py`) and the confirmation board are **removed**. The Routines board reads the person's
   schedule flows: its switch pauses and resumes, delete retires.

   **A refused run pauses its routine (restored on flows, 2026-10-13).** A routine refused for the
   same reason three runs in a row pauses; the reason is posted into the routine's thread and shown
   on the Routines board. Alerts go to that thread by default; only a connection the person
   explicitly chose, and that works, carries outward notifications (`params.notify`).

6. **Every step carries a user-allowed marker.** The image marks the steps a user flow may name;
   `user_flow_steps` in `_global/POLICIES.md` narrows or widens that set within the vocabulary. The
   default is `agent_run` alone. Submitting a user flow with any other step is refused with
   `step_not_allowed` naming the step.

7. **A user flow's agent turn runs `autonomous`.** `agent_run` dispatches with trigger `scheduled`,
   which the delegation contract maps to the `autonomous` regime: nobody is in the loop, so the tools
   that spend credentials, send mail or delete are refused unless policy allows, as for routines
   today.

8. **Flows cannot loop.** A reaction carries its origin chain (`_origin` in its refs: the flows
   whose steps produced the event). An event cannot start a flow already in its chain more than
   `VEXA_FLOWS_LOOP_LIMIT` times, nor any flow once the chain is `VEXA_FLOWS_MAX_HOPS` long. A refused
   hop is logged with counts, never silent.

9. **User flows have quotas.** Per person: active flows, the minimum interval between schedule
   fires, and runs per day — declared in flows' `config.v1` with defaults. A submission over a quota
   is refused with a typed reason (`quota_active_flows`, `quota_min_interval`); a run over the daily
   cap is not admitted, and is counted.

10. **A person sees their own runs.** `GET /flows/runs` answers the caller's flows' reactions with
    their receipts, and nobody else's. It feeds the per-user mirror page.

11. **Contracts.** `flows.v1` gains `FlowDefinition` (scope, trigger `event` | `schedule`, steps,
    params) beside the carrier census, re-sealed as a back-compatible change. `routine.v1` is retired
    after the adapter release. `schedule.v1` retires with the scheduler.

12. **A routine's agent is a long-lived chat in the person's list** (founder, 2026-10-12). The same rule now holds for
    meetings (ADR-0048 decision 6): a meeting started from a chat is that chat's session.
    `agent_run` targets a chat THREAD, not a one-off job. **A chat is a standing concern that owns
    routines** (founder, 2026-10-13): it may own several (`routine_claim` moves one into it — the
    active version's chat binding changes, no new version; `routine_add_trigger` adds a timer), each
    wake is a turn there with the chat's history as memory, and a wake that finds nothing new
    answers one `Checked —` line, drawn as a muted tick. A **routine** is a flow on a schedule; an
    **event agent** is a flow on an event (`params.thread: "per:<ref>"` opens one concern chat per
    value — e.g. per meeting — and routes a repeat into the same chat). A **concern chat** has a
    lifecycle — opened → active → waiting → resolved → archived (`concern_stage`) — shown on the
    chat and readable by the home stream; a **sink** subscribes one existing chat to an event
    stream (cards, no agent run unless its wake policy says). **Trust:** routines and sinks run
    under the background policy (`connection_actions_unattended`); an **event agent is autonomous
    and untrusted** — least privilege by default: every workspace mounted read-only and an empty
    tool write ceiling, no send, draft, share or connection write; each such act is a proposal
    card (a `needs_go` action) in its concern chat that the person presses to run as their own
    turn. `event_agents_pause` is the kill switch (the person's, or every person's for the
    administrator). Raising one agent's trust per capability (the owner, explicitly) under an
    administrator cap (`event_agent_max_trust`) is the next step and not built yet. **A routine runs in the chat where it is
    created**: `flows_submit` from a chat turn passes that chat's id as
    `params.thread` (each turn is told its own id), every run continues that chat with its history,
    and the rail marks the chat as a routine with the flow it belongs to and shows its Pause/Retire
    strip. A routine created with no chat (a background or admin call, an API client) gets its own
    thread, its id derived from the scope and the flow's name (`flow-<digest>`), created on the
    first run and titled with the flow's name. Every
    run appends a turn to it, so the agent inherits every previous run through the same
    per-session history and quiet compaction a chat already has — there is no second conversation
    store. Since ADR-0048 that history is the session store: the thread is a session bound to the
    flow (`flow` = `<scope>/<name>`), and a run, like a person's turn, is handed the session's head
    version and saves the next one. A run's turn carries the flow mark and never renders as the person's words. The person
    can open the thread and reply; their reply is an ordinary chat turn (`human` regime) and the
    next scheduled turn sees it, while scheduled turns stay `autonomous` (they run on a sibling warm
    unit of the same thread, `…-auto`, because a worker's regime is fixed when it is spawned). One
    thread never runs two turns at once: a run that comes due while the thread is busy waits on
    flows' own clock until it is idle, and a person's turn sent while a scheduled turn is mid-run is
    refused with `thread_busy` and sent again. `params.thread: new` gives a fresh thread per run.
    From the thread the person pauses, resumes or retires the flow (`flow_lifecycle`, ownership
    checked); deleting the chat asks whether to retire the flow too and never does either silently.

13. **Agents speak to each other through flows** (founder, 2026-10-12). An agent's address is its
    chat thread id — a person's chat or a routine's thread. The `message_agent(thread, text)` tool
    (agent domain) publishes the carrier `thread.message`; an instance flow delivers it into the
    target thread as a queued turn marked as from the sending agent (its thread, and its flow or
    person), never as the person's words, and the receiver answers with the same tool into the
    sender's thread. Asynchronous only: there is no ask-and-wait, so two agents cannot deadlock.
    The loop guard (decision 8) caps ping-pong by the origin chain each message carries; messages
    count against the sender's own daily message cap (`VEXA_FLOWS_USER_MESSAGES_PER_DAY`, a
    quota beside decision 9's); the sender's session rides the event so
    each side's trace links the other. Who may message whom is policy in `_global/POLICIES.md`, read
    like every other rule: by default freely between one person's own threads, never between two
    people, and never from a background routine (`agent_messages_own`, `agent_messages_shared`,
    `agent_messages_from_routines`). A refusal is typed and names the key that refused it.

    **Across two people (2026-10-13).** A delivered message starts a turn with nobody watching, so
    another person's own threads are never an address: not their `main`, not a routine's
    `flow-<digest>` thread, not a stored session the sender is not in or only reads. With
    `agent_messages_shared` switched on (it is off by default), a message to another person is
    delivered only into a stored session (`s-…`) that no flow owns, where the receiver takes part
    and the sender takes part with write (`owner` or `member`), and only when the sender holds a
    writing role (`owner` or `contributor`, never `viewer`) in a workspace the receiver also
    belongs to. agent-api checks this at send and again where the delivered turn starts; flows'
    intake also refuses any cross-person target that is not an `s-…` session. Every way a
    cross-person address fails answers one identical refusal (`agent_messages_shared`), so the
    answer says nothing about which of the other person's threads exist. A delivered turn into a
    session with two or more people already runs with that session's narrowed mounts and ceiling
    (ADR-0048). Same-person delivery is unchanged.

    **Who is sending.** A worker running with nobody in the loop (the delegation's regime is not
    `human`) sends as a routine unless the thread it names carries the chain of a message
    delivered into it within the last hour; then it carries that chain forward. A routine naming
    `main` therefore still meets `agent_messages_from_routines`, and naming a different thread does
    not reset the hop count. Binding `from_thread` to the exact calling unit needs the unit in the
    signed delegation, which delegation.v1 does not carry yet.

## Users of `schedule.v1`

| caller | what it does | fate |
|---|---|---|
| agent-api `routers/routines.py` | `POST /api/routines` armed a job | **no longer calls the scheduler** (2026-10-13): a deprecated adapter onto flows (`Deprecation: true`) until 0.14.0, then removed; `501 routines_need_flows` without flows; `confirm` answers `410 routine_files_retired` |
| agent-api `routines.py`, `workspace_routines.py`, `routine_resign.py` | compiled routines to jobs, reconciled `routines/*.md`, re-signed old jobs | **removed** (2026-10-13) |
| agent-api one-time migration | lists the remaining routine jobs and cancels each once its flow exists | the **only** remaining agent-api use of the scheduler; ends when the migration completes |
| meeting-api `scheduling/`, `lifecycle/retry.py` | validate against the shape; an in-process mirror scheduler | wired only in tests — moves to flows or is deleted, not yet decided |
| runtime `POST/GET/DELETE /schedule` | the scheduler itself | retires once the list above is empty |

After 2026-10-13 the remaining `schedule.v1` users are the runtime scheduler itself and
meeting-api's unwired scheduling code.

`schedule_digest.py` reads the meeting schedule into a prompt block. It is not a user.

## Open questions

- **Shared-workspace events.** A user flow reacts only to its owner's events. Whether a member's
  flow should also see events from a workspace they share is not decided; until it is, it does not.
- **meeting-api's scheduled bots** use the `schedule.v1` shape in code that is not wired. Whether
  scheduled and retried bots become flows or that code is deleted is a separate decision.

## Alternatives

- **Keep both.** Rejected: two registries, two clocks, and a routine still cannot react to an event.
- **The runtime cron emits `schedule.due` events into flows.** Rejected: still two clocks, and WHEN
  stays in the primitive that should only spawn.
- **Routines for users, flows for admins.** Rejected: two registries for one idea, split by who is
  typing.

## Consequences

- Flows becomes a hard need for scheduled agent turns. A deployment without flows has no routines,
  from this release (amended 2026-10-13: there is no deprecated scheduler path), and the routine
  routes answer `501 routines_need_flows`. The Helm chart sets `VEXA_FLOWS_API_URL` and
  `VEXA_FLOWS_API_KEY` on agent-api so the migration and the adapter reach flows.
- One clock in Postgres. A flows outage delays schedules; the catch-up rule bounds the burst after.
- The routine routes stay as adapters onto flows until 0.14.0, then go. The terminal's Routines view
  reads the person's schedule flows.
- Old routine files leave the workspace. One that cannot become a flow is shown with its reason
  instead of silently running on the old scheduler; its copy sits outside every workspace mount.
- A person can now author flows. The allow-list, the loop guard and the quotas are what make that
  safe, and they are the cost of this decision.
- A routine is something a person can talk to: its thread is in their chat list, carries its whole
  history, and accepts a reply. Long threads lean on the existing compaction. The price is a
  second warm unit per active routine thread (the `autonomous` sibling) and a `thread_busy`
  refusal the terminal has to resend.
- Agents can hand work to each other without a person relaying it. Every such hop is a flows
  reaction, so it is durable, queued, loop-guarded, counted and visible in run history; the price
  is that a message is never answered synchronously.

## Migration order

1. `flows.v1` `FlowDefinition`; `flow_version.scope` and the new key.
2. Schedule triggers, scoped admission, `agent_run`, scope from the caller, the allow-list, loop
   guard and quotas.
3. Routines into user flows (files and jobs, idempotent, counts-only logs), once at startup,
   retried until complete, then stopped; refused and unreadable files surfaced; converted files
   retired outside every mount. The file reconciler, `routine_resign.py`, the compile path and the
   confirmation board are removed in the same change; routine routes become adapters until 0.14.0;
   the refused-run pause rule moves onto flows; Helm gives agent-api the flows URL and key.
4. Other `schedule.v1` callers move or are listed (now: the runtime scheduler and meeting-api's
   unwired code); the scheduler retires when the list is empty.
5. Mirrors: a per-user flows page; the terminal lists scheduled flows; routine threads in the rail.
6. `thread.message` and `message_agent` (decision 13), after the routine thread.
7. Docs, changelog, upgrade note.
