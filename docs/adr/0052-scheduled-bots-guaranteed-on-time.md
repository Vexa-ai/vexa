# ADR 0052 — Scheduled bots are guaranteed on time: the pod starts 10 minutes early, and meetings depend on Flows for scheduling only

**Status:** proposed · 2026-11-01, revised 2026-11-04 · [#1962](https://github.com/Vexa-ai/vexa/issues/1962)
(replaces its 31 Oct ramp-limit design) · builds on ADR-0046 (flows own time) and ADR-0050 (keyed
schedules) · founder rulings of 2026-11-01 are quoted where they settle a choice; the three questions
this ADR first left open were answered by the founder the same day, and the 2026-11-04 revision
narrows the Meetings → Flows dependency to scheduling (*Decided after review*, items 7 and 8)

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
- The waiting pod counts against the account's concurrency ceiling, the one ceiling booked and ad
  hoc bots share; its value comes from meeting-api's limits model (§8, s2g). At the ceiling, the
  start is refused with the existing `MaxBotsExceeded`; Flows retries until a slot frees or
  `deadline` passes. There is no booked/ad-hoc priority and no fair-share: arrival order decides.

### 3. One clock, in Flows; meetings depend on Flows for scheduling only (founder ruling 1, revised 4 Nov 2026)

Flows is the one clock booked bots start from. That is the whole of what meetings need it for
(decided items 7 and 8): a scheduling dependency, not a coupling. Meetings run fully without Flows;
only a booking with `auto_join` needs it.

- meeting-api's `_auto_join_loop` and `auto_join_tick` are removed. `AUTO_JOIN_SWEEP_INTERVAL_S`,
  `AUTO_JOIN_LEAD_S` and `AUTO_JOIN_GRACE_S` go into `meeting_api/removed_config.py` with their
  replacements named. `AUTO_JOIN_RETRY_BACKOFF_S` and `AUTO_JOIN_ALLOW_UNCAPPED` keep their names:
  the start route still reads them (founder, 3 Nov 2026, decided item 4).
- Flows ships in every profile (s2b): lite runs it as supervisord programs, Helm renders it (the
  chart refuses `flows.enabled: false`), the bank profile includes it, compose already runs it.
- **No scheduler, no booking with a bot** (Q1 (a), decided item 8). Where no scheduler is configured
  (meeting-api's publish edge `VEXA_FLOWS_API_URL` unset), a row that becomes `scheduled` with
  `auto_join` on is refused by name: `POST /meetings`, and a `PATCH /meetings/{id}` or
  `PUT /meetings/{platform}/{id}/intent` that makes a plan a booking, answer `422` with
  `code: scheduling_not_deployed` and the `key` to set. Calendar sync still mirrors the event, with
  `auto_join` off and `auto_join_error` "scheduling not deployed". A plan with `auto_join` off is
  unaffected.
- **Flows down.** Bookings are still accepted; their facts wait in meeting-api's outbox (§4), and
  armed alarms fire when Flows returns, up to the deadline (§7). Back before `join_at`: the bot is on
  time. Back after `join_at`: late. Back after the deadline: no bot, and meeting-api's reconciler
  stamps `data.auto_join_error` "not started: scheduler unavailable" on a `scheduled` row with
  `auto_join` on whose deadline passed with nothing on the row from a start call: no
  `auto_join_last_attempt` (the start route writes it before every spawn) and no `auto_join_error`.
  The route stamps its reason on every refusal it answers after its own window checks, the 503s for
  an identity that did not answer and a cap that cannot be read included (those with no hold,
  since nothing was attempted); `too_early`, `backoff`, `not_scheduled`, `past_deadline` and 404
  stamp nothing. So the stamp also lands on a row whose only calls came too early, or after its
  deadline: in both the scheduler was not there in time. A start that is under way when the stamp
  lands clears it when its bot starts. A row whose `scheduled_at` is not a time is stamped "not
  started: no valid start time". The pass reads only rows past their deadline, earliest first, at
  most 200 at a time. That stamp is meetings' own bookkeeping: idempotent, no call to Flows, no
  outbox fact. There is no fallback clock in meetings (Q3 (a)): Flows' own uptime is the fix.
- **Live and ad hoc meetings never wait on Flows.** `POST /bots` goes meeting-api → runtime. The
  `meeting.started` / `meeting.completed` publishes stay best-effort and run as background tasks off
  the lifecycle callback's path (`events.publish_in_background`: each bounded, at most 256 in
  flight, a fact past that dropped and counted as a metric, each task held until it ends), so a
  hung Flows adds nothing to a live meeting's callbacks. Post-meeting work (summaries) waits for
  Flows, because it is a Flows feature.
- Calendar sync arms the rows it mirrored with "scheduling not deployed" again on its first pass
  after `VEXA_FLOWS_API_URL` is set, unless the person set their auto-join themselves.
- `meeting_api/events.py` says so: Flows schedules booked bots; without it a booking with `auto_join`
  is refused by name, and everything else runs the same.

- Rows booked before the upgrade are not lost: the release that removes the sweep first writes one
  outbox fact (§4) for every future `scheduled` row with `auto_join` on (a one-time backfill at
  meeting-api boot, idempotent by revision), so Flows arms them before the sweep is gone.

### 4. Planned rows reach Flows durably: a meeting-api outbox

- Every write that creates, moves, cancels, deletes or toggles `auto_join` on a planned row
  (`POST/PATCH/DELETE /meetings`, calendar sync, the terminal) inserts a `meeting.planned.changed`
  fact into a meeting-api outbox table **in the same transaction** as the row change.
- The outbox reuses the CRM's pattern (`core/crm/src/crm/outbox.py`): bounded, retryable delivery to
  Flows `POST /events` with a publish key; a refused row stays pending and is retried.
- The fact carries `meeting_id`, `uid` (the owner, for the per-account share), `status`,
  `auto_join`, `pod_at`, `join_at`, `deadline`, and a monotonic `revision`. It carries no URL and no
  passcode.
- Delivery is at least once, in revision order per meeting. The keyed row is keyed by `meeting_id`
  and an older revision never overwrites a newer one.
- The existing `meeting.started/completed/shared` publishes stay best-effort; `meeting.started` and
  `meeting.completed` run off the lifecycle callback's path (§3).

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
  row's stamped error as the reason. Meetings decides whether a bot may start (the ceiling, the block
  lists, credit, the deadline: the route refuses past start + `BOOKED_GRACE_S` with 409
  `past_deadline`); Flows decides only when to call again (Q2 (a), decided item 8).
