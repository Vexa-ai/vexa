# ADR 0050 — Sensors sense, triggers schedule, agents only think: flows get a script muscle

**Status:** proposed · 2026-10-14 · amended 2026-10-14 (decision 6: user-owned script sensors) · next sprint (not 0.13.3) · builds on ADR-0046 (flows own time,
flows are scoped), ADR-0040 (credential broker), ADR-0041 (the edge signs identity), ADR-0048
(sessions) · founder: *"we will have thousands of scripts that check for new calendar events every
minute … flexible about things we plan and schedule, triggers, and things that chain triggers."*

## Context

Today the only way a person's flow can notice that something changed in the world is an agent run.
A person's flow may use two steps, `agent_run` and `post_event_card`
(`core/flows/src/flows_steps/routine.py:137,214`; default `user_flow_steps` in
`flows_steps/policies.py:109`), and has two triggers, `event` and `schedule`
(`flows_integrations/flows_api.py:828-849`). Cron is five fields, minute-granular, UTC
(`flows/schedule.py:14-16`), fired from the worker's 10 s reconcile pass (`flows_worker/__main__.py:69,77`).
So "watch the airport board every 30 s" is impossible and "check my calendar every minute" costs a
full agent run per tick, capped at 48 runs a day (`VEXA_FLOWS_USER_RUNS_PER_DAY`, `flows_config.py:255-276`).

What exists and what does not (checked at 993eb9748):

| Question | Finding |
|---|---|
| Provider change detection (`syncToken`, `historyId`, `deltaLink`, `events.watch`, Gmail `users.watch`, Graph subscriptions) | **None.** Calendar reads are a window `events.list` with timeMin/timeMax (`credential_broker/providers.py:193,293-312`). |
| Provider webhook receiver | **None.** The only Google endpoint is the OAuth callback (`routes_connections.py:307`). |
| Microsoft calendar/mail connection | **None.** Broker catalog is `google_calendar`, `google_email`, `custom_secret` (`providers.py:31-41`). Graph is used only for the bot's own shared mailbox, without delta (`flows_integrations/graph_inbox.py:17-21`). |
| Event transport | HTTP `POST /events` (operator key) into Postgres `reaction` rows (`flows_api.py:1019-1058`, `admission.py`). No NATS/Redis streams. Redis pub/sub is UI frames and bot commands only. |
| Event types | `flows.v1/carriers.json`: `meeting.started/completed/shared`, `invite.received`, `thread.message`, `friction.*`, … plus `meeting.upcoming`, `mail.reply`, `crm.record.changed`. **No `calendar.*`, no `bot.*`** on the flows side. |
| Event filter | Exact `event_type` match (`registry.py:345-354`); a sink's `params.filter` is a substring test inside the step (`routine.py:189-194`). |
| One-shot "at" trigger | **None in flows.** Only a step's `Wait(until=…)` (`flows_steps/meeting.py:411-416`). An unwired one-shot compiler exists in meeting-api (`scheduling/compiler.py:59-86`, tests only). |
| A second clock | meeting-api polls ICS feeds every 300 s (`meeting_api/__main__.py:790-833`) and a 30 s auto-join sweep spawns bots at `scheduled_at − lead` (`bot_spawn/auto_join.py`). It tells flows nothing. |
| Future bot start | `request_meeting_bot` has no start time (`vexa_mcp/app.py:94-155`); `POST /meetings` with `scheduled_at` exists but no tool creates it. |
| Follow a rescheduled invite | `invite_intake@3` reads `refs.start` once; `await_start` waits until `start − 120` (`production.py:2839`, `meeting.py:411-416`). An update to the invite is a new fact the running reaction never sees. |
| Loop guard | `_origin` chain, `LOOP_LIMIT=1`, `MAX_HOPS=8` (`admission.py`). Holds for step-emitted events only. |
| Budgets | Environment (`config.v1`), not `_global/POLICIES.md`; POLICIES carries allow-lists and permissions only. |
| Kill switch | `event_agents_pause` retires every person flow with no cron — sinks included (`flows_api.py:1345-1356`). |

Frictions on app.dev, 2026-10-14: no agent-less poll/diff step and minute cron (08:47); proposals
for user-event ingest and a hosted probe (08:49–08:50); no future-start bot (10:35); a bot that
follows a rescheduled event in one step (10:39); no Microsoft calendar (09:44).

## Decision

Four nouns, one owner each. **Sensors** turn the outside world into typed **events** and never act (decision 6.5a); **triggers** turn events and time into runs; **steps** do the work, and only `agent_run`
thinks. Flows (ADR-0046) stays the owner of WHEN; this ADR gives it eyes and a finer clock.

