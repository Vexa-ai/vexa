# ADR 0037 — The target architecture: one edge, three domains over identity and runtime, everything else an event

**Status:** accepted · 2026-09-03 · records the shape the 2026-09-02/03 seam pass was measured
against · supersedes decision 2 of
[ADR-0036](0036-the-mcp-is-an-edge-core-mcp-beside-the-gateway.md) (`core/mcp` as a tree beside the
gateway; the rest of 0036 stands) · the reference the domain-doors gate (P9) is written against

## Context

Two days of reading the line's call sites — not running it — turned a taxonomy question into a
measured seam inventory. The MCP surface existed in two unrelated shapes and neither had a place in
the model (ADR-0036); underneath it, the domains were reaching each other in ways nothing enforced:
the agent reached meetings and flows **three different ways** (five direct service calls, three
through the gateway, one through a prototype rig), a client held an internal service secret and
called two domains past the edge, the edge forwarded a person's bearer to a domain that answered
`401 admin key required` while the JSON-RPC envelope still said `200`, one domain minted another's
UI URL and made it a boot blocker, the runtime carried 131 lines of knowledge about the one caller
that used it, a person-fact store lived in an agent workspace, and the support sink for "what did
not work" was agent-api state — which meant a deployment without agents could not have one. Each
of those is a different symptom of the same absence: **there was no written statement of which
door a domain is allowed to open, so no gate could be written and every crossing looked local and
reasonable at its own call site.** This ADR is that statement. It is the design the founder
approved on 2026-09-03 (*"0. The design — looks correct"*) and it is the reference shape, not a
description of the tree: the distance between the picture below and the code is the backlog.

## The design

![Target architecture: clients call one edge with a bearer; the edge assembles routes.v1 and mcp.tools.v1 from the domains present; meetings, flows and agent depend only on identity and runtime; domains publish events into flows over flows.v1; an optional dependency degrades to not_present; behavior/ is content the runtime loads and deploy/ is config.v1 per service.](0037-the-target-architecture-one-edge-domains-over-identity-and-runtime.svg)

Legend — a solid green arrow is a call over a published contract in the allowed direction; a dashed
grey arrow is an optional dependency that degrades to `not_present`; a blue arrow is an event
published into flows and is never a dependency. Dashed boxes are the `full` profile only.

> The rules the picture obeys: identity is the only shared dependency and runtime the only
> primitive (P3, inward and acyclic); every cross-domain call crosses a published contract at the
> edge (P2, P4); the edge assembles from declarations and owns no policy or credential (P5); a
> domain that needs another declares it optional and degrades (P16); anything one domain wants
> another to react to is an event in flows (P23, one writer per carrier); content is loaded, not
> compiled (P11); configuration is a validated contract per service (P14).

Nothing in the figure is a violation. Every arrow crosses one published contract and points the way
the rule says.

## Decision

**1. One door in, and it is the edge.** Every client — a person's own Claude Code, the web
terminal, and the cloud worker while it runs — reaches the system at the gateway with a **bearer in
the header**. One authentication path: no `?token=` query bridge, no token-as-call-argument
fallback, no mode flag that relaxes it; a session is bound by `Mcp-Session-Id` and a token minted
mid-conversation binds to that session. Fetch-only agents lose access by design. *(P20 — authorize
every access, default-deny; P5 — adapt at the boundary someone else owns.)*

**2. The edge assembles; it owns nothing.** There is **one MCP server, at the gateway**, and its
tool list is the union of manifests exported by the domains that are deployed — schema, route
binding, required identity, deployment profiles. The same is true of HTTP: the gateway composes
`routes.v1` scopes from what each domain declares and holds none of its own. It strips inbound
authority, re-stamps it, forwards, and **refuses what it cannot authenticate — per tool, never per
server**. Duplicate tool names at assembly are a startup refusal, not a last-wins. No MCP over MCP,
no separate control service, and **no `core/mcp` tree** — this supersedes ADR-0036 decision 2; the
manifests live with their domains and the assembling server is the gateway's MCP service. The
agent appears on both sides of this without inverting ownership: the server *reads* the agent's
manifest at assembly, and the agent's cloud worker *calls* the server as an ordinary client, with
the same bearer and the same tools a person's Claude Code uses. *(P2 — couple only through
contracts; P5 — the edge owns no policy or credential.)*

