# Code Review — 260508-v0.10.6.1

Generated: 2026-05-14T06:47:29Z
Stage observed: develop-code
Reviewer: Codex

Update: 2026-05-14T07:03:40Z — the three P1 findings below have been patched
and re-verified locally. The P2 findings remain hardening follow-ups unless
deployment topology raises their severity.

Patch evidence:

- Dashboard playback proxy no longer re-queries `/recordings/{id}` or selects
  from `media_files[]`; it consumes the canonical URL returned by backend
  `/recordings/{id}/master`.
- `/api/agent/*` now requires a valid dashboard user session, allowlists agent
  route roots, and rewrites `user_id` to the authenticated user before
  forwarding with the service token.
- Direct login is no longer implied by missing SMTP. It is allowed only when
  `VEXA_ALLOW_DIRECT_LOGIN=true` or on localhost/127.0.0.1; compose/lite set
  the flag explicitly so `test@vexa.ai` remains a one-step local validation
  login.

Verification:

- `npx eslint 'src/app/api/vexa/[...path]/route.ts' 'src/app/api/agent/[...path]/route.ts' 'src/app/api/auth/send-magic-link/route.ts' 'src/app/api/health/route.ts'` — pass.
- `npm run sync-packages && npm run build` in `services/dashboard` — pass.
- `bash tests3/tests/static/dashboard-playback-canonical.sh` — pass.
- `bash tests3/tests/dashboard-auth.sh` against compose dashboard — pass; `test@vexa.ai` direct local login preserved.
- Unauthenticated `GET /api/agent/sessions?user_id=1` against compose dashboard — HTTP 401.
- `STATE=tests3/.state-compose bash tests3/tests/smoke-bot-transcription-roundtrip.sh roundtrip` — HTTP 200 + 1 segment.
- `STATE=tests3/.state-compose bash tests3/tests/live-bot-transcript-pipeline.sh` — meeting 10099: 6 chunk(s), 17 segment(s).
- `STATE=tests3/.state-lite bash tests3/tests/smoke-bot-transcription-roundtrip.sh roundtrip` — HTTP 200 + 1 segment.
- `STATE=tests3/.state-lite bash tests3/tests/live-bot-transcript-pipeline.sh` — meeting 182: 6 chunk(s), 11 segment(s).

Scope of this pass: security and best-practice audit of the current
working-tree release state, with special attention to dashboard auth
boundaries, recording playback correctness, media storage paths, and release
harness guarantees.

## Findings

### [P1] Dashboard agent proxy exposes service-token agent API without user auth

File: `services/dashboard/src/app/api/agent/[...path]/route.ts`
Lines: 26-96

The dashboard `/api/agent/*` route is a generic proxy to `AGENT_API_URL` that
adds the server-side `AGENT_API_TOKEN` on every forwarded request. GET, PUT,
DELETE, and non-chat POST never require a dashboard user session. The chat path
does read the auth cookie, but if it is absent it injects `bot_token = ""` and
still forwards the request with the service token.

This turns a browser-reachable dashboard API into a bearer of internal
agent-api privileges. The upstream agent-api token gates sensitive operations:
session reset/delete, workspace upload/delete/read/write, schedule bridge, and
workspace file access. Client-side route guards do not protect server route
handlers. Any unauthenticated caller that can reach the dashboard route can ask
the server to authenticate to agent-api on their behalf.

Required fix: require a valid dashboard user session before every method on
this proxy; reject chat when the user token is missing; stop exposing a generic
path proxy and allowlist only the exact agent endpoints needed by the UI. Add
negative tests proving unauthenticated GET/POST/PUT/DELETE to `/api/agent/*`
return 401 and never call agent-api.

### [P1] Missing SMTP falls back to unauthenticated direct login and returns API tokens

File: `services/dashboard/src/app/api/auth/send-magic-link/route.ts`
Lines: 84-168, 205-212

When SMTP is not configured, the magic-link endpoint enters direct-login mode.
For any submitted email that passes registration policy, it finds or creates
the user, creates a user API token, sets auth cookies, and returns the token in
the JSON response. No email verification, OAuth assertion, or explicit dev-only
feature flag is required.

This is a production footgun: a self-hosted or staged dashboard with
`VEXA_ADMIN_API_KEY` configured but SMTP missing becomes account-token minting
by email address. `/api/health` also documents this as the default auth mode
when no OAuth/SMTP provider is configured, so this is not merely hidden test
code.

Required fix: make direct login opt-in via an explicit unsafe/dev flag that is
refused in hosted/stage/prod profiles; default missing SMTP to login disabled
or provider-required. Do not return bearer API tokens in auth responses when an
HTTP-only cookie has already been set unless an explicit API-key reveal flow is
being used. Add tests for SMTP-missing behavior in production-like envs.

### [P1] Dashboard proxy reintroduces media_files master selection

File: `services/dashboard/src/app/api/vexa/[...path]/route.ts`
Lines: 125-181