| concern | owner | durable state |
|---|---|---|
| Provider tokens and every provider API call | credential broker (ADR-0040) | connections |
| Change detection: cursors, watch channels, poll timing | **flows-sensors** (new process, flows image) | `sensor` |
| Events: envelope, dedupe, lineage, retention | flows admission | `event` (new), `reaction` |
| Time: cron, one-shot, keyed relative schedules | flows worker | `flow_version`, `keyed_schedule` (new), `reaction.next_run_at` |
| Thinking | agent (`agent_run`) | sessions (ADR-0048) |

### 1. Sensors (no LLM)

1. **One sensor per (connection, resource), shared by every subscriber.** A Google Calendar
   connection has one `calendar` sensor; a Gmail connection one `mail` sensor; a Microsoft
   connection one of each. A sensor exists while at least one active trigger needs its event types
   and is stopped when the last one retires. Ten flows on "my calendar" cost one sensor.
2. **The broker calls the provider; the sensor owns the cursor.** The broker gains read-only change
   operations — `calendar.changes(cursor)` (Calendar `events.list?syncToken`, `showDeleted`),
   `mail.changes(cursor)` (Gmail `history.list?startHistoryId`), and for Microsoft Graph
   `calendarView/delta` and `messages/delta` (`deltaLink`). Each returns `items`, `next_cursor`, and
   `resync_required` on 410/invalid cursor. Tokens never leave the broker; the sensor never holds one.
3. **Push first, poll as fallback.** Where a public HTTPS endpoint exists, the sensor registers a
   watch (Calendar `events.watch`, Gmail `users.watch` via a Pub/Sub topic, Graph subscriptions with
   `clientState`) and renews it before expiry. A notification carries no data: the gateway's new
   `POST /hooks/{google|graph}` validates the channel token/`clientState`, and only marks the sensor
   due now. The sensor then does the same incremental read. With healthy push the safety poll is
   slow (`sensor_safety_poll_s`, default 900); without push (self-hosted with no public ingress, a
   failed watch) the sensor polls every `sensor_poll_interval_s` (default 60, floor 15).
4. **Resync is bounded and idempotent.** On `resync_required` the sensor re-reads a bounded window
   (calendar: −1 d … +60 d) and lets event dedupe absorb what it has already seen.
5. **Provider quotas are respected centrally.** A token bucket per provider project and per
   connection, `Retry-After`/429/`rateLimitExceeded` back-off with jitter, and a sensor that fails
   N times goes `degraded` with the reason on the connection, never silently. Poll timing is
   jittered over the interval so thousands of sensors never fire in one burst. Push is what makes
   thousands of users affordable: 10 000 connections polled once a minute is 14.4 M calls a day per
   provider, beyond a default per-project quota; with push it is roughly the safety polls plus one
   read per actual change.
6. **Scheduling model: sharded timer heaps over shared threads, not a process per sensor.** The
   `sensor` table (`sensor_id, kind, connection_id, uid, cursor, next_due_at, push_channel,
   push_expires_at, failures, status`) is split into a fixed number of shards; each flows-sensors
   replica leases shards (`FOR UPDATE SKIP LOCKED`, renewed like reaction leases), loads them into an
   in-memory timer heap, and serves ticks from a small shared thread/async pool. A no-change tick is
   one provider read and **zero** database writes: the cursor is checkpointed only when it moves
   (a crash replays from the last checkpoint and dedupe absorbs it). A replica that dies loses its
   leases and another picks the shards up.
7. **`probe` — the generic sensor.** `{source: http GET url | connection read, extract, key, interval_s ≥ 15}`.
   The extract is declarative (a JSON path or CSS selector list and the fields that form an item's
   identity and its compared value); each tick diffs the extracted items against the stored snapshot
   and emits one event per added/changed/removed item, typed `user.<ns>.<name>` by the probe's owner.
   The fetcher is SSRF-guarded (public addresses only, re-checked after DNS, no redirects to private
   ranges), 5 s timeout, 1 MB cap, no cookies; a `connection read` source goes through the broker.
   Probes are per user and count against `probes_per_user` (default 10).
8. **Phase 2 — user scripts from workspaces, in a sandboxed pool.** A script is a file in a
   workspace, pinned to a commit, with the shape `tick(state, ctx) → {state, events}`. It runs as a
   short invocation inside a **shared pool of long-lived sandbox workers**, not a container per
   script: the same sharded timer heap decides when; a pool worker runs the invocation in a fresh
   isolate (WASM/JS isolate or a forked sandboxed process from a warm template — chosen in the
   phase-2 spike), with a CPU budget per tick (default 200 ms), wall cap, memory cap and a state cap
   (64 KB). Its only egress is the probe's guarded fetcher limited to the hosts the script declares,
   and its only output is events. Never inside flows-worker. Caps are per user
   (`scripts_per_user`, `script_cpu_s_per_day`); a script that exceeds them is paused with the reason.

### 2. Events — one typed bus

