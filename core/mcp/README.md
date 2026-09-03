# `core/mcp` — the Vexa control MCP, as an edge

**What it is.** One MCP surface over the whole machine: 64 tools across identity, meetings,
workspaces, flows, friction, rehearse, panel and docs. A person's Claude Code or Codex connects to
it over streamable HTTP with their own token, and drives Vexa from their own chat.

**Where it sits.** Beside `core/gateway`, and for the same reason. It is an **edge**: it exposes the
domains with the caller's identity attached and owns no state of its own. It is not a domain (it has
no store) and not a client (it is a service in the stack, fronted at `/mcp`).

```
a person's agent ──HTTP──▶ gateway /mcp ──▶ mcp-control ──▶ gateway     meetings, transcripts, bots
                                                        ├─▶ agent-api   workspaces, queue, claims, settings, friction, terms
                                                        ├─▶ admin-api   accounts, per-person keys
                                                        └─▶ flows-api   facts, reactions, timeline
```

**It does not run on a laptop.** "Local mode" is about where the WORKSPACE lives, not where this
runs: `workspace_regime(mode="local")` means the person's files are on their own machine and no
cloud agent runs for them — the workspace verbs still operate on the cloud copy, git
(`workspace_pull` / `workspace_push`) is the sync, and their own agent writes the local files with
its native tools.

## The rule this package exists to hold

Every tool is a **thin forward**: build a request, call the HTTP client, shape the response.
`tests/test_thin_forward.py` walks the AST of every tool and fails on

1. any `subprocess`, `docker`, `psycopg` or `sys.path` mutation — the four reaches that made the
   predecessor un-packageable;
2. any tool naming more than one service base URL;
3. any filesystem write outside `VEXA_HOME`;
4. a tool body over the statement budget, or a string literal over 400 characters that is not its
   own docstring — product copy belongs in `_global`, read hot, not in an image.

Every relaxation is one allowlist entry naming the tool and the backlog behind it.

## Where this came from

`deploy/dogfood/rig/vexa_control_mcp.py`: 5,033 lines in one file, 64 tools, a 187-line instruction
literal, a 740-line ASGI middleware, no `pyproject.toml`, no tests, no CI — and it could not start
without a docker socket, two hardcoded container names, a Postgres URL and source checkouts of two
other trees. The full inventory is `~/dev/biz/drafts/2026-09-02-mcp-seam-inventory.md` (B1, B6).

That path is now a 34-line shim that imports this package, because `rig.sh` and a live `~/.storm`
symlink name it and a rig is running from it. The 5,033 lines are gone either way.

## Running it

```bash
uv run --with 'mcp>=2.1,<3' --with uvicorn --with pytest python -m pytest -q   # the suite
python -m vexa_mcp                 # streamable HTTP on $PORT (default 18310), path /mcp
python -m vexa_mcp --stdio         # the offline test transport, not a product path
```

Configuration is env, service-shaped, declared in `config.v1.json` and checked by
`gate:config-contract`. `VEXA_URL` is the one value a deployment must name; the sibling URLs default
to the in-network service names.

---

# Destination

**Founder rulings, 2026-09-02, after this package was built.** In the order they were given, because
each narrows the one before it:

1. **One MCP server, at the gateway**, assembled from tool manifests each owned by the domain that
   owns the door. No MCP-over-MCP, no separate `mcp-control` service.
2. **A separate paid per-seat product, fully MCP, must compose onto this line without touching OSS
   code** — so the assembler reads manifests from the OSS packages *and* from a mounted directory,
   and there is exactly **one** entitlement hook.
3. **`whats_waiting` is flows.** One forward to flows-api's pending-reactions projection with
   `subject=<uid>`. Nothing is unioned at the gateway.
4. **Identity is the only domain everyone depends on.** Meetings, flows and agent must work
   independently *and* together in **any** configuration — identity plus any subset of the three,
   which is eight deployments, not two.

This package is the extraction, not the destination. What it proved — 64 tools, no reach past HTTP,
one identity resolution, a parity fixture — is what makes the move mechanical. What it got wrong is
where things live, and ruling 4 is what makes each error visible rather than a matter of taste.

