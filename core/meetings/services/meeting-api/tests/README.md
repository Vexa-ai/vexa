# tests — meeting-api (L1 of the validation pyramid)

`uv run pytest -q` (driven by `gate:python`). Autonomous — no docker, no meeting, no network.
`conftest.py` provides the lifecycle.v1 goldens (loaded by path), a fake in-memory webhook
receiver, and a `fakeredis.aioredis` client.

| File | Level | Proves |
|---|---|---|
| `test_recording_golden.py` | L1 conformance | the Python `build_recording_master` reproduces every recording.v1 golden (loaded **by path** from `meetings/modules/recording/src/contracts/golden/`) byte-for-byte — same length + same sha256 as the Node twin. |
| `test_zaki_retention.py` | L2 unit | owner-scoped transcript/summary/object erasure returns a content-free receipt and leaves a second tenant unchanged. |
| `test_lifecycle_machine.py` | O-MTG-1 | the lifecycle.v1 goldens drive the FSM in legal order → correct transitions + terminal attribution (`failure_stage` server-derived); illegal transitions (`active→joining`, terminal re-open, active-first) are rejected. |
| `test_lifecycle_http.py` | O-MTG-1 | over FastAPI `TestClient`: POST each golden → `200 accepted`, FSM advances; posted events re-conform to the sealed schema (`_conforms`); illegal → `409`; malformed → `422`; `/health` live. |
| `test_webhook_signing.py` | O-MTG-2 | a verifier recomputing HMAC over `ts.payload` accepts a valid sig, rejects tampered (body/ts/secret/missing); built envelope + headers + `webhook.v1` goldens conform. |
| `test_webhook_delivery.py` | O-MTG-2 | 200→`delivered`; 500→`queued`→worker-sweep drains→`delivered`; unsubscribed per-client event `suppressed` (no HTTP); system scope ignores the filter; backoff respected; exhausted schedule drops. |
| `test_webhook_ssrf.py` | O-MTG-2 | localhost / loopback / link-local / private CIDRs / internal hostnames / non-http schemes / DNS-rebinding-to-private are blocked; public targets pass; the sink short-circuits a blocked URL without touching the transport. |
