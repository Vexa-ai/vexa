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
- **`tickets.py`** — `report_issue`'s ticket: field bounds, the canonical summary/description,
  content and caller fingerprints (never the key), and the sink's wire shape (`raw` or `github`).
- **`manifest.py`** · **`discover.py`** · **`bind.py`** · **`register.py`** — the assembly: each
  deployed domain's `mcp.tools.v1` manifest validated, discovered over HTTP, bound to the domain's
  own OpenAPI, and registered as one route per tool (a domain that declares `forward` is called
  back through the gateway).
- **`reentry.py`** — carries the identity the gateway signed onto a request back to the gateway on
  the tool calls that request causes.
- **`notices.py`** · **`tool_errors.py`** · **`streamable_http.py`** · **`identity.py`** ·
  **`config_preflight.py`** — standing notices on meeting tools, structured tool refusals, the
  streaming `/mcp` transport, the meeting-identity vocabulary, and the vendored config preflight.

Stateless by design — no DB, no redis, no direct meeting-api/admin-api access; the only
outbound seams are the gateway REST surface, the assembled domains' doors, and the
operator's ticket sink.