**3. Identity is the only shared dependency; runtime is the only primitive.** Meetings, flows and
agent each depend on identity — who is this bearer, what are this person's facts, who is a member —
and on runtime, and on nothing else. Any subset of the three is a legal deployment. *(P3 —
dependencies point inward, the graph is acyclic.)*

**4. A domain that needs another declares it optional and degrades.** Coupling among meetings,
flows and agent is never a hard import or an assumed URL: the dependency is declared, and when the
target is not deployed the call resolves to **`not_present`** and the caller states that fact — a
flow step that names an agent in a no-agents deployment reports `not_present`, it does not crash and
does not silently no-op. The same shape covers a capability a deployment does not have: flows owns a
**link port**, the terminal is one adapter, and a deployment without one carries a passthrough that
states the fact in words rather than a boot blocker. *(P16 — defer the implementation, not the
seam; P18 — fail loud, never silent degradation.)*

**5. Anything one domain wants another to react to is an event in flows.** Publishing is not a
dependency and is tolerated absent. `meeting.started` / `meeting.completed`, `onboarding.completed`,
`desk.unscaffolded`, `claim.proposed`, `friction.reported` / `friction.fixed` are declared in
`flows.v1`; flows holds the pending reactions and **`whats_waiting` is a flows read model** over
them. The consequence is accepted explicitly: a live meeting, a desk card or a friction prompt
appears in the queue only if it is modelled as a reaction — anything not modelled leaves the queue.
The friction sink is one of these events, not a domain and not agent state. *(P23 — one writer per
data carrier, a reader never re-derives a producer's data; P4 — a cross-process carrier is sealed
and versioned.)*

**6. Identity publishes `onboarding.completed`.** It is a first-class event in the identity
contract, carrying subject, org and seat, published by identity and **never by agent-api**. The
workspace a person gets is a *reaction* to it where agents are deployed, not a step inside
onboarding. This is what makes onboarding work in a deployment that has no agent domain, and it is
the seam a per-seat billing product consumes without either side knowing the other. *(P3 — the
inward direction; P23 — the publisher owns the carrier.)*