- A refusal by the gateway's service door itself (wrong key, route not allowed, unknown person,
  meetings not deployed) ends the run at once with the door's reason, loudly: it is always wrong and
  waiting does not fix it. The door answers 503, not 403, when the person lookup itself fails (a
  network error, a timeout or a 5xx from identity), so that one flaky cause is retried here like any
  503 (decided item 6). Identity's 404 for an unknown person is an answer, not a failure: it stays a
  loud refusal, never retried.
- The flow is instance-scoped. Its fires are not subject to a person's daily run cap
  (`VEXA_FLOWS_USER_RUNS_PER_DAY`); the account ceiling is the bound.
- `invite_join` is registered in code like `booked_bot`, no longer a seed; a one-off migration in the
  same release removes the stored seed copy (logged when the stored copy had been edited) (s2a, s2d).
- **Known gap: invites do not run through the booked clock yet.** At s2d (`64790526`) `invite_join`
  keeps its ADR-0050 behaviour: its keyed alarm fires at start − 60 s, an invite update moves it and
  `invite.cancelled` cancels it, and `dispatch_bot` sends the bot by URL with `POST /bots`
  (`flows_steps/meeting.py:493`). It creates and moves no planned row, so an invited bot has no
  10-minute lead, no `join_at` and none of the start route's safeguards. The gap closes in follow-up
  slice **s2h**: `invite_join` creates the planned row with `POST /meetings`, keeps the row's id
  against the invite's calendar UID, `PATCH`es that row on a reschedule and `DELETE`s it on a
  cancel, and reads the invitees `post_meeting` needs from Flows' own invite record by meeting id.
  No public contract changes (decided item 5).
