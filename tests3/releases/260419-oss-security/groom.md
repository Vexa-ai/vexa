# Groom — 260419-oss-security

| field        | value                                                              |
|--------------|--------------------------------------------------------------------|
| release_id   | `260419-oss-security`                                              |
| stage        | `groom`                                                            |
| entered_at   | `2026-04-19T15:43:57Z`                                             |
| actor        | `AI:groom`                                                         |
| predecessor  | `idle` (prior release `260419-helm`)                               |
| theme (user) | *"OSS security hardening — pre-cutover to Stripe live mode"*       |

---

## Scope, stated plainly

`vexa-platform` is cutting over from Stripe test mode to live mode. A pre-cutover
security scan and the OSS GitHub security tab surface several findings that
live in the **upstream OSS `Vexa-ai/vexa` repo** — not in the proprietary
platform layer. Every hosted deployment and every self-hoster inherits them.
This cycle closes them upstream so the submodule bump unblocks production cutover.

The user supplied a 5-finding PRD (OSS-1 … OSS-5). I cross-checked each against
`main` and also queried `Vexa-ai/vexa/security` + `/security/advisories` +
Dependabot. **Verdict: PRD is partially stale, and two HIGH/MED
already-reported advisories sit in the security tab that the PRD does not
mention.** Packs below reflect the corrected picture.

---

## Signal sources scanned

| source                                               | count | notes                                         |
|------------------------------------------------------|------:|-----------------------------------------------|
| Pre-cutover PRD (user-supplied, this turn)           | 5     | OSS-1 … OSS-5                                 |
| `/security/advisories` (GH)                          | 2     | both **draft**, both have expired 60-day deadline |
| Dependabot alerts (open, non-dismissed)              | ~25   | clustered under Pack E                        |
| Code-scanning alerts                                 | 0     | endpoint returns docs-only string             |
| Open issues (`gh issue list`)                        | ~60   | only #122 is security-adjacent (CDP exposure) |

---

## Audit of PRD findings against current `main`

Each PRD finding, verified against the live codebase. **The PRD contains three
items that are already partially or fully implemented; the grooming step
corrects the scope so plan doesn't re-do done work.**

