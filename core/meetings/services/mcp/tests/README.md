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
- **`test_standing_notices.py`** — the ride: a standing notice reaches an agent on the
  result of the meeting tool it just called (field in the body, trailing line in the text,
  once per result) — including when that result is a REFUSAL (#1549) — the tools that touch
  no meeting carry none, and every way that hop can fail — domain absent, slow, refused,
  malformed — leaves the result, or the refusal, exactly as it was.
- **`test_tool_annotations.py`** — #1646 contract: `tools/list` over streamable HTTP (built-ins +
  the repo's flows manifest) gives every tool a title and explicit readOnly/destructive/
  idempotent/openWorld hints, names ≤ 64 chars, no shipped tool on the cautious fallback; the
  classification; manifest annotations win over the registry; malformed ones refuse validation.
