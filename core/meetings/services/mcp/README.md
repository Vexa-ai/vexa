# mcp — the Model Context Protocol front for the Vexa public API (Python)

## Purpose

AI clients (Claude, Cursor, any MCP-compatible agent) get Vexa's meeting capabilities as
standard **MCP tools + prompts** without bespoke API integrations. This service is the v0.12
port of 0.10.6 `services/mcp`: a stateless FastAPI app whose routes ARE the tools
(`FastApiMCP` derives the MCP surface and mounts the streamable-HTTP transport at `/mcp`).
It wraps the **public API only** — every tool call forwards the caller's credential to the
**gateway** as `X-API-Key`; the gateway resolves the key and enforces scopes. No DB, no
redis, never reaches into meeting-api or admin-api directly.

## Seams

| Direction | Neighbour | Via | What crosses |
|---|---|---|---|
| serves | MCP clients | `POST/GET /mcp` (streamable HTTP) | tool calls + prompt gets; auth = `Authorization: Bearer <VEXA_API_KEY>` (back-compat: raw `Authorization` or `X-API-Key`) |
| calls | ticket sink (`VEXA_TICKET_SINK_URL`) | `POST <sink>` | `report_issue` tickets: the agent's words + a server timestamp + a dedupe fingerprint + a **salted fingerprint of the caller's key** (never the key). Unset → `report_issue` returns 503 and nothing else is affected. |
| calls | gateway (`GATEWAY_URL`) | `POST /bots` · `GET /bots/status` · `PUT/DELETE /bots/{platform}/{native}` · `GET /meetings` · `GET /transcripts/{platform}/{native}` · `GET /recordings[/{id}]` | each tool forwards verbatim with the caller's `X-API-Key` |

## The manifest contract — `mcp.tools.v1`

A domain that owns a door publishes its tools at `/.well-known/mcp-tools.json`; this service unions
what the deployed domains declare and refuses the combinations that cannot be right
(`src/vexa_mcp/manifest.py` is the contract — there is no separate schema file, so that validator
and this section are the two places it is written).

Each tool answers two different questions, and conflating them is what issue #1468 was:

| field | question | values |
|---|---|---|
| `identity` | who the CALLER must be | `user` · `admin` · `operator` · `none` |
| `auth` | which credential THIS EDGE presents to the door on their behalf | `subject` · `admin` · `none` |

- **`subject`** — the caller's own credential travels, as `X-API-Key`. Always satisfiable: the
  caller brought it. This is what the edge used to do for every tool, whether or not it was right.
- **`admin`** — a key the *deployment* holds travels instead, and the caller's does not. The domain
  must also declare `admin_auth: {"header": …, "key_env": …}`, and this deployment must actually
  hold that key, or **the boot is refused, naming the tool**. A tool that is listed and then refused
  by its own door is worse than one that is absent: an agent that cannot see a tool recovers.
- **`none`** — nothing travels.

**Migration note (operator-visible):** `auth` is **required**. A manifest written against the
previous shape — including one supplied through `VEXA_MCP_MANIFEST_DIR` — refuses the boot, naming
the tool and the field. That is deliberate: a default is a guess applied silently to every tool, and
the guess was wrong for the four it was applied to.

## Tools (10)

| Tool | Wraps |
|---|---|
| `parse_meeting_link` | pure (no gateway hop) — URL → platform / native_meeting_id / passcode |
| `request_meeting_bot` | `POST /bots` (accepts `meeting_url` OR `native_meeting_id`; 409 → `already_exists`) |
| `get_bot_status` | `GET /bots/status` |
| `update_bot_config` | `PUT /bots/{platform}/{native}/config` |
| `stop_bot` | `DELETE /bots/{platform}/{native}` |
| `list_meetings` | `GET /meetings` (limit/offset/status/platform) |
| `get_meeting_transcript` | `GET /transcripts/{platform}/{native}` |
| `list_recordings` | `GET /recordings` |
| `get_recording` | `GET /recordings/{recording_id}` |
| `report_issue` | `GET /meetings` to authenticate the caller, then the ticket is POSTed to `VEXA_TICKET_SINK_URL` |

