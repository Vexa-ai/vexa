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

**Founder ruling, 2026-09-02, after this package was built.** One MCP server, at the gateway,
assembled from tool manifests each owned by the domain that owns the door — meetings, identity,
flows, agent. No MCP-over-MCP, no separate `mcp-control` service. And a second constraint that
decides several of the calls below: **a NO-AGENTS product must be deployable** — gateway + meetings
+ flows + identity, with no `core/agent` at all — and it must carry the meetings tools *plus*
`whats_waiting`.

This package is the extraction, not the destination. What it proved (64 tools, no reach past HTTP,
one identity resolution, a parity fixture) is what makes the move mechanical. What it got wrong is
the HOME: a second MCP process, and `whats_waiting` on agent-api. Both are named below.

## 1. What one domain exports — `mcp.tools.v1`

Schema: [`manifests/mcp.tools.v1.schema.json`](manifests/mcp.tools.v1.schema.json). Stubs with the
real tool lists: [`manifests/`](manifests/).

```jsonc
{
  "contract": "mcp.tools.v1",
  "domain": "flows",
  "owner": "core/flows",                       // committed HERE, not in the gateway
  "base_url_env": "FLOWS_API_URL",             // the gateway key naming this domain's door
  "served_at": "/.well-known/mcp-tools.json",  // the DEPLOYED version answers, not the built one
  "tools": [
    {
      "name": "whats_waiting",                 // globally unique; a duplicate is a boot failure
      "route": { "method": "GET", "path": "/queue/waiting" },
      "identity": "user",                      // user | admin | operator | none
      "profiles": ["no-agents", "full"],       // absent from tools/list outside these
      "enriched_by": [                         // optional additions from another domain
        { "domain": "agent", "route": { "method": "GET", "path": "/api/queue/enrichment" },
          "adds": "the setup gate, the claim-book questions, the first-run friction ask" }
      ]
    }
  ]
}
```

Four fields carry the whole design:

- **`route`** — the tool's input schema and description are **derived from the bound route's
  OpenAPI operation**, never written in the manifest. This is the mechanism
  `core/meetings/services/mcp` already runs on: `operation_id` on a FastAPI route, read by
  `FastApiMCP`. A manifest carrying its own schema would be a second place to write one thing.
  `route: null` is legal only where `base_url_env` is null — the three edge-owned tools.
- **`identity`** — what the route requires, enforced by the assembler *before* the forward, so a
  domain cannot be reached with an identity it never asked for.
- **`profiles`** — a tool the running profile does not carry is **absent from `tools/list`**, not
  present-and-failing. An agent that cannot see a tool recovers; an agent told a tool exists and
  handed a 502 tells the person the product is broken.
- **`enriched_by`** — the answer to "one tool, two domains". The **owner merges**, never the
  gateway: one tool, one owner, one response shape. An enrichment whose domain is absent simply does
  not appear, and the response names which enrichments resolved.

## 2. How the gateway assembles the union

The server already exists: `core/meetings/services/mcp` — a FastAPI app whose 14 routes carry
`operation_id`, wrapped by `FastApiMCP(app, headers=["authorization", "x-api-key"])` and mounted at
`/mcp` (`app.py:1210,1235`), fronted by the gateway's `MCP_URL` (`gateway/app.py:236`). It is
already stateless, already forwards the caller's key as `X-API-Key`, and already never reaches past
its door. **It stops being a meetings-owned service and becomes the assembler** — one directory
move, `core/meetings/services/mcp` → `core/gateway/services/mcp`, because a server that serves four
domains cannot be owned by one of them. Nothing about its transport, auth or tests changes.

Startup, in order, and every step fails the boot rather than degrading:

