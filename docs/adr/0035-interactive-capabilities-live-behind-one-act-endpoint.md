# ADR 0035 — Interactive meeting capabilities live behind one act endpoint; the agent is an ordinary client

**Status:** accepted · 2026-08-17 · settles
[#1089](https://github.com/Vexa-ai/vexa/issues/1089) · governs the disposition of the eight sealed
`api.v1` interactive routes and the migration of
[#514](https://github.com/Vexa-ai/vexa/issues/514) ·
[#500](https://github.com/Vexa-ai/vexa/issues/500) ·
[#333](https://github.com/Vexa-ai/vexa/issues/333)

## Context

`api.v1` seals eight `(path, method)` pairs across four in-meeting interactive surfaces —
`POST`/`DELETE /bots/{platform}/{native_meeting_id}/speak`, `GET`/`POST …/chat`,
`POST`/`DELETE …/screen`, `PUT`/`DELETE …/avatar`. Exactly one of them is served: `GET …/chat`,
and it is a stub that returns a hardcoded empty list
(`core/meetings/services/meeting-api/src/meeting_api/collector/app.py:556`). The shape predates the
0.12 agent layer — it is one REST path per capability, inherited from the pre-0.12 surface.

Three options were on the table (#1089): restore the per-surface REST as sealed; make an agent drive
the bot's `acts.v1` bus and retire REST from the contract; or a hybrid.

**Option 2 is not legally available.** `core/agent/README.md` states the domain law: the agent domain
"is never about: bot lifecycle, the meeting row", and `meetings ⊥ agent` — the two domains "meet only
through published contracts". An agent publishing onto `bot_commands:meeting:{id}` is one domain
reaching into another's internals. Retiring REST would remove the only legal route the agent has. The
open question is therefore not *REST vs agent* but the **shape of the one published surface**, and who
may be its client.

Verified against `origin/main` at `e0b356d6` while settling this (code read, not run):

- `acts.v1` (`core/meetings/contracts/acts.v1/acts.schema.json`) already declares all eleven actions —
  `leave` · `reconfigure` · `speak` · `speak_audio` · `speak_stop` · `chat_send` · `chat_read` ·
  `screen_show` · `screen_stop` · `avatar_set` · `avatar_reset`. The **vocabulary is complete; seven of
  the eleven verbs have no bot handler** — `voiceHandler` dispatches `speak`/`speak_stop` only
  (`core/meetings/services/bot/src/index.ts:119-125`), `orchestrator.ts:187` takes `leave`.
- The conformance gate measures **route registration, not reachability**:
  `_implemented_union()` is `_routes_of(gateway) | _routes_of(meeting_api)`
  (`core/gateway/services/conformance/tests/test_contract_conformance.py:83-88`). `POST …/speak` is a
  registered gateway forward (`core/gateway/services/gateway/src/gateway/app.py:345-347`) to a
  meeting-api route that does not exist, so it counts as implemented, the gate is green, and the call
  404s. It carries no `KNOWN_GAPS.json` row — and cannot: `test_no_stale_known_gaps` rejects a row for
  any route in the implemented union. **Registered-but-unserved is invisible to the gate and
  un-auditable in the ledger.**
- Chat **is** persisted, contrary to the stub's own comment at `collector/app.py:538`. Captured chat
  crosses to Node as a `transcript.v1` segment with `source:'chat'`
  (`services/bot/src/index.ts:250-264`), and `collector/ingest.py:85` carries an explicit
  `source != "chat"` carve-out so a point-in-time segment survives ingestion. Chat read is a **query
  over stored segments**, not a persistence build.
- That capture runs on **jitsi only**. `createJitsiChat` is wired at
  `services/bot/src/capture-bridge.ts:1007`; `createTeamsChat` and `createZoomChat` are exported from
  their modules but have **no production call site** — their only callers are their own unit tests.
  `gmeet-capture` has no chat module at all.
- The `va:meeting:{id}:chat` WS carrier has **no producer anywhere in the tree**. The gateway
  subscribes and forwards it (`gateway/app.py:800`) and a conformance test injects a synthetic payload
  (`test_ws_protocol.py:105`), but nothing publishes to it. `ws.v1 ChatMessage` is a sealed shape with
  zero writers.
- **`voice_agent_enabled` is accepted and silently dropped.** The sealed `POST /bots` body declares it
  (default `false`, described as enabling "TTS, chat, screen share, avatar streaming"), but
  `bot_spawn/router.py:397-413` never reads it and `bot_spawn/invocation.py:129-160` has no
  corresponding parameter. `createSpeakController` computes `enabled = !!inv.voiceAgentEnabled`
  (`capture-bridge.ts:1175`) and discards any speak act with
  `speak ignored: voiceAgentEnabled is false` (`:1202`). Already recorded per-hop on #514
  ("DEAD-GATED AT SPAWN"); absent from #1089 and from the published status page until this ADR.

## Decision

**1. `acts.v1` is the single vocabulary for every in-meeting capability.** Nothing gets a second
implementation path. A new capability is a new enum member in the unsealed bot contract, not a new
sealed REST path.

**2. `api.v1` publishes acts through one endpoint:**
`POST /bots/{platform}/{native_meeting_id}/acts`, body = an `acts.v1` `Act`. The publisher is the
`CommandPublisher` port `lifecycle/stop_router.py` already uses to publish `leave` — a proven path
that needs no new machinery. Scope: `bot`.

Sealed-vs-served then becomes checkable **per action against one route** instead of per method across
eight, and capability growth stops being contract churn.

**3. The agent reaches it as an ordinary client** — a `tool.v1` tool calling the published endpoint
with its per-dispatch minted token, never redis, never a bot-side hook. This satisfies
`meetings ⊥ agent` and turns #333 (external agent by URL) into a *runtime* question: a worker holding
an API key, driving `/acts` and reading `/transcripts`.

**4. Disposition of the eight sealed routes:**

| Route | Disposition | Why |
|---|---|---|
| `POST …/speak` | **serve** — sugar over `Act{speak}` | Bot handler exists. Named consumers code against this exact path (the eval rig's `drive` op, #510's blocked 0.12 leg). |
| `DELETE …/speak` | **serve** — sugar over `Act{speak_stop}` | Bot handler exists. Not currently registered even at the gateway. |
| `POST …/chat` | **serve** — sugar over `Act{chat_send}` | Same named-consumer criterion as `/speak`: n6i's integration calls this exact path. Keeping it is one alias over the act publisher. |
| `GET …/chat` | **keep as the chat read surface; stop lying** | It is a read, not an act. Back it with a query over persisted `source:'chat'` segments mapped into the sealed `ChatMessage` shape, or return `501` until a platform has a reader. |
| `POST`/`DELETE …/screen`, `PUT`/`DELETE …/avatar` | **retire from `api.v1`** — drop the paths, reconcile the seal in a `lane:contract` PR | No bot handler, no named demand, and the purest expression of the shape being replaced. Retiring is cheaper than serving and honest either way. |

Chat **write** therefore has two doors on purpose (`/acts` and the `/chat` alias) and chat **read**
has one. That asymmetry is deliberate: a write is an act, a read is not.

**5. Live chat read keeps the `ws.v1 ChatMessage` carrier and gets it a producer** from the same sink
that already feeds the transcript (`capture-bridge.ts:777` → `index.ts:251`). The alternative — adding
`source` to `ws.v1`/`api.v1 TranscriptionSegment` (neither carries it today) so chat rides the
transcript stream — is a wider contract change for a capability that already has a dedicated sealed
shape.

**6. The conformance gate must assert reachability, not registration**, before any of the above is
provable. This is item one of the work order, not a follow-up.

## Trade-off

One endpoint means the wire body is a discriminated union rather than a path-per-capability, so a
generated client gets a single `acts` operation instead of five typed ones, and OpenAPI-driven tooling
sees less structure. We accept that: the sealed-vs-served ledger has cost us more than typed-per-path
generation has earned, and the `/speak` and `/chat` aliases keep the two paths with named consumers
typed.

Retiring `/screen` and `/avatar` breaks any 0.10-era caller still coding against them. No such caller
is named in the demand register; the routes 404 today, so the break is from *unserved* to *absent*.

## Consequences

- **#514 lands unchanged in substance** — it *is* the missing publisher; it becomes the `/acts` route
  plus `/speak` sugar rather than a bespoke speak backend. Its `voice_agent_enabled` hop is now a hard
  dependency of any speak claim, not a footnote.
- **#500 splits along a seam it already has**: the Teams chat *read* repair is capture-module work,
  untouched by this decision; the *write* half becomes the `chat_send` bot handler reached through
  `/acts`.
- **#541's sealed-vs-served reconcile discipline** applies to the retirement PR.
- The honest-status page's speak row is corrected in the same change: "one publisher away" understated
  the chain by two hops.