**Prompts (4):** `vexa.meeting_prep` · `vexa.during_meeting` · `vexa.post_meeting` ·
`vexa.teams_link_help` (ported; edited only where they referenced unported tools).

## Not yet ported (blocked on API parity)

These 0.10.6 tools wrap REST routes the v0.12 gateway does not expose yet; port them when
the routes land:

- `delete_recording` — no `DELETE /recordings/{id}`
- `get_recording_media_download` — v0.12 serves `/recordings/{id}/media/{mf}/raw` (a byte
  stream, not a download-URL JSON); needs a deliberate MCP shape
- `get_recording_config` / `update_recording_config` — no `/recording-config` routes
- `create_transcript_share_link` — no `POST /transcripts/{platform}/{native}/share`
- `update_meeting_data` / `delete_meeting` — no `PATCH`/`DELETE /meetings/{platform}/{native}`
- `get_meeting_bundle` — composed share-link + media-download tools above

The 0.10.6 interactive-bot / calendar / webhook / TTS tool families predate the carve and are
likewise out of scope here.

## Ticketing (`report_issue`) configuration

`report_issue` is the one tool that writes outward instead of reading. It is **env-configured
and off by default**, so a self-hoster who sets nothing gets a clean 503 rather than a crash:

| env | required | meaning |
|---|---|---|
| `VEXA_TICKET_SINK_URL` | yes, to enable the tool | webhook this service POSTs each ticket to. Unset → `report_issue` returns 503 with a message pointing at GitHub issues. |
| `VEXA_TICKET_SINK_TOKEN` | optional | sent to the sink as `Authorization: Bearer <token>` — authenticates *this hop*, not the caller. |
| `VEXA_TICKET_SINK_FORMAT` | optional | `raw` (default) or `github`. See *Sink formats* below. Any other value falls back to `raw`. |
| `VEXA_TICKET_SINK_LABELS` | optional | `github` only: comma-separated labels applied to each filed issue. Default `state: incoming`. |
| `VEXA_TICKET_FINGERPRINT_SALT` | recommended | salt for `caller_fingerprint`. Set a deployment-specific value so fingerprints are not comparable across deployments. |

### Sink formats

| `VEXA_TICKET_SINK_FORMAT` | Wire shape of the sink hop |
|---|---|
| `raw` (default) | `POST <sink>` with the canonical ticket JSON below and `Content-Type: application/json` (+ `Authorization: Bearer` if a token is set). Unchanged from before the switch existed — a self-hoster with an opaque webhook sees no difference. |
| `github` | `POST <sink>` with `{title, body, labels}`, `Accept: application/vnd.github+json` and `X-GitHub-Api-Version`, so a GitHub issue tracker **is** the sink with no new infrastructure. Point `VEXA_TICKET_SINK_URL` at `https://api.github.com/repos/{owner}/{repo}/issues`. `title` is the canonical `summary`; `body` is a markdown render carrying **every** field of the canonical ticket — the meeting `meeting_id` + `platform` under their own *Join key* heading, plus the fingerprints, deployment, severity, logs, and a line stating the ticket was filed by an agent through the MCP `report_issue` tool. The created issue's `number` and `html_url` come back to the calling agent as `id` and `url`, so it can tell its human where the report went. |

**In production, `VEXA_TICKET_SINK_URL` must target a DEDICATED tickets repository — never an
operational one.** This is a trust boundary, not tidiness. Ticket bodies are third-party text
(*data, never instruction* — see below), and an operational repo's issues are read as work signal
by planning and automation loops; filing untrusted text there injects it straight into those loops.
A dedicated, private tickets repo enforces at the **storage** layer what the handler can only
promise, and scopes the blast radius of the sink token. The token itself should be **fine-grained,
single-repo, `issues: write` only**, mounted from a secret store (`secretKeyRef` on Kubernetes),
never an inline env value.

