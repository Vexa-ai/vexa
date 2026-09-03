# core/flows — the reaction engine

Durable product workflows as TWO Postgres tables and ONE leased worker loop. A fact enters as a
sealed envelope, becomes exactly one `reaction` row (dedup by constraint), is worked one step at a
time, and every touch of the outside world leaves an `effect_receipt` a retry consults before acting.

- **ADR:** the reaction table with receipts IS the engine; Dapr is a possible future executor of the
  same steps, never the foundation. Full design: docs/WORKFLOW-ARCHITECTURE-PRD.md + the published
  architecture page.
- **Isolation:** `src/flows/` (the engine) imports stdlib only at module scope — no meetings, no
  agent, no runtime, no third-party. Step implementations (`src/flows_steps/`) and flow definitions
  (`src/flows_defs/`) sit OUTSIDE the engine's import graph; the engine receives them as values.
- **No scheduler:** time is a column (`next_run_at`); the worker's `SKIP LOCKED` poll is the only
  clock consumer. Waits cost nothing; escalation is `blocked_deadline`, one column not a service.
- **Testing:** the whole failure matrix runs offline on stdlib sqlite with `FakeClock` and fake
  steps — zero domains attached. Postgres is the production dialect (see `schema.sql`).
  `pyproject.toml` is what puts that suite in CI: `gate:python` discovers a tree by `pyproject.toml`
  + `tests/`, so `make test` and the gate now run the same thing. `tests/test_contract_smokes.py`
  is the one exception — it asserts the LIVE stack shapes and skips itself when the stack is down.

Layering (dependency, not data): flows → domain HTTP APIs → runtime. Domains never know flows
exists — they only write outbox facts. Tactical retries (bot reconnects, webhook delivery) stay in
their components; strategic, crash-surviving coordination lives here.

## The mailbox is a door on the public internet

`src/flows_integrations/mailbox.py` turns real mail into facts, and `mail_policy.py` decides who
it will act for BEFORE it does. Three populations, and only the first two cost anything:

| sender | what happens |
|---|---|
| a known user | routed as before — the account already exists because they made it |
| inside `VEXA_FLOWS_MAIL_DOMAINS` | a colleague at this deployment's own domain, provisioned as before |
| anybody else | no account, no agent turn, no model call — one `mail_quarantine` row, and at most one fixed line (a template, never a model) |

**Unset is not "everyone".** An empty allow-list means the mailbox's own domain, exactly as an
unset `VEXA_FLOWS_ATTENDEE_DOMAINS` means the organizer's (PRD §16.2 — *outside the domain,
never*). `In-Reply-To` still says which conversation a reply belongs to and never says who the
sender is: a reply runs a turn only for that thread's own participant. An invite from an organizer
we cannot place records the meeting facts in the quarantine row and creates nothing (PRD decision
19 — *the workspace is established on the click, never for someone who never clicks*); a known
user can have it re-admitted through the operator's `POST /events`.

Two ceilings bound what the inbox can cost even when every sender is legitimate:
`VEXA_FLOWS_MAIL_RATE_PER_SENDER` and `VEXA_FLOWS_MAIL_RATE_GLOBAL` over
`VEXA_FLOWS_MAIL_RATE_WINDOW_S`, counted on ADMITTED turns in `mail_turn` — so a flood of
strangers cannot exhaust the budget of the people who are allowed to use this.

An inbound body that IS allowed through never reaches a prompt raw: it arrives quoted, fenced,
length-capped (`VEXA_FLOWS_MAIL_BODY_MAX`) and labelled untrusted, with the machinery note AFTER
the block. The frame is the control; the text itself is never altered.

## What is waiting is flows

`GET /queue/waiting` answers, for ONE person, what this engine is holding for them: the pending
reactions scoped to their uid AND their address, each naming the flow that produced it and carrying
a typed reason — `human` (blocked on an answer), `failed`, `not_present` (a domain this deployment
does not run, PRD decision 40.7), `pending`. PRD decision 42.2: *what is waiting IS flows*, so
nothing is unioned at the edge and no other domain contributes items — they publish events, and
flow definitions decide what waits. The projection is `src/flows_queue.py`.

Three flows exist for no other purpose than to put a row in that queue: `live_meeting` (a call is
happening now — pending until `meeting.completed` for the same meeting is admitted), `desk_setup`
and `desk_claim` (the two desk cards, both `needs=("agent",)`, so a deployment without the agent
domain has neither the events nor the reactions — there is no desk there to have a card on).

`desk_setup` and `desk_claim` react to `desk.unscaffolded` and `claim.proposed`, which flows
CONSUMES and does not publish — they are the agent domain's to publish, and they are deliberately
absent from this domain's `publishes_events` for that reason. They have no producer yet: agent-api
would publish the first at `control_plane/routers/workspaces.py` `ws_init` (a desk seeded with no
`.scaffolded`) and the second needs a claim-aware route that does not exist, the claim book being
written today through the generic file route. Until then the two flows never fire, which is the
correct behaviour and not a gap in the queue.

**The words are not in the code.** Every sentence a person hears is a file in `behavior/queue/`,
resolved private-mount-first and read hot, and a pending reaction behavior says nothing about is
counted rather than spoken. `behavior/queue/README.md` is the contract.

## Meetings is optional, and so is the agent domain

Two of the three domains flows can reach are **capability** doors, and their absence is a shape of
deployment rather than a misconfiguration (PRD decision 40.7; decision 5 for meetings, agreed by
the founder). Unset means *that domain is not deployed*: the process boots, admits facts and serves
its queue, and the steps that would have reached the absent domain answer `<domain>:not_present` —
terminal, with the reason on the reaction, never a retry loop against a door that is not there.

| domain | key | steps that declare it |
|---|---|---|
| agent | `VEXA_FLOWS_AGENT_API_URL` | eleven, listed in `tests/test_no_agents.py` |
| meetings | `VEXA_FLOWS_GATEWAY_URL` | `await_start` · `dispatch_bot` · `run_meeting` · `process_meeting` · `email_minutes` · `email_attendees` · `drop_to_attendees` · `prepare_meeting` |

**Presence is a configuration fact, never a probe.** A health check would make *"meeting-api is
restarting"* and *"there is no meeting-api"* the same answer, and only the second is a supported
product — the first is an outage, and it keeps the retry path it has always had.

Two things a reader should not have to discover. The meetings key still says **GATEWAY** because
flows reaches meetings *through the edge* today, which ADR-0037 forbids and which is a separate
change: the gateway resolves the caller's key and enriches every forward with the user's scopes,
workspaces and **limits**, and `POST /bots` enforces the per-user concurrent-bot cap out of that
last one — so calling meeting-api directly is not a rename. And the door resolves **at access**,
never at import (`flows_steps.common.meetings_door`): `from .common import GATEWAY` used to run the
refusal while `flows_steps/meeting.py` was still loading, so an unset door was an ImportError for
the whole step vocabulary, including every step with no interest in meetings.

## Configuration

Every environment key this brick reads is declared once in `src/flows_config.py`, with its class
(`required-explicit` · `defaulted` · `capability`), its default and why it exists.
`tests/test_config_declaration.py` asserts BOTH directions — nothing read that is not declared,
and nothing declared that nothing reads. `core/flows` is not yet one of the five `config.v1`
adopted services (seam backlog B7/B9); this table is what makes that adoption a transcription.
