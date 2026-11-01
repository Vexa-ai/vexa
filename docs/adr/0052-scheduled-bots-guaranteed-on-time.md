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

### 4. Planned rows reach Flows durably: a meeting-api outbox

- Every write that creates, moves, cancels, deletes or toggles `auto_join` on a planned row
  (`POST/PATCH/DELETE /meetings`, calendar sync, the terminal) inserts a `meeting.planned.changed`
  fact into a meeting-api outbox table **in the same transaction** as the row change.
- The fact carries `meeting_id`, `user_id`, `status`, `auto_join`, `pod_at`, `join_at`, `deadline`,
  and a monotonic `revision`. It carries no URL and no passcode.
- A drain loop delivers facts to Flows `POST /events` at least once, in revision order per meeting,
  with backoff. Delivery is idempotent: the keyed row is keyed by `meeting_id` and an older revision
  never overwrites a newer one.
- The existing best-effort `meeting.started/completed/shared` publishes are not changed by this ADR.

### 5. The bot is started for the planned row id, never by URL

New internal route on meeting-api, service door only (Flows' key):
`POST /internal/meetings/{meeting_id}/start`.

Under the per-user advisory lock and a row lock it:

1. Refuses with 409 `not_scheduled` unless the row is still `scheduled` and `auto_join` is not off.
   A cancel (`DELETE /meetings`) or a toggle that lands first therefore always wins, whatever Flows
   still has armed. This also fixes the recurring-link claim of the wrong (newest) row.
2. Applies the operator block list (`VEXA_AUTO_JOIN_BLOCK`, renamed `VEXA_BOOKED_BLOCK`) and the
   owner's `auto_join_block` (`bot_spawn/auto_join_block.py` moves under the route unchanged).
3. Honours `auto_join_last_attempt` (calendar sync keeps copying it to sibling rows), resolves the
   per-calendar-source bot name, and resolves recording/transcription through
   `env_flags.resolve_spawn_flag` exactly as the sweep did.
4. Refuses if another row already owns the room (`LIVE_STATUSES`), stamping the holder's id.
5. Spawns through `request_bot`, claiming **this** row id, with `join_at` in the invocation.
6. On any refusal or spawn failure stamps `data.auto_join_error` and `data.auto_join_next_retry` on
   the row (the terminal shows them as today) and returns the reason; 429 carries `Retry-After`.

`auto_join.py`'s safeguards move into this route's module; nothing is reimplemented in Flows.

### 6. The Flows side: one registered flow, keyed by meeting id

- A production flow `booked_bot` (registered in `flows_defs/production.py`, **not** a seed, so it
  changes with the code) consumes `meeting.planned.changed`: arms the keyed row at `pod_at` with the
  `deadline` stored on the row; disarms it on cancel, delete, `auto_join` off, or a status other than
  `scheduled`.
- Its fire calls the route in §5. 409 `not_scheduled` → done, nothing to do. 429 / 503 → `Wait` for
  `Retry-After` (default 15 s) and call again, until `deadline`. Past `deadline` → done, with the
  row's stamped error as the reason.
- The flow is instance-scoped. Its fires are not subject to a person's daily run cap
  (`VEXA_FLOWS_USER_RUNS_PER_DAY`); the account ceiling is the bound.
- `invite_join` stops spawning bots. It creates the planned row with `POST /meetings` and moves it
  with `PATCH` when the invite is rescheduled, so invites run through the same clock. The seed's
  new version reaches existing installs by the seed migration in §7.

### 7. Keyed-schedule fixes in Flows (needed before anything is guaranteed on top of it)

- **«Missed» is judged from the meeting, not the alarm.** `keyed_schedule` gets a nullable
  `deadline` column. A row with a deadline is missed only when `now > deadline`; rows without one
  keep today's rule. An unchanged re-upsert no longer touches `updated_at`. A Flows outage from
  `pod_at` to `start` therefore still starts the bot when Flows returns, up to `start + 10 min`.
- **No head-of-line block.** `fire_due` selects only rows whose `(scope, flow)` is loaded, and keeps
  reading in pages of 200 until no due row is left or a time budget is spent. Retiring a flow marks
  its armed rows `retired`. A row refused by `admissible` is skipped without stopping the page.
- **Seed changes reach existing installs.** Each seed carries a `version`. On boot a stored seed
  with an older version is replaced when its stored body equals the shipped body of that older
  version (the image keeps every shipped version); an operator-edited copy is left alone and listed
  as "update available" on the flows admin page and in the boot log.

### 8. Abuse bounds, cross-account included

- `POST /meetings` validates `scheduled_at` as ISO-8601 (today any string passes) and caps future
  planned rows with `auto_join` on per account (`BOOKED_MAX_PER_ACCOUNT`, value under *Open
  questions*). Above it: 429 with the cap in the body, synchronously, before anything reaches Flows.
- The keyed ceiling for `booked_bot` is per account only (the person's share, 500); the 20,000
  company-wide total does not apply to it. One account's bookings can no longer fill everyone's.
- A `refused` upsert is no longer a log line: Flows answers the outbox delivery with `refused`, and
  meeting-api stamps `data.schedule_error` on the row, visible to the owner.
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
wait; the only runs never billed are those that ended through our fault, as graded by the bot-outcome
classification.

- **Pod-start fact.** The bot emits a producer-timestamped `pod_started_at` at process start (before
  the `joinAt` wait). Not `created_at`: on a booked row that is the booking time.
- **Provenance.** meeting-api provenance gains `pod_started_at`, `outcome_class`, `outcome_reason`,
  and keeps `departed_at` for never-admitted runs. `LIFECYCLE_CONTRACT_VERSION` moves to
  `2026-11-01`.
- **Outcome grade in the product.** meeting-api writes `outcome_class` / `outcome_reason` at the
  terminal transition, using the same rule table as the operator's grading script. A shared fixture
  of rows → expected class pins the two to one table.
- **Dual acceptance.** vexa-platform's rating accepts both versions **before** meeting-api ships the
  new one (today an unknown version rates as `reconciliation_required`, unbilled). For `2026-07-28`
  rows it rates as today; for `2026-11-01` rows: bot minutes = `ceil((departed_at − pod_started_at)/60)`,
  not billed when `outcome_class = ours`.
- **Transcription minutes are split.** They stay `admitted → departed`, and only when Vexa
  transcribed. Wait and lobby minutes are bot minutes only.
- **Credit checks cover waiting and lobby bots.** The service-authority boundaries start at
  `pod_started_at` and the sweep covers `requested`, `joining` and `awaiting_admission` rows. The
  start route (§5) checks credit before spawning.
- **Notice before release.** Lobby and wait billing is a change to what customers pay. The release
  ships only after the lobby-billing notice has gone to customers.

### 11. Hosted cluster (s3, vexa-platform)

- s3 **replaces** the `capacity-reserve` Deployment and the `capacity-resize` CronJob; it does not
  add a second warm-pool writer. One controller owns: the placeholder count, the cordon marks.
- All bots have one priority (an explicit PriorityClass at 0, wired through the existing
  `RUNTIME_K8S_BOT_PRIORITY_CLASS_NAME`, unset on prod today); placeholders stay at −1, so a bot
  preempts one, and the preempted placeholder going Pending is what asks LKE for a node.
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
`ends_at`; a reservation price; a paid-only guarantee. The Individual plan ceiling 1 → 2 is a separate
vexa-platform change, not part of this ADR.

## Slices and order

| Slice | Repo | Changes | Tests |
|---|---|---|---|
| **s1 bot join time** | vexa | `contracts/invocation.v1` (+`joinAt`); `bot/src/index.ts` wait before first `joining` and the reachability gate; `bot_spawn/service.py` passes `join_at` and refuses one past `now + pod lead + 60 s`; `lifecycle/reconcile.py` grace from `join_at` | bot unit: no navigation before `joinAt`, immediate join when past; invocation schema fixture; reconciler does not reap a waiting `requested` row; spawn refuses a far `join_at` |
| **s2 one clock** | vexa | meeting-api outbox table + drain (`meeting.planned.changed`); `POST /internal/meetings/{id}/start` with the sweep's safeguards moved under it; `POST /meetings` ISO check + per-account cap; `_auto_join_loop`/`auto_join_tick` removed, keys to `removed_config.py`; Flows `booked_bot` flow; `keyed.py` deadline column, paging, retired rows, per-account ceiling for `booked_bot`, `refused` returned; seed versioning; `invite_join` → `POST/PATCH /meetings`; Flows in lite, Helm default on, bank profile; gateway service-door allow-list for the new route | cancel-before-fire and cancel-during-fire → no bot (409 `not_scheduled`); recurring link starts the right row; block lists and bot name applied on the new path; outbox survives a Flows outage and delivers in revision order; Flows down from `pod_at` to `start` → bot still starts before `deadline`; 201 retired-flow rows do not stop a due fire; 41 accounts × cap do not refuse a 42nd account; seed upgrade replaces an unedited copy and leaves an edited one; booked < 10 min and after `join_at`; ceiling full → starts when a slot frees; lite and Helm-default boot with Flows |
| **s3 cluster** | vexa-platform (+ vexa runtime knob) | controller replacing `deployment-capacity-reserve.yaml` + `cronjob-capacity-resize.yaml`; second scheduler Deployment, RBAC, config; bot PriorityClass at 0; `RUNTIME_K8S_BOT_SCHEDULER_NAME` in vexa runtime and chart | render tests for the chart; controller unit tests (placeholders not counted, draining nodes skipped, no bot moved); runtime test that the scheduler name reaches the pod spec; staging rehearsal: burst at :58 with booked pods started at :50 |
| **s4 docs** | vexa (`docs/docs/`) | `api/meetings.mdx` (auto-join section, `scheduled_at`, PATCH 409 from start − 10 min, 429 counts waiting bots, the promise verbatim); `core/meetings.mdx:76-87`; `how-to/calendar-sync.mdx:102-103`; `configuration.mdx:314-332`; `deployment-kubernetes.mdx:366, 690-694` and lite/Helm pages (Flows required); `concepts.mdx:36`; troubleshooting "a scheduled meeting didn't auto-join"; ADR-0050 lead values; `quickstart.mdx:19` bot time defined from pod start; changelog entry (earlier pod start, wait and lobby billing, PATCH window) | `docs-current` gate; link check; the "~60 s" claim gone everywhere |
| **s5 billing** | vexa + vexa-platform | platform rating dual acceptance, pod-start minutes, `ours` not billed, transcription split, account page and `/pricing` text, `bot_never_joined` reason retired for v2 rows; bot emits `pod_started_at`; meeting-api provenance fields + version bump + `outcome_class` at terminal transition; service-authority boundaries from pod start | rating fixtures for both versions; never-admitted v2 run billed from pod start; `ours` run not billed; transcription minutes unchanged by wait; grade parity fixture; zero-balance account's waiting bot is stopped |

Order: s1, s3 and s5 can start as soon as this design is signed. s2 after s1. s4 after s2 and s3.
Within s5 the platform's dual acceptance lands on prod before meeting-api's version bump. The release
comes after all five and after the lobby-billing notice.

## Consequences

- One clock, one lead per meaning, one path from a planned row to a bot; a cancel cannot be beaten by
  an alarm.
- Self-hosted lite and default Helm installs run Flows from now on (more processes and memory on lite).
- Booked bots occupy a ceiling slot and are billed for up to 10 min before the start; never-admitted
  bots are billed for their lobby wait. Both are customer-visible price changes.
- `PATCH /meetings` is refused from `start − 10 min` (was `− 2 min`).
- The guarantee holds only within the account ceiling and the cluster pool max.

## Open questions (not settled by the rulings)

1. **Per-account booking cap.** The value of `BOOKED_MAX_PER_ACCOUNT` (proposed: 500 future planned
   rows, equal to the keyed per-person share), and whether bookings further ahead than a horizon
   (e.g. 90 days) are refused.
2. **`unknown` outcomes.** Ruling 3 exempts only `ours`. Is a run graded `unknown` billed?
3. **What the ceiling counts.** `stopping`, `needs_help` and `needs_human_help` pods are not counted
   today, so "counts every bot pod" is not literally true. Count them too (tightens the ceiling for
   customers whose bots are leaving), or leave as is?
4. **Placeholders and the autoscaler.** The settled design said the autoscaler ignores placeholders.
   On LKE the expendable cutoff cannot be set, and a −1 placeholder that is preempted is exactly what
   triggers scale-up today; a placeholder-only node still scales down. This ADR keeps −1. The
   alternative is placeholders below −10 with the controller resizing the LKE pool through the Linode
   API itself.
5. **Pool max versus the promise.** The guarantee fails silently for everyone when the pool is at max.
   Whether the pool max is raised (an open vexa-platform PR proposes 50) and what spend that allows
   is a money decision outside this ADR.
