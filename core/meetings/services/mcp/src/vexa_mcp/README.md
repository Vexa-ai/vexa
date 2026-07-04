# vexa_mcp (package) — create_app · link parser · prompts

The MCP service logic, injectable. Public surface is `__init__.py`: **`create_app(...)`**
(+ the pure `parse_meeting_url` / `ParseMeetingLinkResponse`). Modules:

- **`app.py`** — `create_app(gateway_url, transport=...) -> FastAPI`: every ported tool is a
  thin FastAPI route; `FastApiMCP` derives the MCP tool surface from them and mounts the
  streamable-HTTP transport at `/mcp`. Auth extraction (`Bearer` / raw `Authorization` /
  `X-API-Key`) is fail-closed; the caller's key is forwarded verbatim to the gateway as
  `X-API-Key`. The gateway transport is an injected port (`httpx.MockTransport` in tests).
- **`link_parser.py`** — the pure meeting-URL parser (no gateway hop): URL → platform /
  `native_meeting_id` / passcode, behind the `parse_meeting_link` tool.
- **`prompts.py`** — the MCP prompt catalog (`vexa.meeting_prep` et al.), registered on the
  same `FastApiMCP` mount; prompts reference only ported tools.
- **`__main__.py`** — `python -m vexa_mcp`, the production entrypoint (compose CMD): serves
  `create_app()` with `GATEWAY_URL`/`HOST`/`PORT` from env.

Stateless by design — no DB, no redis, no direct meeting-api/admin-api access; the only
outbound seam is the gateway REST surface.
