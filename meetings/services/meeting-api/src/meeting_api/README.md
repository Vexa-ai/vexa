# meeting_api — the package (public surface = `__init__`)

The cloud control-plane core. Public surface is `meeting_api/__init__.py`:
`build_recording_master` + the `lifecycle` and `webhooks` subpackages.

| Module | Concern |
|---|---|
| `recording_codec.py` | the pure master codec — `build_recording_master` (front door) → format dispatch → WebM byte-concat / WAV RIFF header-merge. The Python twin of `recording-codec.ts`, drift-locked by the shared recording.v1 goldens. |
| `lifecycle/` | **O-MTG-1** — the lifecycle.v1 receiver + meeting-state machine. `LifecycleSink`/`MeetingStore` (the port + in-memory store), `create_app` (the FastAPI receiver), the `BotStatus` FSM. See `lifecycle/README.md`. |
| `webhooks/` | **O-MTG-2** — outbound delivery (system + per-client) behind `WebhookSink`: HMAC sign/verify over `ts.payload`, SSRF URL-guard, per-client event-filter, redis-backed exponential-backoff retry. The webhook.v1 wire shape. See `webhooks/README.md`. |

The recording codec is stdlib-only. `lifecycle` + `webhooks` add `fastapi`/`jsonschema`/`referencing`
(seam validation), `redis`/`fakeredis` (retry queue) — all pinned in `pyproject.toml`. Never imports
another domain's internals; contracts (`meetings/contracts/{lifecycle,webhook}.v1`) are loaded by path
(the legitimate seam).
