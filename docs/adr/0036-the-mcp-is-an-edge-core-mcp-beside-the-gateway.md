# ADR 0036 — The MCP is an edge, not a client: `core/mcp` is one cloud deployment beside the gateway

**Status:** accepted · 2026-09-02 · settles the taxonomy question left open by the 0.12 agent line ·
governs [#729](https://github.com/Vexa-ai/vexa/issues/729) ·
[#1035](https://github.com/Vexa-ai/vexa/issues/1035) ·
[#1113](https://github.com/Vexa-ai/vexa/issues/1113) ·
[#1286](https://github.com/Vexa-ai/vexa/issues/1286) · records the architecture half of the
MCP-first product decision

## Context

Vexa is about to be named after an interface it has never treated as a component. A person points
their own Claude Code or Codex at Vexa, and every meeting verb — dispatch a bot, read a transcript,
write a workspace entity, submit a flow, ask what is waiting — arrives as an MCP tool call. That
surface exists today in two unrelated shapes, and the repository has a place for neither.

Verified in this tree at `3c82fdbf0` (code read, not run):

- **The control MCP is a file on a host.** `deploy/dogfood/rig/vexa_control_mcp.py` is **5,033 lines,
  64 `@mcp.tool()` definitions, 3 `@mcp.prompt`s and one 187-line baked `instructions=` string**
  (`:1668`). It has no package, no image, no `tests/`, and no CI lane — `gatePython`
  (`scripts/gates.mjs:287`) discovers a tree only when it carries both a `pyproject.toml` and a
  `tests/`, and this one carries neither. It lives under `deploy/`, which is where deployment shapes
  live, not code.
- **It is not a client of the stack in the places that matter.** Thirty-one of the 64 tools are not
  thin forwards. Four mechanisms reach past a service's front door entirely: `subprocess.run` on
  `docker inspect` to lift another container's admin token (`:104`), `docker exec … cat >` to write
  into another service's volume (`:2153`, `:2887`), `psycopg.connect` straight to the flows database
  (`:4160`, `:4442`), and `sys.path.insert` to co-host the flows engine, `core/agent/shared` and the
  rehearsal package in its own process (`:2063`, `:3604`, `:4472`). **The consequence is mechanical:
  it cannot be packaged as a process.** It needs a docker socket, two hardcoded container names, a
  Postgres URL and source checkouts of two other trees on the same filesystem.
- **The shipped MCP is a different codebase, not a stale copy.** `core/meetings/services/mcp`
  (`vexa_mcp`) is a FastAPI app + `FastApiMCP` serving **exactly 14 tools**, pinned by
  `core/meetings/services/mcp/tests/test_mcp_surface.py:9`, every tool a thin forward of the caller's
  `X-API-Key`. It shares **zero code** with the control MCP. Its home is a *meetings* service, which
  is why it can only ever expose meetings.
- **Nothing in the model says what an MCP is.** `architecture.calm.json` types nodes as `system`,
  `service`, `module`, `contract`, `webclient`, `data-asset`, `database`. The MCP appears once, as a
  `service` inside the meetings system. A second MCP that also speaks for identity, agent and flows
  has no legal position in that model at all.

The question was put directly — *is MCP a separate domain? a client? what is it in our taxonomy?* —
and it is not cosmetic. Where the MCP is filed decides who owns its state, which gates see it, and
whether "meetings in your Claude Code" is one deployment or a thing every person installs.

## Decision

**1. Four kinds, and the MCP is the fourth thing we already had but never named.**

| Kind | Owns | Examples |
|---|---|---|
| **domain** | state, and the contracts over it | `core/meetings`, `core/identity`, `core/agent`, `core/flows` |
| **edge** | nothing — it exposes domains under an identity | `core/gateway` (HTTP, for people and integrations), **`core/mcp`** (MCP, for agents) |
| **client** | rendering | `clients/terminal`, `clients/extension`, a person's own Claude Code |
| **deployment** | assembly of the above | `deploy/compose`, `deploy/helm`, `deploy/lite` |

An edge holds no state of its own, keeps no table, and has no truth a domain does not already have.
Its whole job is identity in, forward out. The gateway has always been this; naming the kind is what
lets a second one exist.

**2. `core/mcp` is a top-level tree beside `core/gateway`** — not under `clients/`, not under
`deploy/`, not inside the meetings domain. A `deploy/` home says it is a deployment shape; a
`clients/` home says it renders; a `core/meetings` home caps it at one domain's verbs. It is a peer
of the gateway because it is the same kind of thing.

**3. The MCP is a cloud edge only.** One deployment, in the stack, reachable behind the gateway's
`/mcp` route. A person's Claude Code or Codex connects to it **over the network**; it is not
installed on a laptop, and there is no laptop-side server to keep in step with a stack. **"Local"
names the workspace and the absence of cloud agent workers — never the location of the edge.**

**4. Every verb is a thin forward, and the allowlist is empty.** No `subprocess`, no `docker`, no
`psycopg`, no `sys.path` mutation in `core/mcp`, enforced by a test in its own package rather than by
review. **A tool that needs to reach a host is a missing endpoint in the owning domain wearing a
shell command** — the port is: add the endpoint, make the tool forward to it. `POST /api/workspace/write`
on agent-api is the first of these and retires both `docker exec` write paths.

**5. Two regimes per workspace, and they mix.** A workspace is `cloud` or `local` independently of
its siblings, so a person's desk can be on their own disk while their groups stay in the cloud. The
mechanism already exists and is not new machinery: the id lives *in* the workspace
(`.vexa/workspace.json`, `core/agent/shared/workspace_id.py` — *"the server registry is the derived
half… rebuildable from the files by walking the root"*), links address both halves by id
(`[[ws:<workspace-id>/<entity-id>]]`, `core/agent/shared/links.py`), membership is a file in the
workspace's own git repo (`policy/members.json`), and the knowledge layer — `entities.py`,
`desk_readme.py`, `desk_now.py`, `terms.py`, `link_resolver.py` — is pure over a directory. What is
added is small and stated here so it is not re-litigated later: `regime` becomes a per-workspace
field, `link_resolver.resolve` is handed a registry that reads `.vexa/registry.json` first and the
cloud registry when reachable, and pull/push refresh that cache. A workspace the laptop does not hold
resolves `not-yours` until the network says otherwise, which is the honest answer offline.

**6. Agent behaviour is served, not baked.** What reaches a person's own Claude Code today is the
64 tool docstrings, three prompts and one baked instruction string. The turn machinery a cloud worker
gets — the entity write-back phase, the desk refresh, the mount-routing rule, the entity index, the
timeline — lives in `core/agent/worker` and reaches a local agent not at all. The MCP serves that
machinery as tools composed from the same `core/agent/shared` functions the worker calls, and the
instruction text becomes liquid the way deeplink presets already are: baked default → `_global`
override → refreshed on an interval, the `prompt_for` shape (`core/flows/src/flows_defs/production.py`)
applied to the one place it is missing.

**7. An edge is gated like any other tree.** `core/mcp` carries a `pyproject.toml` and `tests/` from
its first commit, so `gate:python` discovers it by construction; it is an adopted service in
`gate:config-contract` with its own `config.v1.json`; and it is a node in `architecture.calm.json`
with `node-type: edge`. Nothing about it is exempt because it started as a prototype.

## Trade-off

**One deployment means a person cannot run the edge next to their files.** Every verb — including
ones that only touch a workspace on their own disk — makes a network round trip, and an offline
laptop can do nothing through the MCP at all. We accept this: a laptop-installed server is a second
artifact to version, a second thing to keep in step with the stack it forwards to, and a support
surface with no telemetry. The regime split in decision 5 is what buys back the part that matters —
the person's *files* are local, and their own Claude Code reads and writes them directly with its
native tools.

**Naming a fourth node kind costs a CALM change and re-seals the chart.** The alternative was to keep
typing edges as `service`, which is what hid the question for a year: a `service` that owns no state
is indistinguishable in the model from one that does, and every ownership rule the dataflow gate
enforces (P23) is written in terms of who owns what.

**The empty allowlist in decision 4 will block a port before it unblocks one.** Roughly half the
control MCP's tools reach a host today, and each one becomes an endpoint in another domain before it
can move. That is the intended cost: the shell command was always a missing route, and the release
that does not pay it ships the docker socket as a product dependency.

## Consequences

- **`deploy/dogfood/rig/vexa_control_mcp.py` is ported and deleted, not archived.** Its README
  currently states the opposite — *"it is not the product and it must never become one"* — which was
  true of a prototype and is superseded by this ADR; that file is the first thing a reader of the new
  package opens, and it is corrected as part of the port.
- **`core/meetings/services/mcp` keeps its 14 tools and its seal.** Two MCP surfaces exist during the
  port; the meetings one is a domain service and stays where it is until the edge subsumes it, at
  which point the 14 tools move and its tests move with them. Neither is silently retired.
- **`gate:python` and `gate:config-contract` still under-cover the seam, and the gap is now named.**
  Both are green over trees that exclude most of what runs a turn. Three specific holes:
  `gate:config-contract` checks reads → declaration but never declaration → reads, so a key declared
  and never read passes forever (`PROC_PENDING_GRACE_SEC` was one; `SCHEDULER_TICK_INTERVAL` still
  is); `core/flows` and `deploy/lite` were invisible to `gate:python` until this line added their
  `pyproject.toml`s; and `deploy/dogfood/rig` carries five test files and a `conftest.py` that CI
  cannot see, because the tree has no `pyproject.toml` and the discovery rule needs both — the
  sharpest illustration of decision 7, since somebody has already written the tests. A sixth
  config-contract check — declared-and-never-read is an error unless the declaration carries a
  reason — is the closing move and is not in this ADR's scope.
- **The known backlog is permitted non-conformance, and it is written down.** The seam inventory
  (`core/mcp` as a package; agent-api's 73 routes in one function; the worker turn's seven preambles;
  the two-spellings pairs between the worker and the MCP) is the release checklist, not a surprise. An
  item not on that list is a defect, not backlog.
- **Nothing here is proven by running.** Every claim above is a code read at `3c82fdbf0`. The two
  claims that will need a live leg before the first release are decision 4's empty allowlist (a test
  in the package, once the package exists) and decision 5's mixed regime (a local desk with cloud
  groups, resolved both ways).

## Enforcement map

| Decision | Enforced by | State |
|---|---|---|
| 2 · `core/mcp` is an edge beside the gateway | `gate:dataflow` completeness — a tree on disk that is not a node in `architecture.calm.json` is RED | on the port |
| 4 · thin forwards only | a test inside `core/mcp` asserting the import allowlist | on the port |
| 7 · discovered by the suite | `gate:python` (`pyproject.toml` + `tests/`), `gate:config-contract` (`CONFIG_ADOPTED`) | on the port |
| 6 · instructions are liquid | none — the `_global` override is data, and data has no gate | accepted gap |
| 5 · regimes mix | a test resolving a link from a local desk to a cloud group and back | to build |
