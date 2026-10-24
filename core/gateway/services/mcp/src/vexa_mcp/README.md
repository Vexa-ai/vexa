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
- **`calendar_tools.py`** · **`workspace_tools.py`** — `calendar_status`, `calendar_connect`,
  `set_auto_join` and `meeting_bind_workspace`: compositions of public routes, registered from
  `create_app` with one call each, with their MCP annotations (`ANNOTATIONS`, the #1852 shape).
- **`features.py`** — `VEXA_FEATURES` at the edge, read at boot: `browser_bots` off ⇒ the three
  bot-account tools are not registered; `crm` off ⇒ no assembled `crm` tool is. Empty (the release
  default) is both off; `all` is both on. The parse is agent-api's, copied.
- **`reentry.py`** — carries the identity the gateway signed onto a request back to the gateway on
  the tool calls that request causes.
- **`obs.py`** — `logevent.v1` lines and the `X-Trace-Id` carried from the inbound request onto
  every outbound hop, including the tool call `FastApiMCP` replays in-process.
- **`paths.py`** — `path_segment`, the one place a caller-supplied value becomes a path segment:
  everything but the unreserved characters percent-encoded, `/` included, and a dot-only value
  encoded too, so no value can end its segment, add a query or fragment, or be resolved as `..`.
- **`visibility.py`** — which tools a caller is shown and may call. The settings
  `VEXA_MCP_TOOLS_ALLOW`, `_DENY` and `_COHORT` apply to ordinary callers. D3 applies to every shared
  caller (a room's or a shared chat's delegation, or a workspace principal, `x-user-principal-kind: workspace`): it is shown only the
  tools whose domain manifest says `shared: allow`, and nothing from a domain that has not declared.
  This is always on, cohort included.
- **`notices.py`** · **`tool_errors.py`** · **`streamable_http.py`** · **`identity.py`** ·
  **`config_preflight.py`** — standing notices on meeting tools, structured tool refusals, the
  streaming `/mcp` transport, the meeting-identity vocabulary, and the vendored config preflight.

Stateless by design — no DB, no redis, no direct meeting-api/admin-api access; the only
outbound seams are the gateway REST surface, the assembled domains' doors, and the
operator's ticket sink.
