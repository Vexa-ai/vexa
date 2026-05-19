# Code Review Scope Map — 260508-v0.10.6.1

Generated: 2026-05-14T07:00:00Z
Source audit: `code-review.md`

Update: 2026-05-14T07:03:40Z — the two scope/deployment P1 blocker classes
were patched:

- Playback SSOT: fixed. Dashboard proxy uses backend `/master` selected URL and
  no longer reselects from `media_files[]`.
- Deployment auth: fixed for the audited dashboard surfaces. `/api/agent/*`
  requires a valid user session; direct login is explicit local/dev only while
  compose/lite preserve `test@vexa.ai` one-step validation via
  `VEXA_ALLOW_DIRECT_LOGIN=true`.

Purpose: map audit findings to the signed release scope so release handling is
clear: scope-owned blocker, scope-owned hardening, or adjacent security debt.

## Summary

| Finding | Scope mapping | Release consequence |
|---|---|---|
| P1 dashboard agent proxy exposes service-token agent API | Adjacent security debt under registry `security-hygiene`; not named in v0.10.6.1 feature scope | Treat as release-blocking security hardening if dashboard agent route is shipped/enabled in this release artifact; otherwise explicitly defer with route disabled or protected |
| P1 missing SMTP direct-login token minting | Adjacent auth/security debt; related to existing dashboard auth registry checks, not a named v0.10.6.1 scope item | Release-blocking for any stage/prod-like deployment profile where dashboard auth is enabled and SMTP/OAuth may be absent |
| P1 dashboard proxy reintroduces `media_files[]` master selection | Directly in scope: ADR-2, G4, `recordings-playback-url-canonical`, `dashboard-multichunk-playback-30s-truncation` | Must fix before leaving `develop-code`; it violates the central recording/playback promise |
| P2 internal recording upload lacks service auth and reads unbounded bodies | Scope-adjacent to recording pipeline, JSONB canonicalization, SSOT/env doctrine; not explicitly named | Hardening follow-up unless meeting-api is externally reachable in a supported deployment; then blocker |
| P2 sweeper user-wide object listing | Scope-adjacent to recording finalization/reconciler reliability | Non-blocking hardening unless validate/prod evidence shows latency/cost/finalization impact |
| P2 raw media Range parser/full download | Directly adjacent to ADR-2 because `/raw` should no longer be dashboard playback path; endpoint hardening remains | Fix or make unreachable from dashboard path before human playback sign; endpoint hardening can follow if canonical path is fixed |

## Detailed Mapping

### 1. Dashboard agent proxy service-token exposure

Finding: `services/dashboard/src/app/api/agent/[...path]/route.ts` forwards
browser requests to agent-api using `AGENT_API_TOKEN` without requiring a
dashboard user session.

Scope links:

- `scope.yaml` has `feature_thresholds_override.security-hygiene: 0`, so
  security-hygiene inventory gaps were knowingly not complete DoD coverage.
- Registry contains general auth/security checks, but no v0.10.6.1 named item
  for `/api/agent/*` proxy authorization.
- Not part of the named "What you get" rows in `scope.md`.

Classification: adjacent security debt found during audit.

Release handling:

- If the agent UI/API is shipped/enabled in this release, this is a blocker
  because the route is browser-reachable and carries a service token.
- If agent is intentionally dormant, release needs a machine-validated guard:
  route disabled, authenticated, or excluded from deployed dashboard surface.

### 2. Missing SMTP direct-login token minting

Finding: `services/dashboard/src/app/api/auth/send-magic-link/route.ts` enters
direct-login mode when SMTP is missing and returns a real user API token.

Scope links:

- Existing registry coverage includes dashboard auth checks such as cookie
  identity and env consistency.
- `scope.md` references the dashboard auth bug as covered by cookie isolation
  and stale-auth recovery, but this direct-login fallback is a different auth
  boundary.
- Not named in v0.10.6.1 feature scope.

Classification: adjacent security debt with deployment-profile impact.

Release handling:

