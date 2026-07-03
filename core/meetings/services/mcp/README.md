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
| calls | gateway (`GATEWAY_URL`) | `POST /bots` · `GET /bots/status` · `PUT/DELETE /bots/{platform}/{native}` · `GET /meetings` · `GET /transcripts/{platform}/{native}` · `GET /recordings[/{id}]` | each tool forwards verbatim with the caller's `X-API-Key` |

## Tools (9)

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

## Gateway exposure

0.10.6's api-gateway forwarded `/mcp` to this service (buffered `api_route` catch-all). The
v0.12 gateway is a deliberate port-injected carve with an explicit route table and no
generic reverse-proxy seam for SSE-flavoured MCP traffic, so this port does **not** patch
the gateway: the MCP service is reachable **directly on its own port** (compose:
`127.0.0.1:${MCP_HOST_PORT:-18010} → 8010`). Auth is still fail-closed — the service itself
holds no credentials; every call is authorized by the gateway with the caller's key.
Fronting `/mcp` at the gateway edge can be added later as a streamed forward
(`_forward_stream`) once wanted.

Client config (e.g. Claude Desktop):

```json
{
  "mcpServers": {
    "Vexa": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "http://localhost:18010/mcp",
               "--header", "Authorization: Bearer ${VEXA_API_KEY}"]
    }
  }
}
```

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

- ✅ delivered — 9 tools + 4 prompts over the v0.12 public API, streamable-HTTP `/mcp` mount
- ✅ delivered — auth passthrough (Bearer / raw Authorization / X-API-Key → gateway `X-API-Key`)
- ✅ delivered — compose service (`mcp`, port 8010) + healthcheck
- ⬜ planned — gateway-fronted `/mcp` (streamed forward at the edge)
- ⬜ planned — the blocked tool set above, as the REST routes reach parity
