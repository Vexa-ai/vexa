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
