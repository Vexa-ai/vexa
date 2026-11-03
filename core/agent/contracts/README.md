# contracts — published by `agent`

Language-neutral contracts this domain owns (JSON Schema, read by path). Consumers reference these by
path; `gate:schema` validates goldens ≡ schema (P8). All are **unsealed** (in development) except
`invoke.v1`; seal each via `pnpm seal:contracts` as it freezes.

The set is the vocabulary of the **one Dispatcher** (agent-api): an external trigger becomes a `unit.v1`
Invocation, which runs a governed `claude` turn over a mounted `workspace.v1`, optionally producing tasks,
proactive cards, and tool calls.

**`workspace.v1` moved to [`core/workspaces/contracts/workspace.v1`](../../workspaces/contracts/workspace.v1)**
(PRD decision 47, step 1 of the workspaces domain carve) — this domain now CONSUMES it (reads by path,
same as `meetings/contracts/transcript.v1`), it no longer PUBLISHES it.

| Contract | What it governs | Sealed |
|---|---|---|
| **`unit.v1`** | the universal invocation envelope — `{ identity, runner, workspaces, trigger, start }`; trigger ∈ message · scheduled · event · transcription. Everything funnels through this. Also the unit's streams: the signed input entry, and the typed `Fault` its failed `done`/`error` frames and agent-api's 502/503 body carry (vocabulary generated into worker, agent-api and terminal by `gen-faults.mjs`). | no |
| **`routine.v1`** | the retired routine stub; a routine is now a person's own flow (ADR-0046) and nothing compiles to `schedule.v1`. Retired after the adapter release. | no |
| **`task.v1`** | a task's state + its representation as a workspace entity. | no |
| **`tool.v1`** | a tool/integration grant — `{ scope, grant, cred_ref, transport, barriers }`; injected as `claude --allowedTools` + MCP. | no |
| **`event.v1`** | an external event (e.g. email, meeting) mapped into a `unit.v1` Invocation — generic event ingress. | no |
| **`proactive-card.v1`** | a proactive output card with actions (agent-initiated surface). | no |
| **`invoke.v1`** | the legacy meetings→agent invocation seam. **Sealed**; retiring as callers move to `unit.v1`. | **yes** |
| **`models.v1`** | the model catalog: `Catalog` (the operator's `VEXA_MODEL_CATALOG` — providers by adapter, models, access, default; credentials only as `secret_ref`) and `ModelList` (what `GET /api/models/catalog` serves one person). ADR-0043. | **yes** |
| **`blocks.v1`** | THE shared block vocabulary: primitives (title, text, person, row, list, evidence, checklist, action; group, attach) drawn the same in the home, in workspace pages (a ```vexa fence) and in agent replies. Plain text, actions only send prompts, open by id, message a person or expand in place. | no |
| **`home-card.v1`** | the new-chat home's envelope over `blocks.v1` (greeting, ranked cards, the left-out line) and the `Feedback` body. Written by the `home_compose` flow through agent-api, drawn by the terminal. | no |
| **`home-customer.v1`** | the `customer` beside each composition: what Vexa knows about the person as a customer, with evidence ids, confidence and the person's corrections. Read by `customer_profile`, never rendered. | no |
| **`desktop.v1`** | the desktop as a read model: `DesktopSections` (what `POST /api/desktop/sections` answers the person's own app — workspaces, presence, calls, placed agent and file refs resolved for the viewer) and `Desktop` (what the terminal's `GET /api/desktop` composes with crm-api's desk); `Attention` and `Refs` (the attention home, `POST /api/desktop/attention` and `/refs`, feature `home2`). Nothing stored, no model. | no |
| **`credential-broker.v1`** | the Connections credential broker's wire: the signed role assertion (`agent` · `human` · `git`) and every route's request and response body. Carries the canonical Python signer (`assertion.py`) its callers vendor. | **yes** |

Note: `schedule.v1` (the runtime Scheduler's job spec, which routines no longer use) is owned by
`runtime`, not `agent` — see `core/runtime/contracts/schedule.v1/`.