1. **One envelope.** `{event_id, type, uid, source, occurred_at, dedupe_key, payload, lineage[], trust}`.
   `uid` is the subject (ADR-0046 decision 4: a person's flows fire only on events about them).
   `trust` is `system` (a platform producer or sensor) or `user` (ingested or emitted by a person's
   flow or script). A new `event` table holds it, unique on `(uid, dedupe_key)`, retained 7 days;
   admission writes it and then the reactions, in one transaction. Postgres stays the bus.
2. **System types are registered** in `flows.v1/carriers.json`: `calendar.event.created|updated|cancelled`
   (payload: `event_id`, `calendar_id`, `start`, `end`, `title`, `video_url`, `self_response`,
   `organizer`, `etag`), `mail.received` (ids, from, subject — never the body), `bot.requested|joined|failed|stopped`,
   and the existing `meeting.*`. Only platform producers may emit them.
3. **User types are `user.<ns>.<name>`**, schema-free, payload ≤ 16 KB, **always untrusted**: an
   `agent_run` receives it as quoted data, never as instruction text, and a non-LLM step may only
   copy fields into declared slots.
4. **External ingest.** `POST /v1/events` at the gateway with a **per-user ingest token**
   (`event_token_create(ns)` / `event_token_revoke`; scoped to `user.<ns>.*`, stored hashed, shown
   once). Only `user.*` types are accepted; `dedupe_key` is taken from the body or hashed from it;
   rate-limited per token (`user_events_per_minute`, default 60). `chat_subscribe` is unchanged: a
   sink simply subscribes to `user.<ns>.*` like any other type.

### 3. Triggers

A flow has exactly one trigger:

| kind | shape | notes |
|---|---|---|
| `cron` | 5 fields UTC, or 6 with seconds | sub-minute (floor `cron_min_interval_s_light`, default 15) **only when every step is non-LLM**; flows with `agent_run` keep `VEXA_FLOWS_USER_MIN_INTERVAL_S` (900). Needs a 1 s scheduler pass for sub-minute flows instead of the 10 s reconcile. |
| `at` | absolute time | one-shot; retires the flow after firing. `request_meeting_bot(start_at)` compiles to this. |
| `event` | `type` glob (`calendar.event.*`) + `filter` | filter is a small predicate over envelope fields: `==`, `!=`, `in`, `exists`, `contains`, `and`/`or`/`not`. No code, no regex backtracking. |
| `relative` | `event` match + `at: "payload.start - 60s"` + `key: "payload.event_id"` | a **keyed schedule** (below). |

**Keyed schedules — how "the bot follows the calendar event" works.** A `relative` trigger keeps
one row per key in `keyed_schedule(scope, flow, key, fire_at, status, version, payload)`.

* Each matching event **upserts** the row: a new `fire_at`, `version + 1`. A reschedule moves it.
* An event that no longer passes the filter (declined, video link removed) or `*.cancelled`
  **cancels** it.
* The worker claims due rows `FOR UPDATE SKIP LOCKED` and writes one reaction with
  `source_event_id = at:<scope>/<flow>/<key>:<fire_at>`, so a slot fires at most once by constraint;
  a row moved *after* firing re-arms (the meeting moved to later), a row not moved never fires twice.
* Keyed schedules count against `keyed_schedules_per_user` (default 500).

Invite intake uses the same mechanism: an invite and its later updates (same iCalendar UID, higher
SEQUENCE) emit `calendar.event.*`-shaped events, so `invite_intake`'s `await_start` becomes a keyed
schedule and follows reschedules too. meeting-api's ICS sync becomes an `ics` sensor kind and its
auto-join sweep retires: one clock, in flows.

### 4. Steps and chains

1. **Non-LLM steps become user-allowed** (by the image marker, narrowed by `user_flow_steps`):
   `request_meeting_bot` (wraps today's `dispatch_bot`), `stop_bot`, `post_event_card`,
   `post_message` (into the person's own chat; wraps `deliver_message`), `http_call` (the guarded
   fetcher, POST allowed only to hosts listed in the flow's params and approved by policy
   `http_call_hosts`), `emit_event` (`user.*` only). `agent_run` is unchanged.
2. **Chaining has one mechanism, events, plus `then` as sugar.** Within a flow, `steps` is the
   ordered chain (as today). Across flows, a step emits an event and another flow's trigger matches
   it. `then: "<flow>"` on a flow compiles to an `emit_event("flow.<name>.done")` and a matching
   trigger, so it inherits the same guard.
3. **Loop guard.** Every event carries `lineage` (the existing `_origin` chain, now also starting
   at sensors and at external ingest as `ext:<token-id>`). Runtime: `LOOP_LIMIT`/`MAX_HOPS`
   unchanged. Submission: flows builds the static graph *emits → matches* over the person's active
   flows and refuses a submission that closes a cycle containing an `agent_run`, or a cycle of
   non-LLM steps with no `cron`/`relative` delay in it (`cycle_refused`, naming the path).
4. **Budgets move to `_global/POLICIES.md`** (company sets, a user can only narrow; the `config.v1`
   values remain the fallback): `flow_agent_runs_per_day` (48, today's value), `flow_light_runs_per_day`
   (2000), `probes_per_user` (10), `probe_min_interval_s` (15), `cron_min_interval_s_light` (15),
   `keyed_schedules_per_user` (500), `user_events_per_minute` (60), `scripts_per_user` (phase 2),
   `max_hops` (8). Non-LLM runs count only against `flow_light_runs_per_day`; a run that reaches
   `agent_run` counts against the agent bucket. Connection sensors count against nothing per user —
   they are shared and bounded by the number of connections.
5. **Kill switches split.** `event_agents_pause` pauses event agents only (today it also pauses
   sinks; see the context table); `automations_pause` pauses every non-LLM flow of a
   person, or of everyone for the administrator.

### 5. MCP first, then the strip

Agents create everything with tools; the chat is the UI.

* `flows_submit` takes the trigger kinds above and the new steps; `flows_preview(definition)` dry-runs
  it: which sensor it will use (and whether that sensor already exists), what would have fired in
  the last 7 days, the next fire times, the budget it consumes. `flows_list` shows upcoming keyed
  schedules per flow.
* `probe_create / probe_list / probe_delete`, `event_token_create / event_token_revoke`,
  `events_recent(type_glob)` (the person's own events, for debugging), `sensors_status` (health per
  connection sensor: push/poll, last tick, degraded reason).
* The routine strip and **Show flow** draw the same flow: sensor → filter → keyed schedule → steps,
  with a *no AI* badge when no step is `agent_run`, and the next three fire times. No new screens:
  a teaching card on connecting a calendar offers one chip, *"Send Vexa to my video meetings"*,
  which is a `flows_submit` call.

### 0. The principle (founder, 2026-10-14): flows is messaging

Flows owns ALL asynchronous messaging in the system:
* **in**: sensors and event ingest;
* **routing**: the bus, triggers, subscriptions, wake policies and chains;
* **out**: email, in-app notifications, webhooks, chat posts, and bot dispatch caused by events.

Synchronous commands a person or an agent issues directly stay direct request/response. Examples: an
agent calling `request_meeting_bot` through MCP, or reading a transcript. Data stays with its owners:
workspaces, sessions, meetings.

**Rule of thumb: if something happens BECAUSE something else happened, it goes through flows.**
Flows is the connector between bots, agents and everything else.

**Boundaries.**
1. Streams stay direct: live status to the UI, a live transcript to a reader, an agent's command to a
   bot.
2. Person-to-person chat is not flows.
3. Flows routes and the owners keep the logic. A step calls the owning domain's API and never
   reimplements its rules.
4. Critical paths need a direct fallback, idempotency keys and per-key ordering (sign-in mail,
   webhooks).

**Migration plan** (connector audit of build 373c5db7b, `~/dev/biz/drafts/2026-10-14-flows-connector-audit.md`):

| # | today | moves to |
|---|---|---|
| 1 | meeting-api sends customer and operator webhooks itself (HMAC, event filter, Redis retry 60/300/1800/7200 s, 24 h, dead-letter, ledger) | a flows **webhook output step**: event filter → signed POST with retries and backoff, a delivery log and per-owner config, the same for people and the global account. Payloads and signatures stay byte-compatible. |
| 2 | meeting-api's ICS calendar sync loop (300 s) | a calendar sensor (decision 6; the `ics` kind) |
| 3 | meeting-api's auto-join loop (30 s) | a keyed schedule. This is already true for invites (`invite_join`, this branch). Calendar-synced meetings then also get `meeting.upcoming`. |
| 4 | two producers of `meeting.started` / `meeting.completed` (meeting-api and invite_intake), deduplicated by id | one producer |
| 5 | agent-api's live transcript watcher | the stream stays direct; the `session_end` fact goes through flows |
| 6 | the dashboard sends sign-in mail with its own SMTP stack | the flows outbound sender, with a direct fallback because sign-in is auth-critical |
| 7 | the runtime's cron scheduler (`/schedule`), still live with nothing arming it | retired |
| 8 | agent-api's `/events` ingress, dormant with no producer | removed, or routed through flows |
| 9 | `run_meeting` polls bot status every 6–8 s inside a step | Wait on the `meeting.*` events |

### 6.0 Sensor ownership by lifetime (founder, 2026-10-14)

1. **Always-on sensors are FLOW-MANAGED.** These sensors outlive any agent run, are event-driven, and
   wake agents through events. Flows owns their schedule, cursor and state, health, enable and
   disable, budgets, audit and kill switch.
2. **Execution is a pluggable adapter behind flows.** The script is identical on every target:
   * (a) *in-flows*: trusted built-in kinds only (calendar and mailbox, as built);
   * (b) *the shared runner pool* with a WASM sandbox: the default for user scripts (6.6);
   * (c) *the owning agent's container*, as a separate restricted process that holds ONLY the
     sensor's grant, never the agent's. This is for live or heavy sensors, and for sensors that
     matter only while the agent is active.
3. **An agent's own working checks are AGENT-MANAGED.** These are dry-running a sensor it is writing,
   watching a live transcript during a meeting, and short polls during a task. They run inside the
   agent's container for the length of its run, in its own loop, and flows does not schedule them.
   They still run under the owner's grants and budgets, are audited, count against the same budget
   and obey the kill switch.
4. **Promotion.** A check that must outlive the run is handed to flows with `sensor_create` →
   `sensor_test` → approve → `sensor_connect`, and becomes an always-on sensor.

**Parity.** People and the global account have the same sensors, flows, scripts, triggers and tools.
"Global" means only shared flows: owned by the global account and applying company-wide. Scope,
ownership and `_global` policy caps are the only differences.

**Global-owned artifacts are seeds.** Images carry the engine. Built-in company flows are seeds,
written once into the global account's store and never overwritten on upgrade (the new seed is
offered as a diff). The global agent changes them from chat: test, approve the capability diff,
enable, or roll back. `invite_join` is the first.

### 6. Amendment (founder, 2026-10-14) — a sensor is a user-owned script

This amends decision 1. Where it and decision 1 disagree, this wins.

**6.1 A sensor is the user's object.** A sensor is `{kind, connection, filter, extract, interval}`
owned by one person, or by the global account for a company sensor. It is made, changed, listed and
removed from chat through MCP (`sensor_create`, `sensor_update`, `sensor_list`, `sensor_delete`).
Ownership, filter, tests, health, budget and audit are per owner. Fetches for one connection may be
shared underneath (one provider read feeding several owners' sensors on that connection); that is an
optimisation, never a change of owner.

**6.2 The script is the general mechanism.** Every sensor is a script with one contract:
`tick(state, ctx) -> {state, events}`, JSON in and out. The script lives in the owner's workspace,
pinned to a commit. Gmail, calendar and probe are starter scripts. The owner can use one as it is or
fork it, and the interface is the same either way. The agent can write the script from a description.
A new commit is a new version, and a new version runs only after `sensor_test` passes on it.

**6.3 Design, then test, then enable.** `sensor_test(sensor, since=…)` runs the sensor over recent real
data with no side effects. It returns the events the sensor would emit and the items it dropped, with
the reason for each. A sensor is enabled only after a passing test of that exact version.

**6.4 Connected to a persistent agent.** `sensor_connect(sensor, chat, wake=per_event|batch:<min>|
match:<text>|never)` binds the sensor's events to a concern chat or event agent. This reuses
`chat_subscribe` sinks (`post_event_card`, which exists: one card per event, capped per hour, wake
policy). Event agents keep least privilege, proposal-only acts and the kill switch
(`event_agents_pause`, which exists). The routine strip and the chat header show a connected sensor's
health, its last events and its next check.

**6.5 Secrets: the script never holds one.**
1. No env var, file or token reaches the sandbox.
2. The sandbox has no network of its own. All I/O goes through two host calls:
   `connection.request(conn_id, op, params)` and `http.fetch(url, …)`. `connection.request` is executed
   by the credential broker. The broker injects auth, enforces an op allow-list (read-only by default),
   rate-limits, audits, and strips auth from responses. *Exists:* the broker's fixed read ops
   (`read_account`: gmail.search/read/thread, calendar.events) and its audit table. *To build:* the
   incremental ops (`calendar.changes`, `mail.changes`) and the op allow-list per sensor.
   `http.fetch` goes through an egress proxy that allows declared hosts only, guards against private
   ranges and metadata addresses, and caps size and time. *Exists in the broker:* the SSRF guard
   (`ssrf.py`) and DNS-pinned public-address dialling (`secret_service.py`). *To build:* the proxy.
3. A user's API key for a third-party site stays in the broker as a custom secret, bound to its
   approved host. *Exists:* `custom_secret` connections with `approved_host`. The script references
   the key as `{{secret:name}}`, and the proxy resolves it only on requests to that host.
4. Per-tick capability: the runner mints a short-lived signed grant naming the owner, the sensor id
   and version, and the declared connections, ops, hosts and secrets. The broker and the proxy check
   the grant on every call. This is the same shape as delegation.v1, whose routine `grants` landed in
   flows phase 0. A global-account sensor runs as the global account.
5. The manifest is pinned with the script commit: connections and ops, hosts, secret bindings,
   interval and budget. A version that widens any capability is re-tested and re-approved by the
   owner, who is shown the capability diff.
6. Events are untrusted data. They go only to the owner's bus and fall under the leak rule.
7. Audit and kill switch: every call is logged with owner, sensor, version, op or host, bytes and
   result. Budgets come from `_global/POLICIES.md`. There is a global kill switch and a per-owner one.

**6.5a A sensor never acts. It may think, narrowly.** A third host call, `model.call(model, input,
schema)`, goes through the deployment's model gateway, on the same route config the agent uses
(LiteLLM / `models.llmBaseUrl`). Its rules:
1. The model is declared in the manifest, and the company must allow it for sensors in
   `_global/POLICIES.md` `sensor_models`. Typically that is a small open-weight reasoning model,
   never the agent's big model by default.
2. The call has no tools and takes no actions. Its output must match a declared JSON schema
   (classify, extract or score) and becomes event fields only.
3. The input is untrusted (mail, pages), so the output is untrusted data too. It can never widen
   what the sensor, or an agent connected to it, may do. This contains prompt injection.
4. Each sensor has a token budget in `_global`, separate from the agent-run quotas, and results are
   cached by input hash.
5. Every call is audited like the other host calls.
6. **Self-hosted by design.** `model.call` targets a route the deployment provides: either an
   optional chart component serving a small open-weight model (CPU-capable by default, GPU
   optional) or the operator's own route (for example a customer's LiteLLM). No sensor depends on
   a hosted third-party model.

Design only. It is built after the tier-1 basics. The ADR's earlier phrase "a sensor never thinks"
is replaced by "a sensor never acts".

**6.5b Design only: a meeting is a source, and agents own their sensor layer.**
1. *The live transcript processor is NOT a flow (founder, 2026-10-14).* It is an agent-managed
   working check (decision 6.0, point 3).
   * **What it is.** A script in the global repository, `_global/scripts/live-watch/`. It is a seed the
     global account owns, and people can fork it.
   * **Where it runs.** The agent runs it in its own container, against the meeting's real-time
     WebSocket (the gateway's `/ws`, the same stream the UI uses).
   * **What it does.** It processes segments with either a simple matcher (keywords, speaker, a
     question to the agent) or a cheap model through `model.call`. When a trigger fires, it wakes the
     agent's own chat through a session-scoped "wake this chat" call on agent-api. Flows is not in
     that loop. The call is audited and budgeted.
   * **How the agent starts it.** `request_meeting_bot` returns a pointer to the script and the exact
     invocation: the meeting id, the stream URL, and how to choose triggers. The agent can start it
     right away.
   * **Lifetime.** When the agent's run ends, the watcher ends. If it has to persist, promotion to a
     flows sensor applies.
   * **Latency and storms.** The target is a confirmed segment waking the agent within 5 s at p95.
     The watcher coalesces matches that arrive during a turn into one wake, and it has a per-meeting
     wake cap and a refractory period.
2. *Agents own their sensor layer.* An agent can create, tune, test and connect sensors for itself
   with the same user tools. It acts within its owner's grants and budgets and gets no extra power.
   A sensor an agent made is the owner's sensor, shown on the owner's strip.

**6.6 Placement: user code never runs in a process that holds credentials.** Scripts never run inside
flows-worker or flows-api, because both hold database credentials and keys.
* *Light tier, the default.* A separate `sensor-runner` Deployment runs a few long-lived pods. They
  hold no secrets and have no network except the egress proxy and the broker (NetworkPolicy, and the
  equivalent compose network). Flows schedules each tick and calls the runner. Each tick runs isolated
  inside the pod on WASM: wasmtime, with CPython-WASI and/or QuickJS for scripts. The host functions
  are `connection.request`, `http.fetch`, `state` and `emit`, and each tick has fuel, memory and time
  limits. This needs no privileges, so it runs under OpenShift `restricted-v2`. A pod per tick is
  rejected: a pod takes seconds to start, and thousands of ticks a minute would flood the API server.
  *Trade-off:* WASM limits scripts to pure-Python or pure-JS libraries, with no native extensions.
  Anything that needs native libraries goes to the heavy tier.
* *Heavy tier.* Long-lived or resource-hungry detection runs as a dedicated workload on the existing
  runtime, the scheduler bots and agent workers already use. Examples: a JS-heavy or signed-in page
  that needs a browser, a stream to listen to, a big diff, an ML model. Each workload declares
  requests and limits, has a per-owner concurrency cap and a `_global` budget, and needs admin
  approval above a size. The meeting bot is already a heavy sensor (meeting → `bot.*` and transcript
  events), and the vocabulary aligns with it.
* *Heavy processing after detection is not a sensor job.* The sensor emits an event, and a flow step
  or a job does the work.
* The secret rules of 6.5 hold on every tier.

**6.7 What phases A+B ship against this amendment.** Built:
* the sensor interface: `flows_sensors.SensorKind.tick(sensor, cursor) -> Tick(changes, cursor)`,
  the same state-in, events-out contract as 6.2;
* the pool: ensure, lease, tick, dedupe, checkpoint, degrade and health;
* triggers: glob, filter, `at`, and `relative` with keyed schedules;
* the light-run budget;
* the calendar sensor, as a declarative kind behind that interface, reading through the broker.

**Gaps, stated:**
* No WASM runner, egress proxy or per-tick capability grant yet.
* `sensor_test`, `sensor_connect` and the `sensor_*` CRUD tools are next. Until they exist, a sensor
  is created implicitly by an active trigger that needs it (decision 1.1).
* Starter scripts arrive with the runner, and calendar becomes the first of them.

### Worked example — a bot to every calendar meeting with a video link, 1 min before, following reschedules, skipping declined

The person says it in a chat; the agent calls `flows_submit` once:

```json
{"name": "bot-to-my-meetings",
 "trigger": {"kind": "relative",
             "event": {"type": "calendar.event.*",
                       "filter": "payload.video_url exists and payload.self_response != 'declined'"},
             "at": "payload.start - 60s",
             "key": "payload.event_id"},
 "steps": ["request_meeting_bot"],
 "params": {"meeting_url": "{{payload.video_url}}", "bot_name": "Vexa Notetaker"}}
```

1. Flows sees the trigger needs `calendar.event.*` about this person → ensures the calendar sensor
   for their connection (creates it, or reuses the existing one).
2. The sensor's incremental read (push-poked or polled) returns the changed events → one
   `calendar.event.created|updated|cancelled` each, deduped by `(connection, id, etag)`.
3. Admission matches the `relative` trigger: video + not declined → upsert `keyed_schedule` at
   `start − 60 s`; declined, link removed or cancelled → cancel the row.
4. At `fire_at` the worker writes one reaction; `request_meeting_bot` posts the bot.
5. The meeting moves → `updated` → the row moves. Already fired and moved later → re-armed once.

**Model calls per meeting: zero.** The one agent turn is the person's sentence that created the flow.

## Spike (bbb, 2026-10-14) — `core/flows/eval/sensors-spike/spike.py`

No test account with a Google Calendar connection was used; the spike runs a **fake provider** in its
own process that implements the Calendar incremental contract (`syncToken` → changed items +
`nextSyncToken`, 410 on a bad token), a sensor pool (timer heap + shared threads), the trigger
matcher and the keyed-schedule firer, on Postgres 17 (throwaway container, tmpfs).

* **Correctness (`scenario`, 1 s poll, 5 s lead): PASS.** Four events: video+accepted, no video,
  declined, video+accepted. Moved the first by +10 s, cancelled the last. Result: exactly one
  reaction, for the moved event, fired at the moved `start − lead` (0.23 s after due); the cancelled
  one's row `cancelled` at version 2; no rows for no-video or declined.
* **Cost (`load`, steady state after every sensor's initial full sync):**

| sensors | interval | threads | ticks/s | CPU per tick (sensor thread / whole process) | wall per tick | DB statements per tick | schedule lag p50 / p99 |
|---|---|---|---|---|---|---|---|
| 2 000 | 15 s | 8 | 133 | 0.61 / 0.91 ms | 1.3 ms | 0.18 | 1.2 / 4.3 ms |
| 10 000 | 60 s | 16 | 167 | 0.55 / 1.03 ms | 1.5 ms | 0.13 | 1.3 / 6.4 ms |

  10 000 connections polled every minute ≈ **0.17 CPU core** of Python and ~22 DB writes/s
  (only ticks that found a change write). A no-change tick writes nothing.
* **What the spike does not show:** real provider round-trips (100–300 ms with TLS) — the pool must
  be async or wider, which changes wall time, not CPU; provider quota (the binding constraint, hence
  push); the first-run spike measured 410 ms/tick with one commit per statement on bbb's saturated
  disk, fixed by one transaction per changed tick — production needs the same.

## Design-fit gate (roadmap-post-0133)

| # | check | verdict |
|---|---|---|
| 1 | One edge / MCP first | **yes** — every capability is a flows tool; the only new UI is the strip drawing and a teaching chip. Provider webhooks enter through the gateway. |
| 2 | One writer, one source of truth | **yes, with a migration** — broker owns tokens and provider calls, flows-sensors owns cursors, flows owns time. Holds only once meeting-api's ICS sync and auto-join sweep move into flows (otherwise two clocks). |
| 3 | Policy decides usage | **yes** — budgets and intervals become `_global/POLICIES.md` keys with conservative defaults; this moves today's env budgets there. |
| 4 | Leak rule, least privilege | **yes** — sensors read only the owner's connection; events are about the owner; user payloads are untrusted data; non-LLM steps act only as the owner and within `user_flow_steps`; event agents stay proposal-only. |
| 5 | Flag + proof | **yes** — flags `flow_triggers_v2`, `flow_sensors`, `user_events_ingest`, `flow_scripts`; off until the worked example runs on app.dev against a real calendar. |
| 6 | Cost and limits | **yes** — zero model calls per sensed change; spike ≈ 1 ms CPU per tick; separate light-run budget; quota handled by push and a central bucket. |
| 7 | Explicit UI state | **needs feature review** — the strip must show the sensor (push/poll/degraded) and the next fire times before ship; designed here, not yet drawn. |
| 8 | Lifecycle / home model | **yes** — discovered at connection time (teaching chip) and from the chat; serves the "Vexa attends my meetings" stage. |

## Plan — build phases

| phase | content | size |
|---|---|---|
| **A** triggers | `event` filter, `at`, `relative` + `keyed_schedule`, 1 s pass for sub-minute non-LLM cron, light-run budget, POLICIES keys, user-allowed `request_meeting_bot` / `post_message` / `emit_event` / `stop_bot`, `flows_preview`, `request_meeting_bot(start_at)`, cycle check | M (~1.5 wk) |
| **B** calendar sensor | flows-sensors process (shard leases + heap), broker `calendar.changes`, `calendar.event.*` carriers, worked example on app.dev with a real Google account; invite intake follows reschedules | M (~1.5 wk) |
| **C** push | gateway `/hooks/google`, `events.watch` + renewal, Gmail `users.watch` (Pub/Sub topic) + `mail.received`, safety-poll fallback, `sensors_status` | S–M (~1 wk) |
| **D** probe + ingest | `probe` sensor with guarded fetcher, `POST /v1/events` + ingest tokens, kill-switch split | S–M (~1 wk) |
| **E** Microsoft | broker Microsoft OAuth (calendar + mail), Graph delta + subscriptions; Teams meeting creation is a separate item | M (~1.5 wk) |
| **F** one clock | ICS sync as a sensor, auto-join sweep retired, meeting-api one-shot compiler removed | S |
| **G** scripts | sandboxed pool for workspace scripts (isolate technology spike first) | L (~3 wk) |

## Migration note — the four live routines on app.dev (2026-10-14), written, not applied

Each is one `agent_run` on a cron today. Rewritten against this ADR:

| routine (owner) | today | rewritten | model calls |
|---|---|---|---|
| `check-email` (user 36, `*/15 * * * *`) | an agent run every 15 min to look for new mail, then summarise | **phase C:** a `gmail` sensor of the person's own (`sensor_create kind=gmail filter="not payload.from contains 'noreply'"`), tested, enabled, `sensor_connect`ed to the routine's chat with `wake: batch:15` and the summarising instruction. Cards land per message with no model call; the agent wakes only when something new arrived, at most every 15 min. Until phase C there is no Gmail sensor, so this one stays as it is. | 96/day → only on new mail, batched |
| `unanswered-inbox-sweep` (user 2, `*/30 6-21 * * *`) | an agent run every 30 min, 06:00–21:30, over Gmail and GitHub | **phase C + D:** a `gmail` sensor plus a `probe` (or user script) on GitHub notifications, both connected to the sweep's chat with `wake: batch:30`. The sweep runs only when either reported something. The page rebuild stays an agent turn (it writes a workspace page). | 32/day → only on new items |
| `nightly-chat-digest` (user 2, `0 23 * * *`) | a daily agent run | **unchanged**: a time-of-day summary of a day's chats is the agent's job, and a cron is the right trigger. Only the honest outcome of phase 0 changes it (refused acts end `failed` and say so in the chat). | 1/day |
| `daily-date` (user 31, `0 22 * * *`) | a daily agent run that posts the date | **a light flow:** `steps: ["post_message"]` with `params.text: "{{date}}"` on the same cron — no agent. Needs the `post_message` light step (phase A follow-up; not built today). Until then it stays an agent run. | 1/day → 0 |

Rule of thumb: when a routine's job is noticing, use a sensor. When its job is thinking about what was
noticed, keep it an agent run woken by the sensor. When its job is a fixed act at a time, use a light
step on a cron or `at`.

## Alternatives

* **Container per script / per sensor.** Simple isolation; thousands of idle containers for work that
  is one HTTP read a minute. Rejected for sensors; for scripts the isolation boundary is the pool.
* **Let an agent poll (status quo).** One model run per tick; capped at 48/day per person. Rejected.
* **A message broker (NATS/Kafka) as the bus.** Postgres already carries events and reactions
  transactionally at this volume (~170 ticks/s, a few dozen writes/s); a second store would be a
  second writer of event state. Revisit past ~10× this load.
* **Per-subscriber polling.** Each flow polls its own copy: N× the quota for the same data. Rejected —
  one sensor per connection.
* **meeting-api scheduled bots as the clock.** Keeps two clocks; contradicts ADR-0046 decision 3.

## Consequences

* Flows grows a fourth process and two tables; the broker grows read-only change operations.
* `event_agents_pause` changes meaning (sinks no longer paused by it).
* Self-hosted installs without public HTTPS get polling only; documented as such.
* `mail_settings` stays admin-only; a person learns Vexa's invite address from a non-admin read
  (`whats_waiting` / a profile field) — separate small item from the 10:39 friction.

## Open questions

1. Gmail push needs a Google Cloud Pub/Sub topic per deployment — acceptable for hosted, optional for self-hosted?
2. Isolate technology for phase G (WASM vs forked sandbox) and which languages a script may be written in.
3. Should `mail.received` carry the subject at all for shared-chat sinks, or only ids (leak rule)?
