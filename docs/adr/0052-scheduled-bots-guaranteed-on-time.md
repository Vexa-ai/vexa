# ADR 0052 — Scheduled bots are guaranteed on time: the pod starts 10 minutes early, Flows owns the one clock

**Status:** proposed · 2026-11-01 · [#1962](https://github.com/Vexa-ai/vexa/issues/1962) (replaces its
31 Oct ramp-limit design) · builds on ADR-0046 (flows own time) and ADR-0050 (keyed schedules) ·
founder rulings of 2026-11-01 are quoted where they settle a choice; what they do not settle is listed
under *Open questions*, not decided here

## Context

Booking a bot ahead already exists in the public API (since 0.12.28): `POST /meetings` with
`scheduled_at` and `meeting_url` creates a planned row (`auto_join` defaults to true), and `PATCH` /
`DELETE` work while it is planned. What is missing is a guarantee. Today a booked bot is spawned by
meeting-api's 30 s auto-join sweep 120 s before the start (`bot_spawn/auto_join.py:65`), so it competes
for a pod and a node in the same minute as every ad-hoc bot sent at :58. A cold node takes about
2 min to become Ready plus about 90 s of image pull (measured once on prod, Sep 2026), so a booked bot
can arrive several minutes late.

There are also two clocks with two leads. meeting-api's sweep fires at −120 s; the Flows `invite_join`
seed fires at −60 s (`flows_defs/production.py:3306-3309`); the docs say "~60 s". ADR-0046 says flows
own time.

Checked in the tree at `codex/v0.13.4` @ `55201db2`:

| Question | Finding |
|---|---|
| Bot join time | None. The bot opens the meeting as soon as it boots (`bot/src/index.ts`, `contracts/invocation.v1`). |
| How a booked bot starts | meeting-api `_auto_join_loop` (`meeting_api/__main__.py:761`) → `auto_join_tick` → `request_bot`, which claims the **newest** planned row for (user, platform, native id) (`bot_spawn/adapters.py:600-607`). |
| What the sweep enforces | Operator and owner block lists (`VEXA_AUTO_JOIN_BLOCK`, `auto_join_block`), the per-calendar bot name, `auto_join_error` + `auto_join_next_retry`, the `auto_join_last_attempt` anti-storm stamp that calendar sync copies to sibling rows (`auto_join.py:1-60, 154-166, 257-404`). None of this runs on `POST /bots`. |
| How Flows starts a bot | `dispatch_bot` posts `POST /bots` **by URL** (`flows_steps/meeting.py:440-485`). |
| Does Flows hear about planned rows | No. meeting-api publishes only `meeting.started/completed/shared`, best-effort, failures swallowed (`meeting_api/events.py:12-18, 110-118`). Nothing on create/move/cancel, nothing from calendar sync. |
| Keyed «missed» | `now − max(fire_at, updated_at) > 600 s` (`flows/keyed.py:32, 146`); an unchanged re-upsert bumps `updated_at` (`:84-93`). |
| Keyed head-of-line | `fire_due` reads `LIMIT 200 ORDER BY fire_at` and `continue`s past rows of retired flows and rows refused by the daily cap without changing them (`keyed.py:135-150`), so 200 such rows stop every later fire. |
| Keyed ceilings | 500 armed rows per person per flow, 20,000 per company flow; above that `upsert` returns `refused` with a log line only (`keyed.py:33-36, 66-74`). |
| Booking cap | None on `POST /meetings` (`collector/app.py:564-634`). |
| Seeds | `invite_join` is written only if absent and never overwritten (`production.py:3300-3340`). |
| Flows in each profile | compose: on (`flows-api`). lite: absent (`deploy/lite/supervisord.conf`). Helm: `flows.enabled: false` (`values.yaml:1364`). bank profile: absent. |
| Account ceiling | `count_active_bots` counts `requested, joining, awaiting_admission, active` (`adapters.py:414-425`). |
| Billing today | vexa-platform `meeting-billing-rating.ts`: bot minutes = `ceil((departed − admitted)/60)`; never admitted = not billed; transcription minutes = bot minutes when Vexa transcribed. meeting-api provenance sets `departed_at = None` for never-admitted runs (`lifecycle/provenance.py:60-64`), contract `LIFECYCLE_CONTRACT_VERSION = "2026-07-28"` (`:7`). |
| Credit checks | The service-authority boundaries start at admission and cover `active`/`needs_help` rows only (`service_authority/sweep.py:97-115`). |
| Outcome grade on the row | None. The five-class bot-outcome grade (ours, host_or_platform, user, normal, unknown) exists only as an operator script. |
| Cluster (hosted) | Bots are priority 0 on `default-scheduler`. A `capacity-reserve` Deployment of `pause` pods at priority −1, one per node, sized by a 5-min CronJob to 30 % of main-pool nodes (min 2, max 10). LKE's managed autoscaler; its expendable-priority cutoff cannot be set. A `g6-standard-4` holds 3 bots (memory-bound: 3 × 2560 Mi). The runtime cannot set `schedulerName` on bot pods (`runtime_kernel/pod_scheduling.py:35-39`). |