| PRD   | PRD claim                                                     | Reality in `main` (audited this turn)                                                                                                                                                                                         | Keep in scope? |
|-------|---------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------|
| OSS-1 | Pin `h11==0.16.0` (CVE-2025-43859)                             | No explicit `h11` pin in **any** service `requirements*.txt`. api-gateway pins `httpx==0.24.0` — old — resolves to a pre-0.16.0 `h11` transitively. **Valid.**                                                                  | **YES**        |
| OSS-2 | Gate `/docs`,`/redoc`,`/openapi.json` behind env in api-gateway + mcp | Neither has gating. `services/api-gateway/main.py:85-115` — `FastAPI(...)` without `docs_url`. `services/mcp/main.py:13` — `FastAPI()` bare. Similarly admin-api, meeting-api, runtime-api, tts-service, agent-api, calendar-service, transcription-service. **Valid — and the blast radius is broader than the PRD says.** | **YES**        |
| OSS-3 | CDP proxy: 307 scheme downgrade, Host-header DNS-rebind block | **Host-header rewrite is ALREADY IN PLACE** — `main.py:1819, 1854, 1879` all pass `Host: localhost` to upstream. Remaining defects: (a) `main.py:1824` hardcodes `proxy_ws_url = f"ws://{host}/..."` — doesn't preserve `wss://` for hosted deployments, (b) `@app.api_route(".../cdp/{path:path}", methods=["GET"])` at `main.py:1807` matches `/cdp/json/version` but not bare `/cdp`. **Valid but narrower than PRD framed it.** | **YES, reduced** |
| OSS-4 | Add `slowapi` rate limiting                                    | **ALREADY IMPLEMENTED.** `main.py:182-250` is a Redis-backed sliding-window middleware with separate buckets for API / admin / WS, driven by `RATE_LIMIT_RPM` / `RATE_LIMIT_ADMIN_RPM` / `RATE_LIMIT_WS_RPM`. Per-route tuning (PRD's sensible defaults) is absent, but the core is there. **PRD is stale.** | **NO** — reclassify to a PR of sensible defaults only if human wants |
| OSS-5 | Audit CORS wildcard + credentials                              | api-gateway `main.py:161-177` — wildcard guard is correct (`allow_credentials=not _cors_wildcard`). meeting-api same pattern. agent-api + runtime-api set no `allow_credentials` (FastAPI default `False`) → **not vulnerable**. mcp has **no CORSMiddleware at all**. **No remaining finding; the PRD flag is a false positive upstream.** | **NO** |

**Conclusion:** PRD OSS-1, OSS-2, OSS-3 are real. OSS-4 is done. OSS-5 is a false positive upstream (platform-side tx-gateway is a separate concern and stays proprietary).

---

## Undocumented findings — the security tab

The PRD does not mention these, but `Vexa-ai/vexa/security/advisories` has
**two unpublished drafts** both past their 60-day responsible-disclosure
deadline. One is a HIGH; its fix is **not in main**.

### **SEC-A** — Unauthenticated internal transcript endpoint (HIGH / CVSS 7.5)

- **Advisory**: `GHSA-w73r-2449-qwgh` · `CVE-2026-25058` · state: **draft** · submitted 2026-01-28 · CWE-306 + CWE-862.
- **Reporter**: Ariel Silver (`@SilverPlate3`).
- **Disclosure deadline**: expired ~2026-03-29. Public disclosure is at the reporter's discretion at any point now.
- **Code path (still vulnerable on `main`)**: `services/meeting-api/meeting_api/collector/endpoints.py:405-427`
  ```python
  @router.get("/internal/transcripts/{meeting_id}",
              include_in_schema=False)
  async def get_transcript_internal(meeting_id: int, ...):
      meeting = await db.get(Meeting, meeting_id)   # no auth, no ownership check
      ...
      return segments
  ```
  `include_in_schema=False` hides the route from OpenAPI, **not** from the network. The route sits on the meeting-api bind; any client that reaches the service port reads any meeting's transcripts by integer enumeration.
- **Impact**: confidentiality breach across tenants. In a hosted multi-tenant deployment, one curl pulls every transcript.
- **Why PRD missed it**: PRD looked at the pre-cutover scan (platform-side), not at the OSS security tab.

### **SEC-B** — SSRF in webhook delivery (MEDIUM / CVSS 5.8)

- **Advisory**: `GHSA-fhr6-8hff-cvg4` · `CVE-2026-25883` · state: **draft** · submitted 2026-01-29 · CWE-918.
- **Reporter**: Ariel Silver (`@SilverPlate3`).
- **Disclosure deadline**: expired ~2026-03-30.
- **Fix status**: **ALREADY IN MAIN.** `services/meeting-api/meeting_api/webhook_url.py` implements `validate_webhook_url()` (blocks localhost, private/link-local/multicast IPv4+IPv6, cloud-metadata, and Vexa Docker service hostnames, with DNS-rebind-safe forward resolution). It's called from `services/meeting-api/meeting_api/webhooks.py:107, 177, 232` before every delivery.
- **Action**: the fix isn't landed-but-unpublished — it just needs CVE release. Advisory draft → published, credit Ariel Silver, cite the commit that added `webhook_url.py`.

### **SEC-C** — Dashboard dependency sweep (~20 open alerts)

- Target: `services/dashboard/package-lock.json` (+ `package.json`).
- Mix: Next.js (6 alerts: DoS, request smuggling, CSRF bypass, disk cache growth, …), Vite (3 HIGH: path traversal, fs.deny bypass, ws file read), flatted (HIGH prototype pollution), fast-xml-parser (HIGH entity expansion bypass), nodemailer, picomatch, brace-expansion.
- Attack surface depends on where the dashboard runs: if it's operator-only and behind platform auth, these are lower priority. If self-hosters expose it, they matter.
- **Remediation shape**: `npm audit fix` + Next.js major bump.

### **SEC-D** — `basic-ftp` in vexa-bot (3 open HIGH)

- Target: `services/vexa-bot/package-lock.json` + `services/vexa-bot/core/package-lock.json`.
- Alerts: `GHSA-rp42-5vxx-qpwr` (DoS), `GHSA-6v7q-wjvx-w8wg` (CRLF injection), `GHSA-chqc-8p9q-pq6q` (CRLF injection).
- Transitive via Playwright ecosystem; basic-ftp is unused at runtime but sits in the lockfile.
- **Remediation shape**: `npm audit fix` or `--force` with a version-range override.

### **SEC-E** — (related, not a new finding)

Open issue [#122](https://github.com/Vexa-ai/vexa/issues/122) ("Expose CDP WebSocket from bot browser for remote debugging & MCP integration") is the feature that the OSS-3 fix enables. Close with the fix-commit, or link.

---

## Packs — candidates for this cycle

Ordered by time-to-exposure risk, not by PRD order.

### Pack A — Close the unauthenticated transcript endpoint  (**recommended: YES, as #1 priority**)

- **source**: GitHub security advisory `GHSA-w73r-2449-qwgh` (CVE-2026-25058).
- **severity**: HIGH (CVSS 7.5).
- **scope**: add an auth dependency to `services/meeting-api/meeting_api/collector/endpoints.py:405` or move the route to an internal-only bind (unix socket / localhost-only interface). Confirm no production flow depends on the unauthenticated path; if it does, require the internal service token that admin-api already mints.
- **estimated scope**: ~30 lines + 1 new regression check (`auth.internal_transcripts_requires_token`). 1 day with tests.
- **reproducibility confidence**: high — advisory has a canned `curl http://localhost:8123/internal/transcripts/1` reproduction.
- **owner feature(s)**: `meeting-api` (and whatever feature owns the transcripts retrieval DoD).
- **why this comes first**: the disclosure deadline expired ~3 weeks ago. Reporter can go public at any time. Stripe live-mode cutover is blocked on this regardless of the PRD.

### Pack B — Publish the webhook-SSRF advisory  (**recommended: YES**)

- **source**: GitHub security advisory `GHSA-fhr6-8hff-cvg4` (CVE-2026-25883).
- **severity**: MEDIUM (CVSS 5.8). Fix is already in `main`.
- **scope**: (1) confirm `validate_webhook_url()` is also applied on the user-facing `PUT /user/webhook` input (defence in depth), not only at delivery time. (2) Write an `aggregate.py`-visible regression check `webhooks.ssrf_blocked` that feeds a crafted payload and expects a 4xx. (3) Draft advisory publishing comms: bump state draft → published, credit Ariel Silver, patched-versions field populated.
- **estimated scope**: ~20 lines (input validation, if missing) + 1 regression check. 0.5 day.
- **reproducibility confidence**: high — reproduction is in the advisory body.

### Pack C — h11 pin + Swagger gating  (**recommended: YES — covers OSS-1 + OSS-2**)

- **source**: user PRD (OSS-1, OSS-2).
- **severity**: CRITICAL (CVE-2025-43859, transitive) + HIGH (info disclosure). Fix is tiny.
- **scope**:
  1. Pin `h11==0.16.0` explicitly in every service `requirements.txt` that depends on httpx or uvicorn (`api-gateway`, `mcp`, `meeting-api`, `admin-api`, `runtime-api`, `tts-service`, `calendar-service`, `telegram-bot`, `agent-api`, `transcription-service`, and `deploy/lite/requirements.txt`).
  2. Add an `ENV`-gated `docs_url` / `redoc_url` / `openapi_url` to every FastAPI app (9 services identified above). Default-deny on `ENV=production`.
  3. Platform-side registry lock `VEXA_DOCS_ENV_GATED` is added downstream (out of scope for OSS PR, noted in §Follow-ups).
- **estimated scope**: ~50 lines across ~10 files + 1 regression check (`grep docs_url=.*PUBLIC_DOCS services/*/main.py`). Half-day.
- **open question**: what env var name does the project prefer — `ENV` or `VEXA_ENV`? (PRD flagged this.) **Plan needs to resolve; groom doesn't.**

### Pack D — CDP proxy scheme preservation  (**recommended: YES, reduced from PRD OSS-3**)

- **source**: user PRD (OSS-3) + open issue #122.
- **severity**: HIGH (usability blocker, not exploit).
- **scope**:
  1. `services/api-gateway/main.py:1824` — derive scheme from upstream forwarded headers (`X-Forwarded-Proto` / `request.url.scheme`) so `proxy_ws_url` becomes `wss://` on HTTPS gateways, `ws://` only on local dev.
  2. Accept `/b/{token}/cdp` (no trailing slash) as an alias for `/b/{token}/cdp/` — either via explicit route or by disabling FastAPI's 307 redirect in this path. The 307 currently drops `https` → `http` when hit behind a terminating proxy.
  3. (Already done — do not redo) Host-header rewrite.
  4. Integration test: `playwright.chromium.connect_over_cdp(f"https://{host}/b/{tok}/cdp")` must return an active browser. Can run in compose mode with a self-signed cert.
- **estimated scope**: ~15 lines in one file + 1 integration test. Half-day.
- **reproducibility confidence**: high — PRD has the exact repro.

### Pack E — Dependabot hygiene (vexa-bot + dashboard)  (**recommended: split — vexa-bot YES, dashboard DEFER**)

- **source**: Dependabot alerts on the GH security tab.
- **E.1 — vexa-bot basic-ftp**: `npm audit fix` on `services/vexa-bot/` and `services/vexa-bot/core/`. 3 HIGH alerts, but basic-ftp is transitive and appears unused at runtime. ~0.5 day; low risk of regression.
- **E.2 — dashboard**: 20+ mixed-severity alerts. Needs a Next.js major bump that is structurally different from a one-off CVE patch, and the dashboard's attack surface depends on platform-side deployment choices. **Recommend: defer to a follow-up release** with its own scope; do not bundle into a "security hardening" release that's otherwise small and PR-ready.

### Pack F — PRD items to drop / re-scope  (**recommended: DROP**)

- OSS-4 (rate limiting) — already implemented. Platform may still want to propose sensible **per-route** defaults, but this belongs in a tuning release, not a security release.
- OSS-5 (CORS audit) — upstream is already safe (`allow_credentials=not _cors_wildcard` where it matters; mcp has no CORS). Platform-side tx-gateway fix stays proprietary.
- Drop both from this cycle's scope.

---

## Suggested cycle shape — human picks

I see two reasonable cycle shapes:

### Shape 1 — Narrow + fast  (**my recommendation**)

- Pack A (unauth transcript — HIGH)
- Pack B (publish webhook SSRF advisory — done code, publish + regression check)
- Pack C (h11 + Swagger gating — tiny, unblocks cutover)
- Pack D (CDP scheme fix — single-file)
- Pack E.1 (vexa-bot `npm audit fix`)
- Defer Pack E.2. Drop Pack F.

Total: **~1.5–2 days develop** + validate + human. Fits in one INNER-loop cycle. Releases 1 green gate + 2 published advisories + 1 regression-check delta. Unblocks the Stripe cutover Lane 1.

### Shape 2 — Minimum-viable advisory close  (fallback if time is tight)

- Pack A + Pack B only. Publish both advisories same day. Defer everything else.

Total: **~1 day**. Gets the two CVE drafts off the disclosure clock but leaves OSS-1/OSS-2/OSS-3 for a follow-up PR against `Vexa-ai/vexa:main`.

---

## Halt

`groom` stops here. `scope.yaml` is `plan`'s output.

### Waiting on human for

- [ ] Confirm **Pack A** (unauth transcript endpoint) lands this cycle.
- [ ] Confirm **Pack B** (publish webhook SSRF advisory) lands this cycle.
- [ ] Confirm **Pack C** (h11 + Swagger gating) lands this cycle.
- [ ] Confirm **Pack D** (CDP scheme preservation).
- [ ] Decide **Pack E** — both subpacks or just E.1 (vexa-bot).
- [ ] Confirm **Pack F is dropped** (PRD OSS-4 + OSS-5 are not in scope).
- [ ] Which cycle shape: **Shape 1 (recommended)** or **Shape 2 (minimum)**?
- [ ] ENV var naming convention: `ENV=production` vs `VEXA_ENV=production` (surfaced in Pack C).

### Follow-ups deferred to plan (not blocking groom→plan)

- Draft the PR split (PRD proposes three PRs; my recommendation collapses to two or three and is sensitive to whether Pack A ships alongside B).
- Draft the regression check IDs that land in `tests3/registry.yaml` for SEC-A, SEC-B, OSS-1, OSS-2, OSS-3.
- Platform-side follow-up ticket after merge: submodule bump + `VEXA_DOCS_ENV_GATED` registry lock + unblock Stripe cutover Lane 1. (Stays in `vexa-platform`, not this repo.)

### Advancing (after human approval)

```bash
python3 tests3/lib/stage.py enter plan --actor AI:plan
```