**7. Runtime spawns what it is told and decodes nothing.** Mounts, names and env arrive through
`runtime.v1` from the caller; runtime carries no knowledge of what a bot or a worker is for, and its
destructive half is guaranteed at the boundary rather than requested over a channel a booting
workload can miss. *(P22 — guarantee teardown, don't request it; P11 — mechanism, not policy.)*

**8. `behavior/` is loaded, not compiled.** Mail copy, flow definitions, presets, prompts and
workspace seeds are **content**: a top-level tree, a peer of `core/`, with no routes and no state,
read at run time by flows and by the edge. Each deployment mounts its own private tree of the same
shape, resolved ahead of the in-repo showcase. A behaviour change is a data change, never an image
rebuild. *(P11 — mechanism, not policy: a specific schema, voice or integration is config at the
edge, never a platform domain.)*

**9. `config.v1` per service, delivered by env, validated at boot.** Every service in the figure —
the MCP service included — is under the config contract, its doors are **required and never
defaulted** to a neighbour's URL, and a missing required door is a loud boot refusal rather than a
placeholder that fails at the first call. *(P14 — config is a validated contract, validate at boot,
fail fast; P18.)*

**10. Two profiles, and they are the unit of deployment.** `no-agents` = identity + meetings +
flows + the edge, carrying the meetings tools and `whats_waiting`. `full` adds agent. The config
contract is profile-scoped, and the gateway assembles whatever is present. A profile is
identity + any subset of the three domains; nothing in the figure is load-bearing for a subset it
is not in. *(P14; P10 — carve a service only when a force requires it.)*

**11. The figure is measured, not admired.** The **domain-doors gate** reads each domain's declared
doors — identity, its own, and whatever it declares optional — and turns CI red on a call that
crosses any other. Until it exists, this ADR is a README rule and by P9 it will rot. *(P9 — every
boundary is mechanically enforced, not aspirational.)*

## Trade-off

**Assembly at the gateway means a tool cannot exist without a domain owning a route behind it.**
Three of today's tools have no server-side home (`start_onboarding`, `bot_send`, `bot_schedule`) and
each must grow one before it can be a manifest entry. That is the intended cost — the same cost
ADR-0036 accepted when it said a tool reaching a host is a missing endpoint wearing a shell command
— and it is paid in routes, not in exceptions.

**Events instead of calls lose the things nobody models.** Under decision 5 the queue shows what
`behavior/` says waits. A capability that was implicitly visible because some code path happened to
compose it is gone until someone writes its trigger and its definition. We prefer the absence to be
visible in a file over being invisible in a composition.

**Optional-and-degrade multiplies the paths that need testing.** Every `not_present` branch is a
branch, and the profile matrix has to be exercised in CI or decision 4 becomes a claim. Two
profiles is the smallest matrix that proves the rule; it is not free.

**Naming identity the single shared dependency makes it the single point of failure.** That is
accepted deliberately: one dependency that everything has is auditable, where three optional ones
that everything sometimes has are not.

## Consequences

- **The domain-doors gate is to build, and the backlog it will red is 27 call sites.** The known
  list at the time of this ADR: the agent reaching meetings and flows five ways directly, three
  through the gateway and one through the rig; the terminal's three agent-api and four identity
  calls made with an internal secret past the edge; flows minting a terminal URL; the runtime's 131
  lines of agent knowledge. Each is branch work with a rule to satisfy, not a judgement call.
- **`whats_waiting`, friction, onboarding and person facts leave the agent domain.** `whats_waiting`
  becomes a flows read model over pending reactions. Friction becomes two events on flows' ingress
  (`friction.reported`, `friction.fixed`) with a read model over the timeline filtered by kind —
  present in every profile because flows is, and reported into by every client including a person's
  own Claude Code. `start_onboarding` becomes an identity route that publishes
  `onboarding.completed`. Person facts (timezone, mail preferences) move to identity and the bot
  name to the meetings bot-context store. Presets and mail copy move to `behavior/`. What stays in
  the agent domain is what it actually owns: workspaces, turns, entities, claims, scaffolds, the
  desk — `full` profile only.
- **`no-agents` must be deployable, and that is the acceptance test for all of the above.** Gateway
  + identity + meetings + flows, no `core/agent` on disk, serving the meetings tools and
  `whats_waiting`, with the post-meeting mail arriving as a fact in words when there is no agent to
  compose a link. If that deployment needs an agent-only module to boot, the coupling is the defect
  — not a reason to ship the module.
- **`core/meetings/services/mcp` keeps its 14 tools and its seal** until the assembly covers them,
  and the prototype control rig is ported and deleted rather than merged. Both were already
  ADR-0036's disposition; nothing here reopens them.
- **The rig is stateless and a mid-conversation token takes effect on the next connection.**
  `stateless_http=True` issues no session id, so nothing can bind a token minted mid-conversation to
  the running one; the stateful alternative breaks every in-flight client on each restart. Register
  → reconnect → continue is the copy; the product answer is the OAuth flow the gateway's MCP service
  speaks.
- **Nothing here is proven by running.** Every count above is a code read of the line at
  `a5ba8e952`. The claims that need a live leg before the first release are decision 4 (a
  `not_present` resolved in a real no-agents deployment) and decision 10 (both profiles booting from
  the same tree).

## Enforcement map

| Decision | Enforced by | State |
|---|---|---|
| 1 · one auth path, bearer in the header | the removal itself — no bridge, no argument fallback, no mode flag to gate | landed |
| 2 · the edge assembles and owns nothing | `gate:dataflow` completeness (a node not in `architecture.calm.json` is RED) + a duplicate-name refusal at assembly | partial · assembly landed, refusal to build |
| 3 · identity the only shared dependency | **domain-doors gate** (P9) | **to build** — 27 sites |
| 4 · optional dependencies degrade to `not_present` | a profile-matrix leg in CI booting `no-agents` and exercising the degraded paths | to build |
| 5 · cross-domain reactions are events in flows | `gate:dataflow` single-writer over the `flows.v1` carriers | partial |
| 6 · `onboarding.completed` published by identity | the event declared in `flows.v1`; `gate:schema` / `gate:contract-version` | to build |
| 7 · runtime decodes nothing | domain-doors gate + the 131-line removal | to build |
| 8 · `behavior/` is loaded, not compiled | none — the private tree is data, and data has no gate | accepted gap |
| 9 · `config.v1` per service, doors required | `gate:config-contract` (`CONFIG_ADOPTED`), MCP service adopted | landed for the MCP service |
| 10 · two profiles | profile-scoped `gate:config-contract` + the CI profile matrix | to build |
| 11 · the figure is measured | the domain-doors gate is decision 11 and decision 3's enforcement both | **to build** |