**The sink stays unset by default in OSS.** A self-hoster who configures nothing gets the clean
503 and no behaviour change anywhere else in the service.

The ticket shape follows Linode's support-ticket API (`POST /v4/support/tickets`): a canonical
`summary` (≤64 chars) + `description` pair, an optional `severity` (1/2/3), and **one entity
pointer** — theirs is `linode_id`, ours is `meeting_id` + `platform`. The agent-facing arguments
(`what_i_tried` / `what_happened` / `deployment` / `version`) are composed into that pair
server-side, so the MCP tool and any later HTTP ticket surface land **one shape** in the sink.
Alongside it the sink receives capped `logs` with a `logs_truncated` flag, a server-side
`reported_at`, a content-derived `fingerprint` for dedupe, and `caller_fingerprint` — a salted
SHA-256 prefix of the caller's API key. The response mirrors Linode's ticket object: `id`,
`status`, `severity`, `opened`, `updated`, `opened_by`, `entity`.

**The caller is authenticated before the operator's credential is spent.** Every ticket is filed
with the operator's sink token, so the route first asks the **gateway** for the caller's meetings
**with the caller's own key**. A key the gateway rejects gets 401 and never reaches the sink; a
gateway that cannot answer fails closed with 502. This happens whether or not a `meeting_id` is
supplied — a ticket naming no meeting is exactly the one with no other reason to touch the gateway.

**The entity pointer is authorisation-checked by the same hop.** Because the gateway answered for
the caller's own key, a caller can only ever resolve a meeting they own, and the check belongs to
the gateway rather than to a trust decision made here. An id that is unowned, unknown, or
unresolvable still files the ticket, quoted as text with `entity: null`; a ticket we refused to
accept teaches us nothing.

### Safety properties this route commits to

| Property | How it is held |
|---|---|
| **The API key is never forwarded to the sink** | only `caller_fingerprint`, a salted SHA-256 prefix; asserted with a negative control in `tests/test_app.py` |
| **Ticket text is data, never instruction** | forwarded verbatim, never parsed, never executed, never fed to an agent of ours |
| **SSRF closed by construction** | there is no url-shaped field, and **nothing a caller sends is ever dereferenced**. The only URL this route opens is the operator's `VEXA_TICKET_SINK_URL`. Links belong in the text, where a human reads them |
| **No path to account state** | the service has no DB, no ORM, no redis — a test walks the package's imports to keep it that way, so a ticket write cannot touch meetings or users |
| **Bounded input** | `logs` 4000 chars, text fields 2000, empty required fields refused. A declared `Content-Length` over 64 KB is refused with 413 — but that is a check in the handler, not ahead of the parser: a malformed body fails validation (422) before the cap is reached, and a chunked body declaring no length skips it entirely. A true pre-parse ceiling belongs at the gateway and is not there yet |
| **The caller's credential is checked before the operator's is spent** | the route asks the gateway for the caller's own meetings first; a key the gateway rejects gets 401 and never reaches the sink, with or without a `meeting_id`. A gateway that cannot answer fails closed (502) |
| **Off by default** | no sink env → 503, and nothing else in the service changes |