- Every **booked** start the scheduler makes goes through `POST /internal/meetings/{id}/start`, and a
  gate fails the build if any meeting-api code starts a booked row's bot by any other function
  (s2d, reworded in s2f; *The contract between Meetings and Flows*, Gates).
- **Known parallel path: `POST /bots` claims a booked row.** Inside `create_meeting_guarded`
  (`bot_spawn/adapters.py:637-656`), `POST /bots` with no row id claims the room's newest planned
  row, `idle` or `scheduled`, a booked one included. An ad hoc bot sent to a room that has a
  booking therefore takes the booking's row at once, without the start route's block lists, attempt
  stamp or `join_at`; an invite's `POST /bots` (above) can do the same. The gate does not cover it.
  It closes in follow-up slice **s2i**. How is not decided (*Open questions*, Q4); the options and
  what each brings:
  - **(a) The claim runs the start route's safeguards** on that row. That puts a second caller in
    front of `start_row`, beside the start route: the parallel booked-start path decided item 5
    forbids, re-opened under another name, and the booked-start gate (*Gates*) would have to admit
    `POST /bots` (`create_bot`) as a second user of `start_row`. The auto-join block lists, which a
    manual `POST /bots` does not apply today, would then apply to an ad hoc bot sent to a booked room.
  - **(b) `POST /bots` leaves the booked row alone.** The ad hoc bot then needs a second row for the
    room, which the unique index on a person's non-terminal rows for one room
    (`uq_meeting_active_user_platform_native`, `scheduled` included) refuses today: either that
    index is narrowed, or `POST /bots` answers 409 for a booked room. With a second row, at `pod_at`
    the booked row's start answers 409 `already_live`, is stamped with the holder's id and is done
    (*The contract between Meetings and Flows*, answers): the booking gets no bot of its own, and
    only its row's stamp says so.
  - **(c) `POST /bots` on a room with a booked row calls the same start route for that row**
    (`start_planned_meeting`): one primitive with two callers, no second path. The booked-start
    gate would admit `POST /bots` (`create_bot`) as a second user of `start_planned_meeting`, and
    `start_row` keeps its one caller. The ad hoc bot then gets the route's own rules: before
    `pod_at` (less 60 s of slack) the route answers `too_early`, the bot it starts waits until
    `join_at`, and the auto-join block lists apply; an ad hoc "join now" on a booking further ahead would need the
    route to take an earlier join time, a change to the route.
- The inverse gate fails the build if meeting-api's ad hoc and live paths (`POST /bots`, the
  lifecycle callback, transcripts, webhooks) use Flows anywhere but the named edges (s2f). An ad hoc
  bot (`POST /bots`) starts without Flows and never waits on it.

### The contract between Meetings and Flows

Meetings depend on Flows for scheduling only (decided item 7). The contract is narrow, and each
side owns its half.

| Direction | What crosses | Carried by |
|---|---|---|
| Meetings → Flows | One fact: a booked meeting's times. `meeting.planned.changed` with `meeting_id`, `uid` (the owner, for the per-account share), `status`, `auto_join`, `pod_at`, `join_at`, `deadline`, `revision`; no URL, no passcode | meeting-api's durable outbox (§4), at least once, in revision order |
| Flows → Meetings, for a booking | One call: "start the pod for meeting X", `POST /internal/meetings/{id}/start` | the gateway's service door, as the row's owner (§5) |

That is the only call scheduling needs, not the only call Flows makes. Flows also calls meetings as
any client does: for its own features (meeting
rows, transcripts, the post-meeting work) and, until s2h, for invites, whose bots it sends with
`POST /bots` by URL (§6, known gap). That `POST /bots` can claim a booked row (§6, known parallel
path, s2i). None of those calls is scheduling, and meetings depend on none of them.

