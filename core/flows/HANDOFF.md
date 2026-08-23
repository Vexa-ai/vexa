# HANDOFF — core/flows (issue Vexa-ai/vexa#1315 · branch `codex/1315-group-meeting-flow`)

**One line:** Vexa's workflow engine is a reaction table with receipts; Dapr is a possible future
executor of the same steps, never the foundation. Requirements + rejected options:
[docs/WORKFLOW-ARCHITECTURE-PRD.md](../../docs/WORKFLOW-ARCHITECTURE-PRD.md). Full illustrated
architecture (dependency vs data-flow, step cycle, states, runtime, domain design):
https://claude.ai/code/artifact/f8406f6f-4726-465c-8fac-c6d3c45b8566

## State (2026-08-23, all pushed through `534930e2`)

- **Engine** `src/flows/` — stdlib-pure at import: admission (dedup by `source_event_id` UNIQUE),
  leased loop (`FOR UPDATE SKIP LOCKED`, two commit points around every effect), receipts,
  reconciler (2 UPDATEs: expired leases, passed block deadlines), signals (resume/retry/cancel as
  audit rows), status projection, Clock port, sqlite/Postgres seam (`db.py`, lazy SQLAlchemy —
  house style). Schema: `schema.sql` (reaction · effect_receipt · signal, epoch-seconds columns).
- **Validation** `tests/` — 27 tests + storm, all green:
  fixture matrix (PRD §13 rows) · n8n shape coverage (branch, fact-emitting sub-flows, error
  workflow, v1/v2 coexistence) · hostile suite (malformed refs, signal abuse, retired-version and
  renamed-step rows, clock regression) · LOOPBACK fixture (the world answers back through the
  webhook, redelivered 3×) · 6-invariant randomized storm (`STORM_ROUNDS=1000` ≈ 5 s;
  `STORM_SEED=n` reproduces a run exactly).
- **Bugs the harness caught & fixed** (regression tests exist for each): resume re-blocked the
  gate forever (now: a signal CONFIRMS the blocked step's receipt — the human is the effect) ·
  v1 shadowed v2 for new events (matcher: newest version per flow identity; PRD Q9 answered) ·
  KeyError on retired flow version · KeyError on renamed step (both now typed failures).
- **THE PRODUCT FLOW** `src/flows_defs/onboard_and_meet.py` (founder spec, tested in
  `tests/test_product_flow.py`): invite intake → onboarding-by-email sub-flow (research → ONE
  question → block on human) → bot at start−2 min → post-meeting processing **queued behind
  workspace readiness** with hourly nudge emails — late, never lost.
- **Witness** `witness/` — three stages, all PASSED:
  `run.py` (doubles, real clock, Mailpit auto-detect) and `run_real.py` (fixture ICS + fixture
  transcript, REAL bot admitted to a real Google Meet, REAL agent worker → real commit `56cef8e`
  in workspace 11, minutes mailed). Human-verified in Mailpit.

## Live witness round 2 (2026-08-23 afternoon, pushed `ce81c333`) — REAL mailbox front door

`witness/run_live.py` + `witness/mail_real.py`: the real `info@vexa.ai` mailbox (creds in
`~/dev/vexa-secrets/business/vexa-mail.enc.env` — **the app password leaked into chat/terminal
during vaulting: ROTATE it and re-vault**) is the product's front door. Proven live with the
founder: real calendar invite → ICS parsed (VEVENT-only + TZID — the 1970 VTIMEZONE bug is fixed)
→ organizer auto-provisioned → REAL confirmation email → **iMIP RSVP** (`METHOD:REPLY`,
`PARTSTAT=ACCEPTED` over SMTP — Vexa shows "Yes" in the guest list, no Calendar API needed) →
**onboarding is a REAL agent conversation over email** (persistent `onboarding` chat session:
agent writes every mail, inbound replies become its turns, IT writes `.scaffolded` when its
acceptance test passes — the same gate the terminal uses) → bot at start−2min → post-meeting
processing queued behind `.scaffolded` with nudges → minutes verbatim in the email body.

**Lessons that are now law:**
- **A step NEVER sleeps or polls internally — every wait is a `Wait`.** The first real adapter
  slept inside a step and froze the whole runner for minutes (mail unpolled, second meeting
  invisible, bot dispatch would have missed). Production loop should add a step-duration watchdog.
- **State must outlive the process.** The runner's throwaway sqlite caused duplicate
  confirmation emails on every restart; the engine dedups perfectly against a durable DB — the
  storm now pins this (`test_engine_restart_with_durable_db_never_repeats_effects`).
- **Match replies by thread (`In-Reply-To`), not sender** — a sender-matched reply "answered"
  the onboarding question from the wrong mail.

**Doubles still standing** (each with its failure story attached — promotion is spec-complete):
in-memory sqlite → Postgres · run_live.py script → `flows-worker` service · in-loop IMAP →
separate integration process · ad-hoc steps → `flows_steps/` adapters with receipts · gateway
polling → meeting-api outbox · regex ICS → real iCalendar parser · sender-matched replies →
threaded · "research from email address" stub → the agent's real research · fixture transcript →
real transcription service (no whisper in this stack) · recipients capped to the organizer →
`_global` Inside rule + consent policy · `changeme` admin auth · amd64-emulated bot image.

## The two binding product constraints

1. **UI-LESS**: email is the entire product surface. Every artifact — confirmation, onboarding
   question, nudge, the meeting note itself — travels VERBATIM in the email body. No UI links;
   replying to the email is the interface.
2. **Retry placement rule**: inside one process's life → the component (bot reconnects, webhook
   delivery queue stays tactical, emits only `delivery_exhausted` as a fact) · crash-surviving /
   minutes-to-days + business step → flows · high-volume dumb transport → dedicated adapter.