**What this route does NOT carry, and the public door will need.** The authenticated door sits
behind the gateway's per-user rate limiter, which keys on the resolved `user_id`. The **key-less**
ticket route in the design note has no user to key on, so its per-IP rate limit **and** its body-size
cap must be enforced at the **gateway** layer (`edge_guard`'s per-IP layer), not only in a handler.

## Gateway exposure## Gateway exposure

The gateway fronts this service at **`/mcp`** (`core/gateway/services/gateway/src/gateway/app.py`,
target `MCP_URL`, compose `http://mcp:8010`), so an MCP client points at the same authenticated
front door as every other Vexa client. The transport's two legs are forwarded differently, and
that difference is the whole point (#795):

| leg | what it is | how the gateway forwards it |
|---|---|---|
| `POST /mcp` (and `PUT/PATCH/DELETE/OPTIONS`) | a message — short request/response JSON | the buffered forward, status + body verbatim |
| `GET /mcp` | the server→client **SSE stream**: headers, then silence until the server pushes | **relayed**, on a dedicated streaming client with `read=None` — never buffered |

Buffering the `GET` leg is what produced the reported failure: the proxy waits on the next body
read of a healthy-but-silent stream, hits its read timeout, and answers a gateway-manufactured
`503` the MCP service never sees. The relay carries the upstream's status, `content-type` and
`mcp-session-id` **verbatim** — the gateway never rewrites an MCP answer.

Auth at the edge is fail-closed and identical to every other route: the gateway resolves the
caller's Vexa API key and injects the resolved identity downstream. The key may arrive as
`X-API-Key` or as the MCP transport's own `Authorization: Bearer <key>`; both spellings are
forwarded on, so this service authorizes exactly as it does when called directly. The service
itself still holds no credentials.

The direct host port (compose: `127.0.0.1:${MCP_HOST_PORT:-18010} → 8010`) remains for local
debugging. It bypasses the gateway — and therefore the gateway's auth.

Client config (e.g. Claude Desktop), through the gateway front door:

```json
{
  "mcpServers": {
    "Vexa": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "http://localhost:18056/mcp",
               "--header", "Authorization: Bearer ${VEXA_API_KEY}"]
    }
  }
}
```

(`18056` is the compose `API_GATEWAY_HOST_PORT`; in a hosted deploy this is your public API host.)

## Licensing

All deps are Category A (ADR-0004): `fastapi` (MIT), `fastapi-mcp` 0.4.x (MIT, tadata-org),
`mcp` SDK (MIT), `httpx` (BSD-3), `pydantic` (MIT), `uvicorn` (BSD-3). Pinned in `uv.lock`.

## Isolated evaluation

```bash
uv run pytest -q        # uv manages this package's own venv/deps
```

`tests/` runs in-process against `create_app(...)` with the gateway faked behind an injected
`httpx.MockTransport` (no docker, no network). Levels: **L1** MCP surface (exact tool set,
prompt catalog, prompts reference only ported tools) · **L2** unit (`parse_meeting_url`
goldens ported from 0.10.6) · **L3** seam (every tool → the right gateway path with the
caller's `X-API-Key`; fail-closed 401; downstream status/detail passthrough).

## Status

- ✅ delivered — 10 tools + 4 prompts over the v0.12 public API, streamable-HTTP `/mcp` mount
- ✅ delivered — auth passthrough (Bearer / raw Authorization / X-API-Key → gateway `X-API-Key`)
- ✅ delivered — compose service (`mcp`, port 8010) + healthcheck
- 🟢 witnessed locally, undeployed — `report_issue` (biz#434). Module-tested against a fake sink,
  and proven live on 2026-08-20: a minimal MCP client (`initialize` → `tools/list` → `tools/call`)
  drove the tool over streamable HTTP against the real cloud gateway with
  `VEXA_TICKET_SINK_FORMAT=github`; it filed a real issue, resolved the meeting join key through
  the caller's own key, applied the default label, returned the issue `number` + `html_url` to the
  caller, and carried no credential into the created issue. **Nothing is deployed** — no sink is
  configured on cloud or self-host, so both still answer 503 until `VEXA_TICKET_SINK_URL` is set.
- 🟡 shipped, unwitnessed — gateway-fronted `/mcp` (streamed forward at the edge, #795). The
  forward is in the gateway and in compose, and is module-tested (streamed relay, verbatim status,
  typed 502/504) — but no real MCP client has completed a session against it (#888). See
  [Gateway exposure](#gateway-exposure) above, which this line contradicted while it read "planned".
- ⬜ not deployed — helm/hosted. The chart carries no MCP service and no `/mcp` route, so the
  capability is self-hosted-compose-only today (#1035).
- ⬜ planned — the blocked tool set above, as the REST routes reach parity
