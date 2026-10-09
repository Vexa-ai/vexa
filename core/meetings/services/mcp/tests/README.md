# tests — mcp service (autonomous, in-process)

`uv run pytest -q`. No docker, no network: `conftest.py` injects a fake gateway behind
`httpx.MockTransport` into `create_app(...)`, so every test drives the SHIPPED forwarding
path and the fake records each hop (method, path, headers, params, body).

- **`test_health.py`** — gate:health: `/health` → 200 `{status:"ok", service:"mcp"}`,
  reachable without a credential and without a gateway hop.
- **`test_mcp_surface.py`** — L1: the derived MCP surface is exactly the ported tool set,
  the prompt catalog is complete, and prompts reference only ported tools.
- **`test_parse_meeting_link.py`** — L2 unit: `parse_meeting_url` goldens ported from
  0.10.6 (platform / native id / passcode extraction).
- **`test_app.py`** — L3 seam: every tool forwards to the right gateway path with the
  caller's `X-API-Key`; missing key fails closed (401); downstream status + detail pass
  through verbatim (incl. the 409 → `already_exists` shape).
- **`test_path_segments.py`** — a caller's value (`/`, `.`, `..`, `%2F`, `?`, `#`) is exactly ONE
  segment of the URL a hand-written tool or an assembled tool actually sends, with no query or
  fragment grafted on; an unknown `platform` is refused before any hop.
- **`test_callback_routes.py`** — drives every tool that reaches the gateway and holds the routes
  it called equal to the `routes.v1` rows flagged `mcp_reentry` (the only rows on which the gateway
  admits a worker's token on this service's re-entry).
- **`test_standing_notices.py`** — the ride: a standing notice reaches an agent on the
  result of the meeting tool it just called (field in the body, trailing line in the text,
  once per result) — including when that result is a REFUSAL (#1549) — the tools that touch
  no meeting carry none, and every way that hop can fail — domain absent, slow, refused,
  malformed — leaves the result, or the refusal, exactly as it was.