## Environment (dev laptop)

- Stack `vexa-v012` (compose at `~/dev/vexa-worktrees/mk-minutes/deploy/compose`, .env gitignored;
  agent-api needs BOTH files: `docker compose -f docker-compose.yml -f docker-compose.hot.yml`).
  Gateway :18056 · admin-api :18057 (X-Admin-API-Key: changeme) · agent-api :18100 (dev:
  `X-User-Id` header) · runtime :18090 · Mailpit :8025/:1025 (standalone container `mailpit`).
- Terminal dev server: `cd ~/dev/vexa-worktrees/mk-minutes/clients/terminal && PORT=3010 node
  server.mjs` (npm dev script hard-codes 3000).
- Witness user: subject 11 (anna@bank.com); key minted with scopes `["bot","browser","tx"]`
  (valid scopes are exactly those; `bot` alone cannot read transcripts). Key/uid cached at
  `/tmp/witness-key` `/tmp/witness-uid` — re-mint via `POST /admin/users/{id}/tokens`.
- **Bot image**: `vexaai/vexa-bot:dev` on Docker Hub is STALE (wrong entrypoint layout) and the
  env base has NO arm64 manifest. Local fix (done, redo after image gc):
  `docker pull --platform linux/amd64 vexaai/meet-join-env:dev && docker tag … vexa/meet-join-env:dev
   && docker build --platform linux/amd64 -f core/meetings/services/bot/Dockerfile -t vexaai/vexa-bot:dev .`
- Worker spawn needs `/workspaces/<subject>` to EXIST → `POST /api/workspace/init` (the
  account-creation seam). A worker restarted after clearing a dead workload SKIPS pre-boot
  in-stream messages — resend payloads (flows receipts make this automatic; hand dispatches don't).

## The behavior/machinery split (founder ruling 2026-08-23 late)

**Machinery contains no prose.** Prompts, email copy, seeds, flow compositions and params are the
BEHAVIOR DOMAIN — data, hot-editable, governed like content. First move done: the three flow
kickoffs live in `behavior/prompts/*.md` (per-version override via flow params `{"prompts":
{...}}`). NEXT COORDINATED MOVE (touches core/agent — the sidebar session's lane): relocate
`core/agent/workspace-seeds/` into the behavior domain; it is the product's voice, not agent
machinery. The behavior domain is HIGHEST-LEVEL, diverse and PROPRIETARY — it lives as a private
content tree mounted at `VEXA_BEHAVIOR_DIR` (the `_global` pattern), never in the OSS repo;
the in-repo `behavior/` keeps only published showcase defaults. Resolution: flow params →
private mount → showcase.

## Delivery of behavior (founder ruling 2026-08-23 night)

The behavior domain ports to customers via **Vexa Delivery** (enterprise BYOC conveyor,
`~/dev/vexa-delivery` — its own owning session; M0–M2 delivered, do not trample): behavior tree
→ digest-pinned signed channel artifact → customer-verified, customer-admitted → mounted as
`VEXA_BEHAVIOR_DIR`. Prompt changes get image-grade ceremony; pure-data artifacts stay
reviewer-inspectable. Channel artifact spec = M3+ item in vexa-delivery, not here.

## Open work, in order

0. **Rotate the leaked app password** (see above) — one-line re-vault.
1. **Real adapters** — promote `witness/real_steps.py` into `src/flows_steps/{meeting,agent,email}_steps.py`
   (HTTP, effect keys, receipts); wire the flows-worker as a process (compose service `flows-worker`,
   Postgres tables via `schema.sql` at boot).
2. **Mailbox integration** — real vexa@bank.com inbound replaces the fixture ICS: parser-only
   (mailroom's deciding half becomes `invite_intake`); onboarding replies arrive as resume signals.
3. **meeting-api outbox** — `meeting.completed` written in the lifecycle transaction; publisher →
   flows admission. Until then the witness polls.
4. **Contracts + calm.json** — three sealed schemas (event.v1/step.v1/status.v1) registered in
   `architecture.calm.json`. ⚠ another live session holds calm/seal dirty in this worktree — coordinate.
5. **Migration backlog** (delete old mechanism IN THE SAME CHANGE the flow takes over, never before):
   post-meeting notifier callback in `core/agent/worker/engine.py:575` → flow #1 ·
   auto-join sweep + `scheduling/` subsystem (meeting-api) → bot-dispatch flow ·
   `sweeps/single_flight.py` dissolves as its loops become flows/tickers.
6. Engine niceties when needed: per-flow retry budgets, `Wait` with deadline, event ledger table
   (tripwire: second flow on one event — ALREADY TRUE for invite? no: different events), FlowVersion
   registry (tripwire: runtime activation), Dapr spike (tripwire: days-long waits / rejoining branches).

## Gotchas for the next session

- Commit **by pathspec** (`git commit -- core/flows`) — another session works this worktree.
- Pre-push gates: every directory needs a non-empty README; `check-isolation.js` is ESM (repo is
  `type: module`); root `gates.mjs isolation` runs each brick's script.
- Never `docker system prune`; never stop stack containers; Mailpit container is disposable.
- The seed/terminal/minutes work lives on `mk-minutes-shape` (worktree `~/dev/vexa-worktrees/mk-minutes`)
  — flows work stays here; don't cross-commit.
