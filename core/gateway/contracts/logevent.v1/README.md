# logevent.v1 — the structured log envelope (observability SSOT)

Every control-plane service emits **one JSON object per log line**, conforming to
`logevent.schema.json`. This contract is the **shared seam** for observability — the
single source of truth for log shape, the **distributed `trace_id`**, and the
**user-vs-system `audience`** split. Services do **not** import shared logging code; each
ships its own ~20-line emitter that conforms to THIS schema (the contract is the SSOT, not
a library — keeps the import-boundary gate clean across domains).

## The envelope (`#/$defs/LogEvent`)
| field | req | what |
|---|---|---|
| `ts` | ✓ | RFC3339 UTC timestamp |
| `level` | ✓ | `debug \| info \| warning \| error \| critical` |
| `service` | ✓ | emitter: `gateway \| meeting-api \| runtime \| …` |
| `trace_id` | ✓ | **the distributed correlation key** — minted at the edge if `X-Trace-Id` is absent, then forwarded downstream verbatim so **every hop shares it** |
| `span` | | per-hop/operation id within the trace |
| `user_id` | | the end user (post-auth), string or int |
| `meeting_id` | | the meeting/session, when applicable |
| `session` | | the **agent unit** (a chat or a background job) that caused the line — set only from a signed source (the delegation token's `unit` claim, via identity's validate answer or the gateway's signed identity), never from a client header; equals the credential broker's `audit.session` |
| `audience` | ✓ | **`user`** (end-user-facing) or **`system`** (operator/debug) |
| `event` | ✓ | stable low-cardinality snake_case name |
| `fields` | | free-form structured context (method, path, status, error, …) |

## The trace
A request enters the **gateway** (the edge). The trace middleware reads `X-Trace-Id`; if
absent it **mints one**, binds it to a `contextvars` ContextVar, and **forwards it
downstream** via the `X-Trace-Id` header. Every service behind it reads that header, binds
the same id, and forwards it on its own outbound calls. Result: a single request's log lines
across every hop **all carry the same `trace_id`**.

The hops that carry it: gateway · meeting-api · runtime · agent-api · the agent worker ·
the MCP service · the credential broker · identity (admin-api) · flows-api · flows-worker ·
the mailbox. agent-api hands a dispatched turn's trace id to its worker (on the signed
input-stream entry, or `VEXA_TRACE_ID` at a cold spawn); the worker sends it as `X-Trace-Id`
on its MCP attachment, so the worker's tool calls continue the trace that started the turn.

## The session
A trace is one request. An agent **session** — one chat thread, or one background job — is
many: every turn and every tool call. agent-api mints each worker's delegation token
(`delegation.v1`) with the unit id as its `unit` claim; identity answers it, and the gateway
signs it into the identity it forwards (`gateway-identity.v1` `delegation.unit` →
`x-user-delegation-unit`). Services that hold a verified identity log it as `session`, and
the credential broker records it as `audit.session`. A worker cannot name another session:
the claim is inside a token only agent-api can mint.

> **One remaining hop (follow-on):** the **bot/pipeline** leg is owned by the concurrent
> telemetry stream and is OUT OF SCOPE here. Propagating `trace_id` into the bot (over the
> recording/lifecycle seam) is the one hop left to thread.

## Goldens (`golden/`)
- `user-bot-joined.json` — a **user-facing** event (`audience=user`).
- `system-downstream-forwarded.json` — a **system/debug** event (`audience=system`, `level=debug`).
- `error-downstream-unreachable.json` — an **error** event (`audience=system`, `level=error`).
- `system-worker-tool-call.json` — a worker's tool call reaching the gateway, carrying the
  agent `session` it belongs to.

All share one `trace_id` to illustrate a cross-hop trace.

## Gates
- `node validate.mjs --check` (rides **gate:schema**) — every golden conforms to `LogEvent`,
  and the prefix→audience invariants hold (`user-*`⇒user, `system-*`⇒system, `error-*`⇒error).
- The cross-service trace eval rides **gate:python** / the future **gate:tracing**
  (`gateway/services/conformance/tests/test_tracing.py`): one minted id, forwarded, present
  on every emitted line; non-conformant lines detected and failed.

## Sealing
A published `.vN` is frozen by `contracts.seal.json` (gate:contract-version). While in
development it is reported as *unsealed*; seal with `node scripts/gates.mjs seal` on a
`lane:contract` human-reviewed PR.