```
1. PROFILE      read VEXA_PROFILE (no-agents | full | dev). It names which domains exist.
2. DISCOVER     for each domain in the profile: GET {base_url_env}/.well-known/mcp-tools.json
                A domain in the profile that does not answer is a BOOT FAILURE — "the meetings
                tools are missing" must never be something a person discovers by asking for one.
                A domain NOT in the profile is never asked, so its absence costs nothing.
3. FILTER       drop every tool whose `profiles` does not contain the running profile.
4. UNION        merge. A name claimed by two manifests is a BOOT FAILURE naming both domains —
                never last-one-wins. Two domains claiming one name is a design question and a
                person has to answer it.
5. BIND         for each tool, fetch the owning domain's OpenAPI, find the operation for
                {method, path}, and derive the input schema + description. A route named by a
                manifest that the domain's OpenAPI does not carry is a BOOT FAILURE — that is the
                manifest lying about its own service, which is the one failure this design can
                otherwise hide.
6. SERVE        one MCP server, one /mcp, one instruction string (this package's
                instructions.py moves here — it is the assembly's text, not a domain's).
```

Per call: resolve identity once (the gateway already does this — `_mcp_key`, `app.py:939`), check
the tool's declared `identity`, forward to `{base_url}{path}` with `X-User-Id`, shape the response.
**That is the whole assembler.** It holds no domain logic, which is the property that makes it
gateway-shaped rather than a fifth service.

Two things the assembler must NOT do, both learned here: it must not compose across domains (that is
what `enriched_by` is for, and the owner does it), and it must not carry product copy (that belongs
in the owning domain, read hot).

## 3. This package's eight modules → domain directories

| this package | destination | door | tools |
|---|---|---|---:|
| `tools/meetings.py` | `core/meetings/services/meeting-api` | meeting-api | 17 |
| `tools/workspaces.py` + `tools/friction.py` | `core/agent/control_plane` | agent-api | 24 |
| `tools/flows.py` (+ `bot_schedule` from meetings) | `core/flows` | flows-api | 11 |
| `tools/identity.py` (+ `settings` from workspaces) | `core/identity/services/admin-api` | admin-api | 6 |
| `tools/panel.py` + `tools/docs.py` | `core/gateway/services/mcp` (the assembler) | none | 3 |
| `tools/rehearse.py` | `deploy/dogfood/rehearse` | the package | 3 |

Three tools move domain on the way, and each is a correction rather than a preference:

- **`bot_schedule` meetings → flows.** A booking is a durable reaction; it lives in the flows
  database and is cancelled through the flows projection. Today it fans out to three doors.
- **`settings` agent → identity.** `mail_minutes`, `mail_rsvp`, `bot_name`, `timezone` are read by
  **flows** at processing time and must exist with no agent domain deployed. A per-person preference
  in a workspace file is unreachable in `no-agents`.
- **`transcript_terms` agent → meetings**, split: the extractor is mechanical and belongs beside the
  transcript; the entity index is an agent enrichment.

`report_friction` and its three siblings stay agent-owned, which means **a `no-agents` deployment
has no improvement loop**. That is an open call, flagged in the manifest, not a decision made here.

## 4. `whats_waiting`, split

Flows owns it: the queue is a queue of reactions, and reactions are flows state. `GET /queue/waiting`
on flows-api.

| item kind | owner | source | present in `no-agents` |
|---|---|---|---|
| `live_now` | flows core | meeting-api `/bots/status` for the caller | yes |
| `blocked` | flows core | its own `reaction.status = blocked` | yes |
| `stuck` / `ours_not_theirs` | flows core | its own failed reactions + the ours-or-theirs list | yes |
| `welcome` (first turn, and anonymous) | flows core | copy, read hot | yes |
| `next_options` / `close_with_options` / `offer_self_sustain` | flows core | copy, read hot | yes |
| `setup` (the `.scaffolded` gate) | **agent enrichment** | agent-api `/api/queue/enrichment` | **no** |
| `question` (the claim book) | **agent enrichment** | same | **no** |
| `tell_us` (the first-run friction ask) | **agent enrichment** | same | **no** |

Flows calls the enrichment route when `AGENT_API_URL` is configured, merges into `items`, and states
what it resolved:

```jsonc
{ "uid": "128", "waiting": 2, "items": [ ... ],
  "enrichments": { "agent": "resolved" } }      // or "absent" — never a silent short list
```

`enrichments` is not decoration. A queue that is short because a domain is off and a queue that is
short because nothing is waiting are opposite facts, and the reader has to be able to tell them
apart — the same rule as `read_ok` on a transcript.