The `proxy=1` playback path first calls the canonical backend
`/recordings/{id}/master?type=...`, but then discards the backend's selected
master and re-queries `/recordings/{id}` to pick `(recording.media_files ||
[]).find(mf => mf.type === mediaType)`.

That is the exact class ADR-2 is meant to remove: dashboard-side selection
from `media_files[]`. It is also unsafe because the public
`RecordingResponse.media_files` schema can omit the metadata needed to
distinguish a master entry from a chunk entry. For historical duplicate media
arrays, or any array where the first same-type media file is not the finalizer
master, the human browser can again stream chunk 0 / a non-master object even
though the backend canonical master endpoint selected the right artifact.

Required fix: keep selection on the backend side. Either make
`/recordings/{id}/master` return enough stable data for the dashboard proxy to
stream the exact selected master (`media_file_id` or a proxy-safe internal raw
route), or add a backend endpoint that streams the canonical master directly
with Range support. Harden the static prove to reject any `media_files.*find`
selection under `services/dashboard/src`, including server routes, and add a
fixture where a chunk precedes the master.

### [P2] Internal recording upload lacks service auth and reads unbounded bodies into memory

File: `services/meeting-api/meeting_api/recordings.py`
Lines: 121-207

`/internal/recordings/upload` is mounted directly on meeting-api and accepts
uploads with only a `session_uid` lookup. It has no service-auth dependency,
then reads the entire uploaded body with `await file.read()` before applying
any size cap or streaming behavior. A caller with network access to meeting-api
can attach media to any known session UID and can drive memory/storage pressure
with large uploads.

The endpoint is intended for internal bot traffic, but the code relies on
network topology rather than an application-level boundary. That is brittle for
compose/helm/proxy drift, exactly the kind of SSOT/env split this release has
been exposing.

Required fix: add internal service authentication for bot/runtime upload
traffic, enforce request/body size limits before reading into memory, stream
uploads where possible, and validate `media_type`, `media_format`, and
`chunk_seq` against a small allowlist/range before using them in storage keys.

### [P2] Sweeper can repeatedly scan an entire user's recording bucket prefix

File: `services/meeting-api/meeting_api/sweeps.py`
Lines: 375-425

The unfinalized-recordings sweep selects the latest terminal meetings and, for
each session missing JSONB recording metadata, lists `recordings/{user_id}/`
from object storage. That prefix is user-wide, not scoped to the meeting or
session. For a heavy user, every sweep can repeatedly list a large object set
just to filter locally by `session_uid`.

This is a reliability and cost risk rather than a direct exploit. It can also
hide real finalization problems under storage latency, especially in production
where object listings are slower and paginated.

Required fix: narrow candidates before touching storage, add a durable
last-swept/backoff marker, and avoid user-wide object listings. Prefer a
session-scoped index, explicit upload metadata, or a bounded prefix derived
from known recording/session state.

### [P2] Raw media endpoint parses Range headers unsafely after full download

File: `services/meeting-api/meeting_api/recordings.py`
Lines: 533-555

The legacy `/raw` media endpoint downloads the full object into memory and then
parses `Range` manually. Malformed values such as non-integer bounds can raise
`ValueError` and produce a 500, and unsatisfiable ranges are not returned as
416. This path should be a fallback, but the current dashboard proxy still uses
it for playback.

Required fix: avoid `/raw` for dashboard playback after the canonical master
fix, and harden the endpoint anyway: validate range syntax, return 416 for
unsatisfiable ranges, and avoid full-object memory loading for large media.

## Positive Notes

- Local storage now rejects path traversal after normalization in
  `services/meeting-api/meeting_api/storage.py`.
- Dashboard `/api/vexa/*`, webhook config, and profile key routes generally
  resolve user identity from the server-side auth cookie instead of trusting a
  client-supplied user id.
- The release harness now contains explicit SSOT/env checks for transcription
  URL resolution and browser-reachable media paths; those should remain
  principle checks, not meeting-id-specific assertions.
- GHSA-9wv6-78fw-fq5c is now in release scope. The dashboard PostCSS lockfile
  resolves to `8.5.10`; transcription-service no longer installs
  `python-multipart` and its upload route uses a bounded standard-library
  multipart parser.

## Open Questions

- Should direct login exist at all outside local developer machines? If yes,
  it needs a visibly unsafe feature flag and a registry test that production
  profiles reject it.
- Is meeting-api ever reachable outside the service mesh/compose network in
  any supported deployment? If the answer is "no", the internal-upload finding
  remains defense-in-depth; if the answer is "sometimes", it becomes a release
  blocker.

## Residual Risk

This audit focused on the changed release surface plus obvious auth/storage
boundaries. The pulled-in advisory dependency class now has a registry proof
and local release-gate evidence. Remaining P2 findings should be carried as
hardening work unless deployment topology exposes meeting-api directly.
