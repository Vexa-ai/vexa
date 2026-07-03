# tests — meeting-status-ws (L3 SEAM)

`test_seam_meeting_status.py` proves the user-scoped `meeting.status` WS feature end-to-end over
**in-memory fakes** (no docker/DB/live redis): drive the REAL meeting-api intent endpoint → capture
the frame it publishes to `u:{user_id}:meetings` → feed it through the REAL gateway `/ws` user-scope
forward path → assert a faked socket receives it UNCHANGED and `ws.v1`-golden-conforming.

`conftest.py` puts both services' `src` trees + the gateway's test-fakes on `sys.path`;
`gateway_fakes.py` re-exports `FakeRedis`/`FakeAuthorizer`. Run via `uv run pytest -q` (driven by
`gate:python` — this package is discovered now that the tests live under `tests/`).