The start call's answers, and what Flows does with each:

| Answer | Meaning | Flows |
|---|---|---|
| 201 `started` | the pod is starting; it opens the meeting at `join_at`. Any `auto_join_error` the row carried is cleared | done |
| 409 `not_scheduled` · `past_deadline`, 404 | nothing to start, ever: cancelled, toggled off, stopped, deleted, not the subject's, or past its window. Nothing is stamped | done |
| 409 `already_live` | a bot owns the room: when it is another row's, the row is stamped with the holder's id; when the claim itself lost to a duplicate, nothing is stamped and the row keeps its attempt stamp | done |
| 403 `blocked`, 422 `meeting_host_refused` | refused for good; the reason is stamped | done |
| 429 `max_bots`, 402 `service_denied`, 503 `stt_unconfigured` · `authority_unavailable` · `spawn_failed` · `identity_unavailable` · `cap_unresolvable` | not now; the reason is stamped (`identity_unavailable` and `cap_unresolvable` with no hold: nothing was attempted) | call again after `Retry-After`, until the deadline |
| 429 `backoff`, 409 `too_early` | not now: inside a hold an earlier answer earned, or before the pod may start. Nothing new is stamped | call again after `Retry-After`, until the deadline |
| the service door's own refusal (`service door: …`) | a configuration fault; meeting-api never saw the call | the run fails loudly (§6) |

- **Meetings owns** the times (`BOOKED_POD_LEAD_S`, `BOOKED_JOIN_LEAD_S`, `BOOKED_GRACE_S`, in
  meeting-api config, §2), the ceiling, the block lists, credit and the deadline. **Flows owns** the
  alarm and when to call again. Flows interprets nothing about a meeting.
- **Not part of the contract:** the best-effort `meeting.started`, `meeting.completed` and
  `meeting.shared` facts. They feed Flows features (live and post-meeting flows, the share mail);
  losing one delays that feature, never a meeting. The lifecycle callback never waits on them (§3).
- **Flows as a client:** from s2h, `invite_join` creates and moves planned rows through the public
  `POST`/`PATCH /meetings` (§6); until then it sends bots with `POST /bots`. Either is Flows
  depending on Meetings, not the reverse.
- **Without Flows** everything in meetings runs except a booking with `auto_join`, which is refused
  by name (§3). **With Flows down** bookings wait in the outbox and are started when Flows returns,
  up to their deadline; past it the row is stamped (§3).
