# tests — mailroom (autonomous, in-process)

`uv run pytest -q`. No docker, no network, no Mailpit, no gateway: `conftest.py` injects a fake
mailbox and a fake control plane behind the service's ports, so every test drives the SHIPPED
decision path and the fake records exactly what the mailroom asked the control plane to do.

- **`test_health.py`** — gate:health: `/health` → 200 `{status:"ok", service:"mailroom"}`, no
  credential, no hop, and an honest `ingest.configured=false` when the mailbox is unconfigured.
- **`test_access.py`** — gate:access: `/internal/*` is default-deny when a secret is configured,
  open (deliberately, asserted) when it is not, and 503 rather than pretending when there is no
  mailroom at all.
- **`test_invite.py`** — L1/L2 the pure parser: MIME shapes, both calendar flavours, `SEQUENCE`,
  `RRULE`/`EXDATE`, floating-time refusal, roster roles, and never-raises-on-garbage.
- **`test_oracle_corpus.py`** — L2 the 22-fixture invitation corpus (`fixtures/ics/oracle/`)
  replayed row by row and chain by chain: create → update → cancel, stale replay, idempotency,
  second-occurrence attendance. **This file is the specification in executable form.**
- **`test_corpus.py`** — L2 the local corpus (`fixtures/ics/`, `fixtures/eml/`): the cases the
  oracle does not cover — Zoom, Teams short links, plus-tagged addresses, `METHOD:REPLY`, a
  missing `UID`, real multipart MIME, and an ordinary non-invitation email.
- **`test_service.py`** — L2/L3 the loop's properties: idempotency, cursor resume, durable store,
  fail-safe on a refusing control plane, re-binding after a cancel, multi-workspace resolution.
- **`test_adapters.py`** — L3 seam: Mailpit paging/cursor/raw-fetch and the public-API calls
  (paths, `X-API-Key`, bodies, status translation) against `httpx.MockTransport`.
- **`test_meeting_link.py`** — L2 goldens the vendored link parser shares with meeting-api's copy.
- **`test_config.py`** — L1 environment → `Settings`, and the composition root's degrade path.
