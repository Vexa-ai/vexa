# `rehearse` — user states are data

> *"let's get back to the flows, testing different user states — we need to be able to invoke
> those without constant rebuilding."* — founder, 2026-09-02 (PRD decision 38)

Every state a person can be in when a touch reaches them, written as a recipe of steps against the
product's own doors. Entering one takes seconds, needs no image, and leaves the instance alone.

```
rehearse/
  states.yaml     THE CATALOGUE — six states, as data
  catalogue.py    loads and VALIDATES them against a closed vocabulary
  doors.py        the only thing that talks to the stack; one method per verb
  engine.py       rehearse() · subject_reset() — the executor and the two guards
  stub_doors.py   the whole stack as a dict, so every recipe is provable offline
  run_all.py      the catalogue as the test; failures become friction (decision 33)
  tests/          the suite; no network, no docker, no home directory
```

The hand tool is [`../bin/rehearse.py`](../bin/rehearse.py); the control MCP serves the same two
functions as `rehearse` and `subject_reset`.

## The six states

| state | who the subject is | the touch it ends at |
|---|---|---|
| `blank-admin` | nobody yet, on an unclaimed instance | the sign-in mail whose link CLAIMS the instance |
| `organizer-invited` | a user with a desk who just put the mailbox on a meeting | `Prepare: <title>` → `/?s=` (kind `prep`) |
| `attendee-stranger-minutes` | in the room, never signed in | the attendee follow-up → `/?s=` (kind `post-meeting`) |
| `group-member` | a member of a group desk, after a `#group:` meeting | the follow-up whose intent is the group desk |
| `warm-desk-recurring` | two prior reports of the same series on the desk | `Prepare:` again, warm branch |
| `reply-pending` | replied to a minutes mail | the `email_chat` turn |

```bash
bin/rehearse.py states                                       # what the catalogue holds
bin/rehearse.py plan  organizer-invited olga@rehearse.test   # resolve every step, execute none
bin/rehearse.py enter organizer-invited olga@rehearse.test   # ← the link comes back
bin/rehearse.py enter attendee-stranger-minutes sam@rehearse.test --meeting 2026-03-16 --fresh
bin/rehearse.py reset olga@rehearse.test
bin/rehearse.py all                                          # every state, per-state pass/fail
```

## The two refusals

Both run **before the first door**, because this executes on a stack somebody's real work is
living on.

1. **Every address must be under `$VEXA_REHEARSE_DOMAIN`** (default `rehearse.test`). Not only the
   one you typed: the check reads the whole resolved plan, so the organizer a recipe derives and
   the room it pulls out of the fixture are checked too. The founder's identities, `_global`, the
   DNA and OeNB groups are unreachable *by construction*, not by care. The one exception is the
   address the mail double itself answers as (`VEXA_MAIL_ADDR`) — every invite is addressed to it.
2. **No live meeting may belong to anyone outside that domain.** A rehearsal writes facts and
   mail; a live meeting is the one thing here that cannot be re-recorded. The probe **fails
   closed**: if it cannot be read, the run refuses.

## What needs a swap, and what never does

Decision 38.4, and the line is exact — **the tool writes DATA**.

| change | needs an image / a swap? | why |
|---|---|---|
| a new state, or an edit to one | **no** | `states.yaml` is read at call time |
| a preset (`_global/asks/*.md`) | **no** | read from `_global` when the link is clicked |
| a mail template (`_global/mail/*.md`) | **no** | same — read at send time |
| the company layer (`_global/README.md` …) | **no** | an admin edit is a commit in the `_global` repo |
| a flow's steps or version | **no** | submitted flows are rows; the worker refreshes from the DB |
| a fixture in `~/dna-fixtures` | **no** | read from disk per call |
| `VEXA_REHEARSE_DOMAIN`, `VEXA_MAIL_ADDR`, `VEXA_UI_URL` | env only | a lane restart, never a build |
| **a change to `doors.py`, `engine.py` or a service's code** | **yes** | code is code; a swap is a swap |
| **`DELETE /admin/users/{id}`** (what `subject_reset` needs) | **yes, once** | the route ships on this branch; until admin-api is swapped, `subject_reset` reports the user as `remaining` and deletes everything else |

`tests/test_hot.py` is the enforced half of this table: it asserts that every door a recipe uses is
a service already running, that no recipe names a preset, a template or `_global`, that the package
never builds or restarts anything, and that the only three places in `doors.py` that shell out are
the three named in its module docstring. A README claiming "no image needed" would go stale the
first time somebody reached for `docker` to make one awkward state work.

## Where it does not go through a route, and why

Three exceptions, all reads or per-subject deletes, all named in `doors.py`'s docstring and pinned
by `MAY_SHELL_OUT` in the test:

- **`live_meetings()`** — there is no instance-wide live-meeting route (`GET /bots/status` is
  scoped to one caller), so the guard reads the meetings table through the postgres container.
  Read-only, fail-closed. *This is a missing route, filed rather than habituated.*
- **`session_keys_delete()` / `scaffold_keys_delete()`** — agent-api owns
  `agent:sessions:<uid>`, `agent:session:<uid>:*`, `agent:scaffold:<id>` and
  `agent:scaffolds:by:<address>` and exposes no delete. The prefixes are **listed, never globbed
  on `agent:*`**, and there is no `FLUSH` anywhere: the same valkey carries the live transcript
  streams.
- **`friction_delete_for()`** — that subject's friction rows in the flows lane; no route removes
  them.

## Idempotence, and how a state is re-entered

There is **no marker file**. Identity is derived: the ICS UID and the meeting's native id are
functions of `(state, subject, meeting)`, and the fact's `source_event_id` names the meeting **row**
that native id resolves to. So a second run dedups where the product already dedups — the mail
poller on the ICS UID, `seed_meeting` adopting the completed row it already made, `admit()` on
`(source_event_id, flow)`, `user_ensure` on the address.

One state cannot be idempotent by nature: `attendee-stranger-minutes` needs somebody who has never
been seen, and the drop gives them a desk. It **says so** on the second run rather than pretending,
and the way back is `--fresh`, which resets the subject *and* the organizer the recipe derives
(the meeting row belongs to the organizer, so resetting only the subject would leave the fact
deduping the re-entry away).

## `subject_reset(address)`

One subject gone: user (admin-api), desk (the workspace route), sessions and pending scaffolds
(the four redis prefixes above), friction rows, and every mail to or from that address. It
**reads the emptiness back** and reports whatever it could not remove under `remaining` — a reset
that half worked and said "done" is the ledger's phantom `_global` write one layer down. The
instance is never blanked; blanking is [`../bin/blank-instance.sh`](../bin/blank-instance.sh) and
it deletes everybody.

## Running the tests

```bash
cd deploy/dogfood/rehearse && uv run pytest -q     # what gate:python runs
python -m rehearse.run_all --stub                  # the catalogue, offline
```

The suite is offline by construction: no network, no docker, and no read of `~/dna-fixtures` — two
small transcripts in the corpus's own shape live in `tests/fixtures/`. A suite that read the rig's
home directory would pass on bbb and fail everywhere else, and would quietly start measuring
whatever somebody left in that directory.

## The lane a rehearsal runs against

**Never the founder's lane.** The dogfood box carries two flows lanes over one stack: the founder's
(db `flows`, api `:18200`, mailbox `vexa@storm.test`) and the sim lane (db `flows_sim`, api
`:18201`, mailbox `vexa@sim.test`). They share agent-api, admin-api, the gateway and mailpit — all
per-identity — and nothing else. A rehearsal runs on the sim lane.

```bash
deploy/dogfood/bin/sim-lane-up.sh        # api + worker, from THIS checkout's core/flows
deploy/dogfood/bin/sim-mailbox-up.sh     # the inbound poller (admits facts — start it on purpose)
```

Both are twins of `~/.storm/flows-up.sh` with four lane-scoped values changed (db, port, mailbox,
operator key) plus `rehearse.test` in `VEXA_FLOWS_ATTENDEE_DOMAINS` — without which every follow-up
to an `@rehearse.test` attendee is filtered and the flow reports success having mailed nobody.
Their `pkill` patterns are lane-scoped for the reason `flows-up.sh` wrote down the hard way:
`-m flows_worker` is the FOUNDER lane's argv, and the sim worker keeps its renamed `argv[0]` so
neither lane's restart can reach the other.

**Check what the worker loaded before trusting a run.** It prints `4 flows · 21 steps`, the gate
state, and how many versions it hot-loaded from the DB. Then `GET /flows` must show
`shadowing_versions: []`. A flow version authored through the API is newest-wins and shadows the
image's — the sim lane held `post_meeting@16` with a retired step list (`require_workspace`, no
`drop_to_attendees`), so a run against it would have measured flows decision 29 deleted. Retire
anything above the code's version before running:

```sql
UPDATE flow_version SET status='retired' WHERE name='post_meeting' AND version > 4;
```

## Against the running stack

`run_all` on the live doors is the same code with `LiveDoors` in place of the stub. It clicks
nothing: whether a link *works* when a person clicks it is a walk, and a founder's judgment. What
the suite proves is everything up to the click — the touch exists, its link is a scaffold, the
record resolves, the desk holds what it should. Failures are filed through decision 33's route
(`POST /api/friction` on agent-api) with a repro line, so a fixing agent can re-enter the state
without asking anybody how.
