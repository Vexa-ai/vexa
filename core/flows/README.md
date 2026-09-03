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

## Configuration

Every environment key this brick reads is declared once in `src/flows_config.py`, with its class
(`required-explicit` · `defaulted` · `capability`), its default and why it exists.
`tests/test_config_declaration.py` asserts BOTH directions — nothing read that is not declared,
and nothing declared that nothing reads. `core/flows` is not yet one of the five `config.v1`
adopted services (seam backlog B7/B9); this table is what makes that adoption a transcription.