**The commit in this branch puts `GET /api/queue/waiting` on agent-api. That is the wrong home** and
the `no-agents` constraint is what proves it: the tool would vanish from a deployment that is
required to carry it. The queue module (`core/agent/control_plane/queue.py`) moves to `core/flows`
with the three agent-sourced item kinds lifted out into the enrichment route; the copy mechanism
(`_global/queue/*.md`, read hot) moves with it unchanged.

## 5. The two deployment profiles

Compose profiles, minimal diff: the agent tier carries `profiles: ["full"]`, everything else stays
unprofiled and therefore starts in both.

```yaml
agent-api:     { profiles: ["full"] }
agent-worker:  { profiles: ["full"] }
runtime:       { profiles: ["full"] }
mcp:           { environment: [ VEXA_PROFILE=${VEXA_PROFILE:-no-agents}, AGENT_API_URL=... ] }
```

```
docker compose up                      # no-agents: gateway · meetings · identity · flows
docker compose --profile full up       # full: + agent-api · agent-worker · runtime
```

`VEXA_PROFILE` is what the assembler reads at step 1, and it is the ONE value that decides the tool
surface — derived from nothing, so a deployment cannot end up with a profile it did not choose.

### What `gate:config-contract` needs for a profile-scoped service

Three changes, in order of how quietly each fails without them:

1. **`targets` is not enough — a key needs `profiles` too.** Today check 3 requires every declared
   key to appear in every surface its `targets` name. `AGENT_API_URL` on the MCP assembler exists in
   `full` and not in `no-agents`; without a per-key profile the gate either demands it in the
   no-agents surface (wrong) or the key goes undeclared (worse). Add `profiles` to
   `KeyDeclaration` in `deploy/contracts/config.v1/config.schema.json`, defaulting to all.
2. **A cross-profile key must be `class: capability`, never `required-explicit`.** A service that
   boots in `no-agents` may not have a required key pointing at a `full`-only service — the boot
   preflight would refuse to start the product the profile exists to ship. Add check 6: for every
   adopted service, a key naming a service outside the service's own narrowest profile is
   `capability`, with a `when_unconfigured` that says which tools disappear.
3. **The adopted-service entry names its profiles**, and `composeServiceEnv` reads the profiled
   block (it already does — the parse is line-wise and profile-blind). The entry this branch added
   for `mcp-control` becomes the entry for `mcp`, with `profiles: ["no-agents", "full"]`.

And one gate that does not exist yet and is the point of the whole design:

4. **`gate:mcp-manifest`** — for each profile, union the in-profile manifests, assert every name is
   claimed once, assert every bound route exists in its domain's OpenAPI, and assert the `no-agents`
   union contains the meetings tools **and** `whats_waiting`. That last assertion is the founder's
   constraint expressed as a test, and it is the only thing that stops the no-agents product
   quietly losing a tool six merges from now.

## What in this branch survives the move, and what does not

| survives | why |
|---|---|
| `tests/rig_surface.json` + the parity test | the 64-tool surface is the contract regardless of who serves it; the assembler's union is diffed against the same fixture |
| `tests/test_thin_forward.py` | the rule is the same in a domain's tool module as in this package's |
| `delegation.py` + its byte-identity test | one verifier, wherever the assembler runs |
| the four B6 reaches being gone | they are gone from the tool bodies, and the bodies move intact |
| `core/agent/control_plane/{claims,person_settings}.py` and the flows `source_event_prefix` filter | routes the tools forward to; the owner is the question, not the existence |
| the `behavior/{asks,mail}` move | product behaviour, unrelated to who serves the tools |

| does not survive | replaced by |
|---|---|
| `mcp-control` as a compose service, and its `config.v1.json` | the assembler at `core/gateway/services/mcp`, profile-scoped |
| `core/mcp/src/vexa_mcp/` as the final home of eight tool modules | four domain directories + the assembler |
| `GET /api/queue/waiting` on agent-api | `GET /queue/waiting` on flows-api + `/api/queue/enrichment` on agent-api |
| `web.py`'s sign-in pages living at the edge | admin-api's onboarding routes (§3, identity manifest) |
