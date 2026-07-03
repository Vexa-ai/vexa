# tests — the O-API-1 behavioral evals (ride gate:python)

- `test_api_surface.py` — api.v1: drives all 10 CORE paths through the TestClient gateway
  + port-fake meeting-api; asserts 2xx + body conforms to its sealed
  `#/components/schemas/<Shape>`; auth-negative (no key → 401), invalid key → 401,
  insufficient scope → 403; on-disk goldens conform; sealed identity is main 1.5.0.
- `test_ws_protocol.py` — ws.v1: replays subscribe→`Subscribed` ack, forwarded redis
  payloads → `TranscriptionSegment`/`BotStatus`/`ChatMessage` frames, malformed →
  `Error` frames, missing key → `missing_api_key` + close 4401; on-disk goldens conform.

Run: `cd v0.12/gateway/services/conformance && uv run pytest -q`.