- Blocker for stage/prod-like dashboard deployment unless direct login is
  explicitly forbidden there.
- Acceptable only for local/dev if gated by an explicit unsafe flag and backed
  by a registry check proving production-like profiles reject it.

### 3. Dashboard proxy reintroduces `media_files[]` master selection

Finding: `services/dashboard/src/app/api/vexa/[...path]/route.ts` calls
canonical `/recordings/{id}/master`, then re-selects the first matching
`recording.media_files[]` entry and streams `/raw`.

Direct scope links:

- `scope.md` "What you get": "Multichunk recordings play end-to-end —
  dashboard reads master, not chunk-0."
- `scope.md` "What you get": "One canonical `playback_url` on each recording
  (dashboard stops choosing)."
- `scope.md` G4: "Dashboard owns storage-layout selection
  (`pickMasterMediaFile()`)"; closure says dashboard reads `playback_url`.
- `scope.md` ADR-2: "Canonical `playback_url`; dashboard becomes pure
  renderer."
- `scope.yaml` `recordings-playback-url-canonical`: delete dashboard
  `pickMasterMediaFile()` and use `recording.playback_url`; null means
  finalizing, no fallback.
- `scope.yaml` `dashboard-multichunk-playback-30s-truncation`: backfill and
  prevent chunk-0 truncation for ~73 historical recordings.

Classification: direct scope violation.

Release handling: blocker. The release cannot leave `develop-code` while any
dashboard server/client path re-implements media selection over
`media_files[]`.

### 4. Internal recording upload lacks service auth and reads unbounded bodies

Finding: `/internal/recordings/upload` has no service-auth dependency and reads
the entire upload into memory before validation/cap.

Scope links:

- Adjacent to recording-storage cleanup and JSONB canonicalization.
- Adjacent to SSOT/env doctrine because the current defense is mostly network
  topology.
- Not explicitly listed as a v0.10.6.1 feature or gap.

Classification: scope-adjacent hardening.

Release handling:

- Blocker only if meeting-api is reachable outside the intended internal
  network in any supported stage/prod path.
- Otherwise file as follow-up hardening: internal service auth, request size
  limit, streaming upload, and media metadata validation.

### 5. Sweeper user-wide object listing

Finding: `_sweep_unfinalized_recordings` lists `recordings/{user_id}/` for
each candidate session instead of a narrow session/recording prefix.

Scope links:

- Adjacent to finalizer/reconciler reliability and "Preparing audio" fixes.
- Not explicitly named in the v0.10.6.1 scope.

Classification: scope-adjacent reliability hardening.

Release handling: non-blocking unless validate or prod evidence shows the sweep
is causing finalization latency, storage cost spikes, or missed playback
delivery. Should be tracked for v0.10.7 or a hardening patch.

### 6. Raw media endpoint Range parser/full download

Finding: `/recordings/{id}/media/{media_file_id}/raw` downloads the full object
into memory and parses `Range` manually.

Scope links:

- Directly adjacent to ADR-2 because dashboard playback should no longer use
  `/raw` once canonical playback is fixed.
- Current finding #3 makes this more important because the dashboard proxy is
  still using `/raw`.

Classification: direct path hardening while `/raw` remains in dashboard
playback; otherwise adjacent endpoint hardening.

Release handling:

- Fix the canonical dashboard path first so human playback no longer depends
  on `/raw`.
- Then harden `/raw` or explicitly leave it as a deprecated compatibility
  fallback with bounded risk.

## Net Release Decision

The scope-owned blockers are:

1. Dashboard must not choose playback media from `media_files[]`.
2. Dashboard playback must use the canonical producer/backend-selected master.

The deployment-security blockers are conditional but serious:

1. `/api/agent/*` must be authenticated or disabled if shipped.
2. SMTP-missing direct login must be dev-only/explicitly unsafe, not a
   production-like default.

The remaining P2s are hardening follow-ups unless deployment topology makes
them externally reachable or validate/prod evidence shows active impact.