## 1. The dependency graph

```
                    ┌──────────┐
                    │ identity │   accounts · person facts · onboarding · entitlement subject
                    └────▲─────┘   depends on NOTHING
           ┌─────────────┼─────────────┐
           │             │             │
     ┌─────┴────┐  ┌─────┴────┐  ┌─────┴────┐
     │ meetings │  │  flows   │  │  agent   │
     └──────────┘  └──────────┘  └──────────┘
        bots            the           desks
        transcripts     reaction      knowledge
        bot defaults    engine        friction

  A domain's declared doors are ITSELF and IDENTITY. Nothing else. There is no meetings→agent
  edge, no agent→meetings edge, no flows→agent edge — in either direction, at any layer.
```

**Eight configurations, and every one of them is a product:** identity alone, +meetings, +flows,
+agent, +meetings+flows, +meetings+agent, +flows+agent, all four. A tool declares `requires` — the
domains it needs — and the assembler serves exactly the tools whose requirements are met. Enumerating
deployment NAMES (`no-agents`, `full`) was the earlier shape and it was wrong for the same reason
every enumeration of a product matrix is wrong: the eighth configuration nobody named is the one that
breaks.

| domain | `depends_on` | tools | present when |
|---|---|---:|---|
| identity | — | 6 | always |
| meetings | identity | 17 | meetings deployed |
| flows | identity | 11 | flows deployed |
| agent | identity | 24 | agent deployed |
| gateway (assembler, edge-owned) | identity | 3 | always |
| rehearse (dev harness) | identity + all three | 3 | dev |

## 2. How the three couple without depending on each other

Two mechanisms, and only two.

**Events published into flows — fire-and-forget.** A domain publishes a fact and does not wait, does
not retry into an error, and does not care whether anybody admitted it. **A missing flows domain is
tolerated**: the publish is dropped and the publisher's own verb still succeeds. That is what makes
`meetings` deployable with no flows at all — a meeting still records, it simply produces no reaction.
Declared per manifest in `publishes_events`.

**Flow steps naming an agent — "not present", never an error.** A flow definition may name an agent
step. With no agent domain deployed that step resolves to **not present** and the reaction continues
or parks by the definition's own rule; it does not fail. A flow that cannot run its agent step in a
deployment without agents is a flow that was written for a different deployment, and the engine says
so in the step's own state rather than in a 502.

Everything else is forbidden. In particular a domain may not read another's store, call another's
route, or import another's module — the three shapes the seam inventory found in the rig, one layer
up.

## 3. Three things this branch has wrong by construction

**`start_onboarding` calls two domains.** It creates the account on admin-api and then seeds a
workspace on agent-api. Under ruling 4 that is identity→agent and it cannot exist. The shape:
`POST /onboarding/start` is an **identity route and nothing else**; it publishes
`onboarding.completed`; **seeding a desk is a reaction to that event**, and it exists only where the
agent domain does. The tool then has one door in all eight configurations, and a deployment with no
agents onboards people perfectly well — it just never seeds a desk, because there are none.

**`bot_send` reads the person's default bot name from agent-api.** Under ruling 4, meetings→agent.
The fix is not a second call, it is ownership: **a bot default is a fact about the bot, and the bot is
meetings'.** `bot_name` moves next to `bot_config`, keyed by person id, and meeting-api resolves it
internally. `bot_send` becomes one gateway forward.

**`bot_schedule` uses three doors for one verb** — the person's timezone from agent-api, the pending
rows straight out of the flows database over `psycopg`, and the fact through the flows intake. It
becomes one flows forward: flows owns the booking (it is a durable reaction) and resolves the
timezone from **identity**, the one door it is allowed. *The founder's question — why does this read
a timezone from agent-api — is what produced ruling 4.*

### The settings file, sorted

`core/agent/control_plane/person_settings.py` holds six keys in one vocabulary. They are two
different kinds of thing and that is why one file in the agent domain made two other domains depend
on it:

| key | what it is | owner | read by |
|---|---|---|---|
| `bot_name` | this person's default for THEIR bot | **meetings** — next to `bot_config` | meeting-api, resolving `bot_send` internally |
| `timezone` | a fact about the person | **identity** | flows (`bot_schedule`, every stated time), agent |
| `mail_minutes` | how this person wants to be contacted | **identity** | flows |
| `mail_join` | " | **identity** | flows |
| `mail_rsvp` | " | **identity** | flows |
| `mail_prep` | " | **identity** | flows |

The four `mail_*` keys are contact preferences — facts about the person, not about the mail — so they
go with `timezone`. The `settings` tool stays one tool and becomes identity's; its vocabulary shrinks
by exactly one key, and that is the only user-visible change in the move.

## 4. What a manifest exports — `mcp.tools.v1`

Schema: [`manifests/mcp.tools.v1.schema.json`](manifests/mcp.tools.v1.schema.json). Stubs with the
real tool lists: [`manifests/`](manifests/).

```jsonc
{
  "contract": "mcp.tools.v1",
  "domain": "flows",
  "source": "oss",                             // oss | mounted
  "owner": "core/flows",                       // committed HERE, not in the gateway
  "base_url_env": "FLOWS_API_URL",
  "served_at": "/.well-known/mcp-tools.json",  // the DEPLOYED version answers, not the built one
  "depends_on": ["identity"],                  // itself and identity. Anything else fails the gate.
  "tools": [
    { "name": "whats_waiting",                 // globally unique; a duplicate is a boot failure
      "route": { "method": "GET", "path": "/queue/waiting" },
      "identity": "user",                      // user | admin | operator | none
      "requires": ["identity", "flows"] }      // absent from tools/list unless both are deployed
  ],
  "publishes_events": [                        // fire-and-forget; a missing flows is tolerated
    { "event": "meeting.completed", "status": "published today", "triggers": "post_meeting v4" }
  ]
}
```

- **`route`** — the tool's input schema and description are **derived from the bound route's OpenAPI
  operation**, never written in the manifest. This is the mechanism `core/meetings/services/mcp`
  already runs on (`operation_id` + `FastApiMCP`). `route: null` only where `base_url_env` is null.
- **`identity`** — what the route requires, enforced by the assembler *before* the forward.
- **`requires`** — the domains this tool needs. Unmet ⇒ **absent from `tools/list`**, not
  present-and-failing. An agent that cannot see a tool recovers; an agent told a tool exists and
  handed a 502 tells the person the product is broken.
- **`depends_on`** — itself and identity. This is the field the new gate rule checks.
- **`publishes_events`** — the composition seam, and it is not the gateway's.

## 5. `onboarding.completed` — the carrier the paid product hangs on

**Founder ruling: `onboarding.completed` triggers billing on the new product.** That makes it a
contract, not a convenience, and it pins five things:

| | |
|---|---|
| **published by** | **IDENTITY ONLY.** Never agent-api, never the terminal, never a flow step. One producer for one fact — the rule the whole architecture is organised around, and the one this event is most likely to lose, because five different places currently think they decide it (§6). |
| **payload** | `subject` (the person's id), `org` (the organisation id), `seat` (the seat this person occupies) — declared in `flows.v1` as an **identity-owned carrier** |
| **cardinality** | **exactly once per completed onboarding.** Deduped on the subject at the producer, not by the consumer: a billing domain that charges twice because a retry re-published is a defect nobody sees until an invoice |
| **profiles** | **fires in every configuration**, including one with no agent domain at all. No agent code on the path — that is the point of moving it to identity |
| **consumers** | a desk seed (agent, when present) · a welcome flow (flows, when present) · a subscribe prompt (a mounted billing domain, when present). All three are reactions; none of them is a branch in a tool |

Fire-and-forget still holds: with no flows domain the publish is dropped and onboarding still
completes. What may NOT happen is the reverse — onboarding completing without the event, in any
configuration, because that is a person who is signed in and has no seat.

## 6. Where onboarding completion is decided TODAY — slice 2's starting inventory

**Five independent paths, none of which publishes anything, and three of which seed a desk inline.**
That is why `onboarding.completed` cannot simply be added: there is no single moment to add it to
yet.

| # | path | file:line | what it does |
|---|---|---|---|
| 1 | the MCP sign-in verbs | `core/mcp/src/vexa_mcp/tools/identity.py:119` `start_onboarding`, `:132` `:135` account create, **`:141` desk seed**, `:185` `confirm_login`, `:227` token mint | account → code → token, and seeds the desk itself |
| 2 | the MCP's OAuth door — **a second copy of path 1** | `core/mcp/src/vexa_mcp/oauth.py:88` `:90` account create, **`:94` desk seed** | the same three steps again, in another module |
| 3 | the MCP's shared helper — **a third copy** | `core/mcp/src/vexa_mcp/identity.py:301` `account_for`, `:306` create, **`:312` desk seed** | same again, called by `confirm_login` |
| 4 | the terminal's own auth | `clients/terminal/src/app/api/auth/adminApi.ts:61` `:65` account, `:70` token, **`:471` `/agent/workspace/init`**, `:407` `:661` bootstrap admin claim | a whole parallel onboarding a person can complete without the MCP existing |
| 5 | the flows mail door | `core/flows/src/flows_steps/common.py:158` `:171` account, `:183` token; `core/flows/src/flows_defs/production.py:365` and `:1185` `ag.workspace_init(uid)` via `flows_steps/agent.py:91` | an invite from a stranger onboards them silently |

And the two services that hold the state the five paths race over:

- `core/identity/services/admin-api/src/admin_api/app/main.py:428` (create), `:555` (token mint),
  `:944` (`_BOOTSTRAP_ADMIN_LOCK` — the first sign-in claiming the instance)
- `core/agent/control_plane/api.py:2801` `POST /api/workspace/init` — the de-facto completion signal
  today, in the one domain that must not be on the path

Slice 2 is therefore: **make identity the only path**, publish the event there, and turn the three
inline desk seeds into a reaction. The three duplicate copies inside `core/mcp` collapse to nothing
when the tool becomes a forward — they are an artefact of a file that could not call a route.

## 7. How the gateway assembles the union — including the private domain

The server already exists: `core/meetings/services/mcp` — a FastAPI app whose 14 routes carry
`operation_id`, wrapped by `FastApiMCP(app, headers=["authorization", "x-api-key"])` and mounted at
`/mcp` (`app.py:1210,1235`), fronted by the gateway's `MCP_URL` (`gateway/app.py:236`). Stateless,
forwards the caller's key as `X-API-Key`, never reaches past its door. **It stops being a
meetings-owned service and becomes the assembler** — `core/meetings/services/mcp` →
`core/gateway/services/mcp`, because a server serving four domains cannot be owned by one of them.

```
1. DEPLOYED   which domains answer. identity is required; meetings, flows, agent are each present
              or not — eight configurations, and the set is discovered, not named.
2. DISCOVER   OSS:     GET {base_url_env}/.well-known/mcp-tools.json per deployed domain
              MOUNTED: every *.mcp.tools.v1.json in VEXA_MCP_MANIFEST_DIR (a volume; empty by
                       default, and empty IS the OSS product), then each one's served_at.
              A domain declared deployed that does not answer is a BOOT FAILURE.
3. FILTER     drop every tool whose `requires` is not satisfied by the deployed set.
4. UNION      OSS and mounted into ONE name space. A name claimed twice is a BOOT FAILURE naming
              both manifests — a mounted manifest gets no precedence, so a private mount can
              never shadow an OSS tool.
5. ENTITLE    at most ONE manifest may declare `entitlement`; two is a boot failure; none is the
              normal case (no hook, entitled() always true).
6. BIND       per tool, fetch the owning domain's OpenAPI and derive the schema + description from
              the operation at {method, path}. A route the OpenAPI does not carry is a BOOT
              FAILURE — a manifest lying about its own service is the one failure this design
              could otherwise hide.
7. SERVE      one MCP server, one /mcp, one instruction string.
```

Per call: resolve identity once (`_mcp_key`, `gateway/app.py:939`) → `entitled(subject)` if a
manifest declared the hook → check the tool's declared identity → forward to `{base_url}{path}` with
`X-User-Id` → shape. The assembler holds no domain logic, no composition and no product copy. That is
what makes it gateway-shaped rather than a fifth service, and what lets a paid domain arrive as a
mounted file plus a URL.

## 8. This package's eight modules → domain directories

| this package | destination | door | tools |
|---|---|---|---:|
| `tools/meetings.py` (+ `bot_name` defaults) | `core/meetings/services/meeting-api` | meeting-api | 17 |
| `tools/workspaces.py` + `tools/friction.py` | `core/agent/control_plane` | agent-api | 24 |
| `tools/flows.py` + `whats_waiting` + `bot_schedule` | `core/flows` | flows-api | 11 |
| `tools/identity.py` + `settings` (person facts) | `core/identity/services/admin-api` | admin-api | 6 |
| `tools/panel.py` + `tools/docs.py` | `core/gateway/services/mcp` (the assembler) | none | 3 |
| `tools/rehearse.py` | `deploy/dogfood/rehearse` | the package | 3 |

`report_friction` and its three siblings stay agent-owned, so **a deployment without the agent domain
has no improvement loop.** Flagged in the manifest as an open call, not decided here.

## 9. `whats_waiting`, and what has to become an event

**It is flows.** `GET /queue/waiting?subject=<uid>` — the subject-scoped pending-reactions
projection, nothing unioned at the edge. Today's tool fans out to four sources; three are not
reactions and do not survive without an event and a flow definition. **This table is the decision.**

| today's fan-out | reads now | already a reaction? | what it needs |
|---|---|---|---|
| **flows reactions** — `blocked`, `stuck`, `ours_not_theirs` | `GET {flows}/reactions` | **Yes** — this IS the projection | nothing. The ours-or-theirs split becomes a field on the reaction, computed where the reason is written, not a keyword list in a tool |
| **live meeting** — `live_now` | gateway `/bots/status` | **No.** `meeting.started` is emitted in meetings but is **not a registered trigger** (`production.py:56-61` registers `invite.received`, `meeting.completed`, `meeting.upcoming`, `mail.reply`) | register `meeting.started` + a flow whose step stays pending while the call runs and completes on `meeting.completed`. Cheapest of the three — the event exists |
| **desk cards** — `setup` (`.scaffolded`) and `question` (the claim book) | agent-api workspace files | **No.** Nothing publishes either | two new events, `desk.unscaffolded` and `claim.proposed`, published by agent + a flow definition each. Absent without the agent domain by construction, which is correct: there is no desk |
| **friction first-run ask** — `tell_us` | the friction record count | **No** | one new event `friction.first_run` + a flow. Or drop it: a flow that fires once per person is a heavy way to say hello |

Two consequences. **The copy stops being the tool's** — every sentence a person hears becomes a flow
definition's, in `behavior/`, editable without a deploy, which is what PRD §3.8 asks for and what a
keyword list inside a tool body can never be. And **a short queue is no longer ambiguous**: pending
reactions carry which flow produced them, so "nothing is waiting" and "that domain is not deployed"
are answered by the flow list rather than by a field the tool invents.

**The commit in this branch puts `GET /api/queue/waiting` on agent-api. That is the wrong home**, and
ruling 4 proves it: the tool would vanish from seven of the eight configurations.

## 10. The gate rule, and the migration backlog it produces

**The rule — `gate:domain-doors`: a domain's doors are IDENTITY, RUNTIME, and ITSELF. Anything else
fails.** Enforced two ways: `depends_on` in each manifest, and a scan of each domain's source for
another domain's base-URL key.

**Runtime is a primitive, not a domain** (founder ruling). It spawns bots for meetings and workers
for agent; it has no tools, no manifest and no person-facing surface, so nothing about it is a
product decision a person ever sees. `* → runtime` is allowed exactly as `* → identity` is, and the
15 sites that reach it are not backlog.

Run today, **27 call sites fail.** This is the migration backlog, and its shape is the finding: the
agent domain reaches into three others, and nothing reaches into it.

| edge | sites | representative |
|---|---:|---|
| **agent → meetings** | 16 | `core/agent/shared/config.py:99` (`meeting_api_url` in the settings model) · `core/agent/control_plane/api.py:1019,1052,2308,2338,2346,2423` · `schedule_digest.py:48,56,80,109` · `admin_panel.py:198` |
| **agent → gateway** | 3 | `transcription_watcher.py:136,178` · `admin_panel.py:195` |
| **agent → flows** | 3 | `shared/timeline.py:51` · `control_plane/api.py:1039` (the queue route this branch added) · `dispatch.py:546` |
| **meetings → gateway** | 3 | `services/mcp/app.py:40,575,585` — dissolves when that service becomes the assembler |
| **flows → agent** | 1 | `flows_steps/common.py:13` |
| **flows → gateway** | 1 | `flows_steps/common.py:12` |

**The seven `→ gateway` sites are the same violation wearing a hat.** A domain calling the edge that
fronts it is not a shortcut to the edge — it is a call to whichever domain the edge forwards to, with
an extra hop and the caller's own identity laundered through it. `transcription_watcher` reaching
`/bots/status` is `agent → meetings`; `flows_steps/common.py:12` reaching the gateway is flows
calling meetings. Counting them separately would let a domain satisfy the gate by adding a hop, which
is the opposite of the rule.

So the backlog is really **agent → meetings (18 once the two watcher sites are read for what they
are), agent → flows (3), flows → meetings (1 via the edge), flows → agent (1)** — and three sites
that disappear with the assembler move. Every one of them is what ruling 4 exists to remove.

### What `gate:config-contract` needs alongside it

1. **`targets` is not enough — a key needs `requires`.** `AGENT_API_URL` on the assembler exists only
   where the agent domain does. Add it to `KeyDeclaration` in
   `deploy/contracts/config.v1/config.schema.json`, defaulting to always.
2. **A cross-domain key must be `class: capability`, never `required-explicit`.** A service that
   boots without a domain may not require a key pointing at it — the preflight would refuse to start
   a product the configuration exists to ship. `BILLING_API_URL` and `VEXA_MCP_MANIFEST_DIR` are the
   same class: absent is normal.
3. **The adopted-service entry names the domain it belongs to**, so rule 1 and `gate:domain-doors`
   read the same table. The `mcp-control` entry this branch added becomes the entry for `mcp`.
4. **`gate:mcp-manifest`** — for each of the eight configurations: union the satisfiable manifests,
   assert every name is claimed once across oss and mounted, assert every bound route exists in its
   domain's OpenAPI, assert at most one `entitlement`, and assert that identity-only still serves a
   usable surface. That last line is ruling 4 written as a test, and it is the only thing that stops
   the eighth configuration quietly breaking six merges from now.

## What in this branch survives the move, and what does not

| survives | why |
|---|---|
| `tests/rig_surface.json` + the parity test | the 64-tool surface is the contract regardless of who serves it; the assembler's union is diffed against the same fixture |
| `tests/test_thin_forward.py` | the rule is the same in a domain's tool module as in this package's — and it must be extended to follow module-local helpers, which is where `bot_send` and `bot_schedule` hide their second and third doors |
| `delegation.py` + its byte-identity test | one verifier, wherever the assembler runs |
| the four B6 reaches being gone | gone from the tool bodies, and the bodies move intact |
| `core/agent/control_plane/{claims,person_settings}.py`, the flows `source_event_prefix` filter | routes the tools forward to; the OWNER is the question, not the existence |
| the `behavior/{asks,mail}` move | product behaviour — and `behavior/` is where the flow definitions §9 needs already live |

| does not survive | replaced by |
|---|---|
| `mcp-control` as a compose service, and its `config.v1.json` | the assembler at `core/gateway/services/mcp` |
| `core/mcp/src/vexa_mcp/` as the final home of eight tool modules | four domain directories + the assembler |
| `GET /api/queue/waiting` on agent-api, and `queue.py`'s four fan-outs | one flows projection + three new events and their flow definitions (§9) |
| `person_settings.py` as one vocabulary in one domain | bot defaults in meetings, person facts in identity (§3) |
| `start_onboarding` calling two domains, and its three duplicate copies | one identity route publishing `onboarding.completed` (§5, §6) |
| `_global/queue/*.md` as the copy mechanism | flow definitions in `behavior/` |
| `web.py`'s sign-in pages living at the edge | admin-api's onboarding routes |