- **Self-scaling** comes from the same shape: a booked bot's pod starts at `start − 10 min`; when no
  node has room it is Pending and the cluster autoscaler adds one. A cold node took about 2 min to
  become Ready plus about 90 s of image pull (one prod measurement, Sep 2026, *Context*), inside the
  10-minute lead, so booked bots need no warm pool. The pool max (§11) is the ceiling of that
  scaling. The same Pending pod is what the public status `queued` shows when the cluster cannot
  place it (§9, defined in [#2034](https://github.com/Vexa-ai/vexa/pull/2034)): one mechanism, for
  every bot path.
- **Gates** (s2f), read from meeting-api's source with `ast`, by function rather than by module,
  each rule with negative controls (the four plants of the Factory review of #2037 among them):
  - *Every booked start goes through the start route* (`tests/test_main_auto_join_wiring.py`):
    meeting-api keeps no sweep (`_auto_join_loop`, `auto_join_tick` and `bot_spawn/auto_join.py`
    are gone); the route is mounted and answered by `start_router`'s handler `start_planned`, the
    only user of `start_planned_meeting`, which is the only user of `start_row`; `request_bot` is
    used only by `start_row`, by `POST /bots` (`create_bot`) and by the Meet respawn
    (`respawn_due`); only `start_row` passes a booked start's keys (`planned_meeting_id`,
    `join_at`) into it, and the Meet respawn's `**kwargs` carry neither. It does not see the known
    parallel path (§6): `POST /bots` claiming a booked row inside the repository.
  - *Meeting-api uses Flows only on the named edges* (`tests/test_flows_edges.py`): every name
    meeting-api uses from `events` or `planned_outbox`, outside those two modules, is an edge
    allowed only at its listed module and enclosing function: `deliver` and `backfill` in
    `__main__`'s outbox loop; `install` (the outbox's flush hook, a database write) where
    `sessions/models.py` is imported; `publish_in_background` in the lifecycle callback
    (`_apply_lifecycle_event`), given only `publish_meeting_started` or `publish_meeting_completed`,
    which appear nowhere else and are never called or awaited there; `publish_meeting_shared` and
    `share_mail_title` in `_mail_the_invite`, which only the share route
    (`mint_transcript_share_by_id`) calls. Inside the two modules, only `publish` uses
    `_flows_base`, `_flows_key` or `httpx`, only the three `publish_meeting_*` functions use
    `publish`, and in `planned_outbox` only `deliver` uses `_flows` or `httpx`. `VEXA_FLOWS_API_URL`
    and `VEXA_FLOWS_API_KEY` are spelled only in `_flows_base`, `_flows_key` and `_flows`, and the
    URL also as `collector/booking.py`'s `SCHEDULER_ENV`, which only `scheduler_configured` and
    `scheduling_refusal` use; `scheduler_configured` is used only by the booking bound and calendar
    sync's re-arm. Every listed edge must still exist, so the lists cannot outlive the code.
  - Not covered: Flows' own side (that `booked_bot` calls only the start route is
    `core/flows/tests/test_booked_bot.py`'s), and code that would reach Flows without naming either
    module, the address or the key.

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
  creates a planned row is the one path for `POST /meetings`, calendar sync and, from s2h,
  `invite_join`. In the
  same transaction it validates `scheduled_at` as ISO-8601 (today any string passes) and counts the
  account's future `scheduled` rows with `auto_join` on against `BOOKED_MAX_PER_ACCOUNT` = 500,
  with no horizon on how far ahead (founder, 1 Nov 2026). Over the cap: `POST /meetings` answers 429 with the cap in the body;
  calendar sync still mirrors the event but creates the row with no alarm and `auto_join_error`
  stamped "booking cap reached".
- One account's bookings can no longer fill another's: the keyed share for `booked_bot` is per
  account (§7).
- A `refused` upsert is no longer a log line: Flows answers the outbox delivery with `refused`, and
  meeting-api stamps `data.auto_join_error` on the row, visible to the owner.
- **What the ceiling counts** (decided by the CEO, 2026-11-01): `count_active_bots` also counts
  `needs_help` and `needs_human_help` pods; `stopping` is not counted.
- **The limits are not this ADR's (s2g).** Every booked-bot limit — the shared concurrency ceiling
  (§2), the future-booking cap (`BOOKED_MAX_PER_ACCOUNT`, 500), the scheduled-bots cap and the
  booking rate — comes from meeting-api's one limits model (`meeting_api/limits.py`, being reworked
  in [#2034](https://github.com/Vexa-ai/vexa/pull/2034)). Its values are resolved by admin-api —
  the plan defaults (`platform_settings`) and the account's overrides (`users.data`) — and served to
  meeting-api over the existing bot-context edge (admin-api's
  `GET /internal/users/{id}/bot-context`, the spawn context meeting-api already reads for the cap,
  the webhook settings, the bot name and the block list). This ADR adds no limit of its own. Until
  s2g lands, `BOOKED_MAX_PER_ACCOUNT` is the deployment setting s2c wired (the bullets above).
- Within one account, fake bookings are bounded by the ceiling (waiting pods count) and paid for
  (§10). `join_at` is never exposed on public `POST /bots`; the internal route sets it and refuses a
  `join_at` later than `now + BOOKED_POD_LEAD_S + 60 s`, so a pod cannot sit on its MeetingToken
  (5 h TTL, `bot_spawn/service.py:936`) longer than 11 min before joining.

### 9. The bot waits until `join_at` (s1)

- `joinAt` (UTC ISO) is added to `invocation.v1` and read in `bot/src/index.ts`. Before it, the bot
  boots the browser and its capture pipeline but opens no meeting page; the first `joining` emit
  and the reachability gate (`index.ts:423-426`) wait until it.
- While waiting the row stays `requested`; the ceiling already counts `requested`. The row's
  `data.join_at` shows when it will join.
- **One capacity mechanism, every bot path.** A pod the cluster cannot place stays Pending, and the
  autoscaler adds a node. That same runtime fact — the Pending pod's placement facts in
  `WorkloadStatus.pending` — is what meeting-api shows as the public status `queued`, for every bot,
  booked included (the founder's ruling of 4 Nov 2026, ~10:00Z, for a queued status). `queued`,
  its `status_reason` (`cluster_at_capacity`, then `will_join_late` once the meeting has started; a
  booked bot's at `scheduled_at`) and when a pending pod counts as queued are defined in
  [#2034](https://github.com/Vexa-ai/vexa/pull/2034), not here: there a normal scale-up wait is not
  queued, only a pod the cluster cannot grow for.
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
- **Pool max 50 nodes** (founder, 1 Nov 2026: «50 nodes»; 20 on `main` today). s3 sets the LKE
  autoscaler max of the main pool to 50, landing or superseding
  [vexa-platform#561](https://github.com/Vexa-ai/vexa-platform/pull/561), which already proposes 50.
- The guarantee is also bounded by that pool max: the controller alarms when a
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
| **s2c outbox + start route** (sweep still on) | vexa (meeting-api, gateway) | outbox table + drain on the CRM outbox pattern; `POST /internal/meetings/{id}/start` with the sweep's safeguards; planned-row creation path with ISO check and booking cap (API and calendar sync); ceiling counts `needs_help` / `needs_human_help`; gateway service-door allow-list | cancel-before-fire and cancel-during-fire → 409, no bot; door: identity lookup network error, timeout or 5xx → 503, retried by Flows; identity 404 → loud, no retry; wrong key → the run fails loudly; recurring link starts the right row; block lists, bot name, last-attempt stamp applied; outbox survives a Flows outage and delivers in revision order; cap refuses `POST /meetings` with 429 and calendar sync rows over the cap get no alarm |
| **s2d switch the clock** | vexa (meeting-api, flows) | `booked_bot` flow; one-time backfill of facts for future `scheduled` rows; `_auto_join_loop` / `auto_join_tick` removed, keys to `removed_config.py`; `invite_join` registered in code, stored seed removed by a one-off migration (its rewrite to `POST/PATCH /meetings` is s2h) | a row booked before the upgrade is armed by the backfill and gets its bot; booked < 10 min and after `join_at`; ceiling full → starts when a slot frees; no meeting-api clock left and no booked start outside the start route (gate) |
| **s2f scheduling only** | vexa (meeting-api, docs) | the s2d gate reworded to every booked start, plus the inverse gate; `POST /meetings` (and `PATCH`, `PUT …/intent`) refuse a booking with `auto_join` with `422 scheduling_not_deployed` where `VEXA_FLOWS_API_URL` is unset, calendar sync mirrors with `auto_join` off; the reconciler's "not started: scheduler unavailable" stamp past the deadline; `meeting.started/completed` published in the background; `events.py`, configuration, deployment and meetings docs wording | with Flows stopped and with Flows hung, `POST /bots` spawns, a lifecycle callback returns well under the 2 s publish bound and webhooks fire; the 422 and the calendar stamp; the stamp is idempotent and calls no Flows; gates catch a planted Flows call on a live path |
| **s2h invites through the booked clock** (follow-up) | vexa (flows) | `invite_join` creates its planned row with `POST /meetings`, keeps its id against the invite's calendar UID, `PATCH`es it on a reschedule, `DELETE`s it on a cancel, reads the invitees from Flows' own invite record; no `POST /bots` | rescheduled invite moves the bot; cancelled invite deletes the row; invitees reach post_meeting; the invite's bot starts through the start route at `pod_at` |
| **s2i `POST /bots` and a booked row** (follow-up) | vexa (meeting-api) | `create_meeting_guarded`'s claim of the room's newest planned row when that row is booked with `auto_join`. Not decided (*Open questions*, Q4): (a) the claim runs the start route's safeguards; (b) `POST /bots` leaves a booked row alone; (c) `POST /bots` calls the same start route for that row; each option's consequences, the gate's included, in §6 | whichever option is chosen, a test pins it for an ad hoc `POST /bots` to a booked room, and the booked-start gate covers it |
| **s2g bot limits** (separate slice, card `29e813493e9e`) | vexa (meeting-api, identity) | every bot limit in Meetings on meeting-api's one limits model (`meeting_api/limits.py`, [#2034](https://github.com/Vexa-ai/vexa/pull/2034)): the shared ceiling, `BOOKED_MAX_PER_ACCOUNT`, the scheduled-bots cap, the booking rate; values from admin-api (`platform_settings` plan defaults, `users.data` overrides) over the bot-context edge | owned by that slice; this ADR adds no limit |
| **s3 cluster** | vexa-platform (+ vexa runtime knob) | main-pool autoscaler max 20 → 50 (`infra/main.tf`, lands or supersedes vexa-platform#561); controller replacing `deployment-capacity-reserve.yaml` + `cronjob-capacity-resize.yaml`; second scheduler Deployment, RBAC, config; bot PriorityClass at 0; `RUNTIME_K8S_BOT_SCHEDULER_NAME` in vexa runtime and chart | render tests for the chart; controller unit tests (placeholders not counted, draining nodes skipped, no bot moved); runtime test that the scheduler name reaches the pod spec; staging rehearsal: burst at :58 with booked pods started at :50 |
| **s5 billing** | vexa + vexa-platform | platform rating dual acceptance, pod-start minutes, `ours` not billed, transcription split, account page and `/pricing` text, `bot_never_joined` reason retired for v2 rows; bot emits `pod_started_at`; meeting-api grading table + `outcome_class` at terminal transition + provenance fields + version bump; service-authority boundaries from pod start | rating fixtures for both versions; never-admitted v2 run billed from pod start; `ours` run not billed, `unknown` billed; transcription minutes unchanged by wait; after s1 + s2c: a waiting bot's minutes counted from pod start, and a zero-balance account's waiting bot is stopped |
| **s4 docs** | vexa (`docs/docs/`) | `api/meetings.mdx` (auto-join section, `scheduled_at`, booking cap, PATCH 409 from start − 10 min, 429 counts waiting bots, the promise verbatim); `core/meetings.mdx:76-87`; `how-to/calendar-sync.mdx:102-103`; `configuration.mdx:314-332`; `deployment-kubernetes.mdx:366, 690-694` and lite/Helm pages (Flows schedules booked bots); `concepts.mdx:36`; troubleshooting "a scheduled meeting didn't auto-join"; ADR-0050 lead values; `quickstart.mdx:19` bot time defined from pod start; changelog entry (earlier pod start, wait and lobby billing, PATCH window) | `docs-current` gate; link check; the "~60 s" claim gone everywhere |

Order:
- After this design is signed, s1, s2a, s2b, s3 and s5 can start (s5's tests that need waiting bots
  run after s1 and s2c).
- s2c after s1. s2d after s2a, s2b and s2c. s2f after s2d, stacked on it. s2h and s2i after s2f.
- s4 after s2f, s3 and s5.
- Within s5 the platform's dual acceptance lands on prod before meeting-api's version bump.
- Release prerequisites: every slice above, s2g (the booked-bot limits on the one limits model)
  among them, and the Individual plan ceiling of 2 bots
  ([vexa-platform#564](https://github.com/Vexa-ai/vexa-platform/pull/564)).

## Consequences

- One clock, one lead per meaning; a cancel cannot be beaten by an alarm. One path from a planned
  row to a bot only once s2h and s2i land: until then an invite's bot is sent with `POST /bots` by
  URL, and an ad hoc `POST /bots` claims a booked row (§6).
- Self-hosted lite and default Helm installs run Flows from now on (more processes and memory on lite).
- Meetings depend on Flows for scheduling only. A deployment without it runs every meeting the same
  and refuses a booking with `auto_join` by name; a Flows outage costs booked bots only, and a booking
  it costs says so on its row.
- Booked bots occupy a ceiling slot and are billed for up to 10 min before the start; never-admitted
  bots are billed for their lobby wait. Both are customer-visible price changes.
- `PATCH /meetings` is refused from `start − 10 min` (was `− 2 min`).
- The guarantee holds only within the account ceiling and the cluster pool max.

## Decided after review

1. **Booking cap.** «500 future bookings; above that the API refuses» — yes.
   `BOOKED_MAX_PER_ACCOUNT` = 500, no horizon (§8).
2. **Cluster ceiling.** «50 nodes». The main pool's autoscaler max is 50 (§11, s3), through or in
   place of vexa-platform#561.
3. **Notice before release.** Asked whether the release ships only after the lobby-billing notice
   has gone to customers: «NO». The release does not wait for the notice; it is not a release
   prerequisite.
4. **Two auto-join keys stay** (3 Nov 2026). Asked whether `AUTO_JOIN_RETRY_BACKOFF_S` and
   `AUTO_JOIN_ALLOW_UNCAPPED` are renamed, removed or kept: «оставить ("a")». Both keep their names;
   §3 removes only the three sweep keys.
5. **Invites through Flows** (3 Nov 2026). «Там у нас может главное проверку пройти, чтобы у нас не
   было параллельных каких-то путей. Чтобы у нас все шло как положено. Через flows.» Every bot
   start, emailed invites included, goes through the booked clock in Flows (§6), with no public
   contract change and a gate against any other start path.
6. **Door refusals** (3 Nov 2026, decided by the CEO under the founder's rulings «fallbacks ONLY for
   FLAKY stuff», 27 Oct, and item 5). A failed person lookup at the service door (a network error, a
   timeout or a 5xx from identity) answers 503 and Flows retries it; every other door refusal,
   identity's 404 for an unknown person included, fails loudly (§6).
7. **Meetings depend on Flows for scheduling only** (founder, 4 Nov 2026, 08:20Z,
   `decision:page:scalability#9661072a43c5`): «scheduled bots are pods that start on schedule (via
   flows) … meetings become dependant on flows and that should only be a dependancy on scheduling -
   not the total coupling; bots join start inside those pods on time; this way we get system self
   scaling». It revises ruling 1 (§3 as first written: "Flows required in every profile", 1 Nov) and
   the gate of item 5 ("every bot start" through the booked clock, 3 Nov), which now reads every
   **booked** start (§3, §6, *The contract between Meetings and Flows*).
8. **The revision and its three questions** (4 Nov 2026, `decision:page:scalability#cc483dc08d9a`).
   The founder answered the Software desk's revision memo «Go» (~08:48Z), and the CEO decided its
   open questions on the desk's recommendations:
   - Q1 (a): where no scheduler is configured, a booking with `auto_join` is refused by name at
     `POST /meetings` (`422 scheduling_not_deployed`); calendar sync mirrors the event with
     `auto_join` off and the reason stamped (§3).
   - Q2 (a), as built: at the ceiling meetings answers `429` with `Retry-After`, and Flows calls again
     until meetings says done (§6).
   - Q3 (a): no backup clock in meetings. A booking Flows did not start by its deadline is stamped
     "not started: scheduler unavailable"; Flows' own uptime is the fix (§3).

## Open questions

- **Q4 (s2i). `POST /bots` on a room with a booked row.** Today it claims the booked row without
  the start route (§6, known parallel path). Options, none chosen: (a) the claim runs the start
  route's safeguards; (b) `POST /bots` leaves the booked row alone; (c) `POST /bots` calls the same
  start route for that row. Each option's consequences are in §6.