## Decision

### 1. The promise

Public wording, verbatim (founder, 2026-11-01):

> A bot booked 10 or more minutes ahead, within your concurrency limit, is in the meeting when it starts.

No money-back credit, no SLA document.

### 2. The timeline of a booked meeting

Two global settings, one value each, owned by meeting-api config (`config.v1.json`), read by nobody
else (Flows receives the computed times in the fact, §4):

| Setting | Default | Meaning |
|---|---|---|
| `BOOKED_POD_LEAD_S` | 600 | the bot's pod starts at `start − 600 s` |
| `BOOKED_JOIN_LEAD_S` | 120 | the bot opens the meeting at `start − 120 s` |
| `BOOKED_GRACE_S` | 600 | the last moment a bot may still be started is `start + 600 s` (today's `AUTO_JOIN_GRACE_S`) |

For every planned row with `auto_join` on, API-booked and calendar-imported alike (founder ruling 2):

- `pod_at = start − BOOKED_POD_LEAD_S`, `join_at = start − BOOKED_JOIN_LEAD_S`, `deadline = start + BOOKED_GRACE_S`.
- At `pod_at` Flows asks meeting-api to start the bot for that row id (§5). The pod starts, boots the
  browser and waits; it opens the meeting at `join_at`.
- Booked less than 10 min ahead: `pod_at` is already past, the pod starts at once and waits.
  Booked after `join_at`: the pod starts and joins at once. Booked after `deadline`: no bot (as today).
- The waiting pod counts against the account's concurrency ceiling. At the ceiling, the start is
  refused with the existing `MaxBotsExceeded`; Flows retries until a slot frees or `deadline` passes.
  There is no booked/ad-hoc priority and no fair-share: arrival order decides.

### 3. One clock: Flows required in every profile (founder ruling 1)

- meeting-api's `_auto_join_loop` and `auto_join_tick` are removed. `AUTO_JOIN_SWEEP_INTERVAL_S`,
  `AUTO_JOIN_LEAD_S`, `AUTO_JOIN_GRACE_S`, `AUTO_JOIN_RETRY_BACKOFF_S`, `AUTO_JOIN_ALLOW_UNCAPPED`
  go into `meeting_api/removed_config.py` with their replacements named.
- Flows becomes part of every profile: added to lite as supervisord programs, `flows.enabled: true`
  by default in Helm, added to the bank profile. compose already runs it.
- `meeting_api/events.py`'s statement that a deployment without a flows domain runs meetings the same
  is no longer true for planned meetings; it is rewritten to say so.

- Rows booked before the upgrade are not lost: the release that removes the sweep first writes one
  outbox fact (§4) for every future `scheduled` row with `auto_join` on (a one-time backfill at
  meeting-api boot, idempotent by revision), so Flows arms them before the sweep is gone.

### 4. Planned rows reach Flows durably: a meeting-api outbox

- Every write that creates, moves, cancels, deletes or toggles `auto_join` on a planned row
  (`POST/PATCH/DELETE /meetings`, calendar sync, the terminal) inserts a `meeting.planned.changed`
  fact into a meeting-api outbox table **in the same transaction** as the row change.
- The outbox reuses the CRM's pattern (`core/crm/src/crm/outbox.py`): bounded, retryable delivery to
  Flows `POST /events` with a publish key; a refused row stays pending and is retried.
- The fact carries `meeting_id`, `user_id`, `status`, `auto_join`, `pod_at`, `join_at`, `deadline`,
  and a monotonic `revision`. It carries no URL and no passcode.
- Delivery is at least once, in revision order per meeting. The keyed row is keyed by `meeting_id`
  and an older revision never overwrites a newer one.
- The existing best-effort `meeting.started/completed/shared` publishes are not changed by this ADR.

### 5. The bot is started for the planned row id, never by URL

New internal route on meeting-api, service door only (Flows' key):
`POST /internal/meetings/{meeting_id}/start`.

Under the per-user advisory lock and a row lock it:

1. Refuses with 409 `not_scheduled` unless the row is still `scheduled` and `auto_join` is not off.
   A cancel (`DELETE /meetings`) or a toggle that lands first therefore always wins, whatever Flows
   still has armed. This also fixes the recurring-link claim of the wrong (newest) row.
2. Applies the operator block list (`VEXA_AUTO_JOIN_BLOCK`, name unchanged) and the owner's
   `auto_join_block` (`bot_spawn/auto_join_block.py` moves under the route unchanged).
3. Honours `auto_join_last_attempt` (calendar sync keeps copying it to sibling rows), resolves the
   per-calendar-source bot name, and resolves recording/transcription through
   `env_flags.resolve_spawn_flag` exactly as the sweep did.
4. Refuses if another row already owns the room (`LIVE_STATUSES`), stamping the holder's id.
5. Checks credit, then spawns through `request_bot`, claiming **this** row id, with `join_at` in the
   invocation.
6. On any refusal or spawn failure stamps `data.auto_join_error` and `data.auto_join_next_retry` on
   the row (the terminal shows them as today) and returns the reason; 429 carries `Retry-After`.

`auto_join.py`'s safeguards move into this route's module; nothing is reimplemented in Flows.

### 6. The Flows side: flows registered in code, keyed by meeting id

- A production flow `booked_bot`, registered in `flows_defs/production.py` (not a seed, so it changes
  with the code), consumes `meeting.planned.changed`: arms the keyed row at `pod_at` with the row's
  `deadline`; disarms it on cancel, delete, `auto_join` off, or a status other than `scheduled`.
- Its fire calls the route in §5. 409 `not_scheduled` → done, nothing to do. 429 / 503 → `Wait` for
  `Retry-After` (default 15 s) and call again, until `deadline`. Past `deadline` → done, with the
  row's stamped error as the reason.
- The flow is instance-scoped. Its fires are not subject to a person's daily run cap
  (`VEXA_FLOWS_USER_RUNS_PER_DAY`); the account ceiling is the bound.
- `invite_join` stops spawning bots and arming its own alarm. It creates the planned row with
  `POST /meetings` and moves it with `PATCH` when the invite is rescheduled, so invites run through
  the same clock. It is registered in code like `booked_bot`, no longer a seed; a one-off migration
  in the same release removes the stored seed copy (logged when the stored copy had been edited).

### 7. Keyed-schedule fixes in Flows (needed before anything is guaranteed on top of it)

- **«Missed» is judged from the meeting, not the alarm.** `keyed_schedule` gets a nullable
  `deadline` column. A row with a deadline is missed only when `now > deadline`; rows without one
  keep today's rule. An unchanged re-upsert no longer touches `updated_at`. A Flows outage from
  `pod_at` to `start` therefore still starts the bot when Flows returns, up to `start + 10 min`.
- **No head-of-line block.** `fire_due` selects only rows whose `(scope, flow)` is loaded, and keeps
  reading in pages of 200 until no due row is left or a time budget is spent. Retiring a flow marks
  its armed rows `retired`. A row refused by `admissible` is skipped without stopping the page.
- **Ceilings count per flow.** `held()` counts a person's armed rows of one flow only, so rows of
  other flows never use up `booked_bot`'s share. For `booked_bot` the share is per account and the
  20,000 company-wide total does not apply.

### 8. Abuse bounds, cross-account included

- **One booking cap, enforced where every booked row is created.** The repository function that
  creates a planned row is the one path for `POST /meetings`, calendar sync and `invite_join`. In the
  same transaction it validates `scheduled_at` as ISO-8601 (today any string passes) and counts the
  account's future `scheduled` rows with `auto_join` on against `BOOKED_MAX_PER_ACCOUNT` (value
  under *Open questions*). Over the cap: `POST /meetings` answers 429 with the cap in the body;
  calendar sync still mirrors the event but creates the row with no alarm and `auto_join_error`
  stamped "booking cap reached".
- One account's bookings can no longer fill another's: the keyed share for `booked_bot` is per
  account (§7).
- A `refused` upsert is no longer a log line: Flows answers the outbox delivery with `refused`, and
  meeting-api stamps `data.auto_join_error` on the row, visible to the owner.
- **What the ceiling counts** (decided by the CEO, 2026-11-01): `count_active_bots` also counts
  `needs_help` and `needs_human_help` pods; `stopping` is not counted.
- Within one account, fake bookings are bounded by the ceiling (waiting pods count) and paid for
  (§10). `join_at` is never exposed on public `POST /bots`; the internal route sets it and refuses a
  `join_at` later than `now + BOOKED_POD_LEAD_S + 60 s`, so a pod cannot sit on its MeetingToken
  (5 h TTL, `bot_spawn/service.py:936`) longer than 11 min before joining.

### 9. The bot waits until `join_at` (s1)

- `joinAt` (UTC ISO) is added to `invocation.v1` and read in `bot/src/index.ts`. Before it, the bot
  boots the browser and its capture pipeline but opens no meeting page; the first `joining` emit
  and the reachability gate (`index.ts:423-426`) wait until it.
- While waiting the row stays `requested`. No new public status or webhook value; the ceiling
  already counts `requested`. The row's `data.join_at` shows when it will join.
- The reconciler's pre-active reap grace (`lifecycle/reconcile.py:170-181`) is measured from
  `join_at` when present, so a waiting pod is not reaped as stuck.

### 10. Billing from pod start (founder rulings 2 and 3)

Ruling: every bot is billed from pod start until it leaves, admitted or not; booked bots pay for the
wait; the only runs never billed are those that ended through our fault. Every other class is
billed, `unknown` included.

- **Pod-start fact.** The bot emits a producer-timestamped `pod_started_at` at process start (before
  the `joinAt` wait). Not `created_at`: on a booked row that is the booking time.
- **The product owns the bot-outcome grading.** The rule table (five classes: ours,
  host_or_platform, user, normal, unknown) lives in meeting-api; meeting-api writes `outcome_class` /
  `outcome_reason` at the terminal transition. Operator tooling reads this table instead of keeping
  its own copy, so there is one source of truth.
- **Provenance.** meeting-api provenance gains `pod_started_at`, `outcome_class`, `outcome_reason`,
  and keeps `departed_at` for never-admitted runs. `LIFECYCLE_CONTRACT_VERSION` moves to
  `2026-11-01`.
- **Dual acceptance.** vexa-platform's rating accepts both versions **before** meeting-api ships the
  new one (today an unknown version rates as `reconciliation_required`, unbilled). For `2026-07-28`
  rows it rates as today; for `2026-11-01` rows: bot minutes = `ceil((departed_at − pod_started_at)/60)`,
  not billed when `outcome_class = ours`.
- **Transcription minutes are split.** They stay `admitted → departed`, and only when Vexa
  transcribed. Wait and lobby minutes are bot minutes only.
- **Credit checks cover waiting and lobby bots.** The service-authority boundaries start at
  `pod_started_at` and the sweep covers `requested`, `joining` and `awaiting_admission` rows. The
  start route (§5) checks credit before spawning.

### 11. Hosted cluster (s3, vexa-platform)

- s3 **replaces** the `capacity-reserve` Deployment and the `capacity-resize` CronJob; it does not
  add a second warm-pool writer. One controller owns: the placeholder count, the cordon marks.
- All bots have one priority (an explicit PriorityClass at 0, wired through the existing
  `RUNTIME_K8S_BOT_PRIORITY_CLASS_NAME`, unset on prod today).
- **Placeholders stay at priority −1** (decided by the CEO, 2026-11-01). A bot preempts one, and the
  preempted placeholder going Pending is what asks LKE's autoscaler for a node; a placeholder-only
  node still scales down. Nothing of ours resizes the pool: LKE's autoscaler stays its only writer.
  (The settled design's "autoscaler ignores placeholders" is not possible on LKE, whose expendable
  cutoff cannot be set.)
- Packing: a second scheduler, run as our own Deployment with `NodeResourcesFit: MostAllocated`
  (LKE's managed scheduler is not configurable). Bots **and** placeholders use it, so two schedulers
  never race for one node. vexa change: the runtime gains `RUNTIME_K8S_BOT_SCHEDULER_NAME`
  (`pod_scheduling.py`, `config.v1.json`, Helm `runtime.<bot>.schedulerName`).
- The controller keeps a warm pool sized by real bots, and marks the emptiest nodes "no new pods"
  counting real bots only (placeholders do not count; never on a node already draining). A marked
  node goes when its last meeting ends. No live bot is ever moved (bot pods are bare pods and the
  autoscaler does not drain nodes holding them).
- The guarantee is also bounded by the pool max (20 on `main` today): the controller alarms when a
  bot is Pending because the pool is at max.

### 12. What was dropped (founder, 2026-11-01)

The 31 Oct ramp curve; fair-share admission for ad-hoc bots; separate booked/ad-hoc priorities;
`ends_at`; a reservation price; a paid-only guarantee.

## Slices and order

| Slice | Repo | Changes | Tests |
|---|---|---|---|
| **s1 bot join time** | vexa | `contracts/invocation.v1` (+`joinAt`); `bot/src/index.ts` wait before first `joining` and the reachability gate; `bot_spawn/service.py` passes `join_at` and refuses one past `now + pod lead + 60 s`; `lifecycle/reconcile.py` grace from `join_at` | bot unit: no navigation before `joinAt`, immediate join when past; invocation schema fixture; reconciler does not reap a waiting `requested` row; spawn refuses a far `join_at` |
| **s2a keyed fixes** | vexa (flows) | `keyed.py`: `deadline` column and missed-from-deadline, re-upsert leaves `updated_at`, paging past skipped rows, retired rows marked, `held()` per flow, per-account share without the company total for `booked_bot`, `refused` returned to the caller | Flows down from fire time to deadline → still fires; unchanged re-upsert does not extend the window; 201 retired-flow rows do not stop a due fire; rows of one flow do not use another flow's share; 41 accounts at their share do not refuse a 42nd |
| **s2b Flows in every profile** | vexa (deploy) | lite supervisord programs for Flows; Helm `flows.enabled: true`; bank profile; `meeting_api/events.py` wording | lite boots with Flows; Helm default renders Flows; bank profile renders Flows |
| **s2c outbox + start route** (sweep still on) | vexa (meeting-api, gateway) | outbox table + drain on the CRM outbox pattern; `POST /internal/meetings/{id}/start` with the sweep's safeguards; planned-row creation path with ISO check and booking cap (API and calendar sync); ceiling counts `needs_help` / `needs_human_help`; gateway service-door allow-list | cancel-before-fire and cancel-during-fire → 409, no bot; recurring link starts the right row; block lists, bot name, last-attempt stamp applied; outbox survives a Flows outage and delivers in revision order; cap refuses `POST /meetings` with 429 and calendar sync rows over the cap get no alarm |
| **s2d switch the clock** | vexa (meeting-api, flows) | `booked_bot` flow; one-time backfill of facts for future `scheduled` rows; `_auto_join_loop` / `auto_join_tick` removed, keys to `removed_config.py`; `invite_join` registered in code, rewritten to `POST/PATCH /meetings`, stored seed removed by a one-off migration | a row booked before the upgrade is armed by the backfill and gets its bot; booked < 10 min and after `join_at`; ceiling full → starts when a slot frees; rescheduled invite moves the bot; no meeting-api clock left (grep gate) |
| **s3 cluster** | vexa-platform (+ vexa runtime knob) | controller replacing `deployment-capacity-reserve.yaml` + `cronjob-capacity-resize.yaml`; second scheduler Deployment, RBAC, config; bot PriorityClass at 0; `RUNTIME_K8S_BOT_SCHEDULER_NAME` in vexa runtime and chart | render tests for the chart; controller unit tests (placeholders not counted, draining nodes skipped, no bot moved); runtime test that the scheduler name reaches the pod spec; staging rehearsal: burst at :58 with booked pods started at :50 |
| **s5 billing** | vexa + vexa-platform | platform rating dual acceptance, pod-start minutes, `ours` not billed, transcription split, account page and `/pricing` text, `bot_never_joined` reason retired for v2 rows; bot emits `pod_started_at`; meeting-api grading table + `outcome_class` at terminal transition + provenance fields + version bump; service-authority boundaries from pod start | rating fixtures for both versions; never-admitted v2 run billed from pod start; `ours` run not billed, `unknown` billed; transcription minutes unchanged by wait; after s1 + s2c: a waiting bot's minutes counted from pod start, and a zero-balance account's waiting bot is stopped |
| **s4 docs** | vexa (`docs/docs/`) | `api/meetings.mdx` (auto-join section, `scheduled_at`, booking cap, PATCH 409 from start − 10 min, 429 counts waiting bots, the promise verbatim); `core/meetings.mdx:76-87`; `how-to/calendar-sync.mdx:102-103`; `configuration.mdx:314-332`; `deployment-kubernetes.mdx:366, 690-694` and lite/Helm pages (Flows required); `concepts.mdx:36`; troubleshooting "a scheduled meeting didn't auto-join"; ADR-0050 lead values; `quickstart.mdx:19` bot time defined from pod start; changelog entry (earlier pod start, wait and lobby billing, PATCH window) | `docs-current` gate; link check; the "~60 s" claim gone everywhere |

Order:
- After this design is signed, s1, s2a, s2b, s3 and s5 can start (s5's tests that need waiting bots
  run after s1 and s2c).
- s2c after s1. s2d after s2a, s2b and s2c.
- s4 after s2d, s3 and s5.
- Within s5 the platform's dual acceptance lands on prod before meeting-api's version bump.
- Release prerequisites: every slice above, and the Individual plan ceiling of 2 bots
  ([vexa-platform#564](https://github.com/Vexa-ai/vexa-platform/pull/564)).

## Consequences

- One clock, one lead per meaning, one path from a planned row to a bot; a cancel cannot be beaten by
  an alarm.
- Self-hosted lite and default Helm installs run Flows from now on (more processes and memory on lite).
- Booked bots occupy a ceiling slot and are billed for up to 10 min before the start; never-admitted
  bots are billed for their lobby wait. Both are customer-visible price changes.
- `PATCH /meetings` is refused from `start − 10 min` (was `− 2 min`).
- The guarantee holds only within the account ceiling and the cluster pool max.

## Open questions for the founder

1. **Booking cap value.** `BOOKED_MAX_PER_ACCOUNT`: proposed 500 future booked rows per account,
   with no horizon on how far ahead.
2. **Pool max versus the promise.** The guarantee fails for everyone when the pool is at max. Whether
   the pool max is raised (an open vexa-platform PR proposes 50) and what spend that allows is a money
   decision.
3. **Notice before release.** Wait and lobby billing changes what customers pay. Does the release wait
   until a notice has gone to customers? No one has ruled on this commitment yet.
