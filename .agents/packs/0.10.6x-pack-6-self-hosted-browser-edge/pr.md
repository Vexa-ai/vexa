# Pack PR: [Pack] PACK 6 - Self-Hosted Browser Edge

Pack epic: https://github.com/Vexa-ai/vexa/issues/361
Pack id: `0.10.6x-pack-6-self-hosted-browser-edge`
Release: `0.10.6.x replay`
Base branch: `v0.10.6^{}`
Integration branch: `codex/release-0.10.6x-pack-integration`
Evidence: `.agents/packs/0.10.6x-pack-6-self-hosted-browser-edge/`

## Outcomes

CEO: Self-hosted users can open the dashboard and browser views without internal container URLs or unintended host-port exposure.

CTO: Browser-facing config/proxy/auth edges are explicit, public, and separate from internal service routing across Lite, Compose, and Helm-shaped deployments.

User: The dashboard works from the browser, while Meeting API/Admin/Runtime/TTS/Redis/Postgres stay internal in self-hosted Lite.

## Scope

- #348
- browser/Lite portions of #331

## Blast radius

Dashboard runtime config, auth cookies, proxy routes, VNC/CSP, Lite network model, Helm runtime bot launch, self-hosted docs.

## What shipped

- `services/dashboard/src/lib/browser-api-url.ts` — new `resolveBrowserApiUrl()` SSOT that decides what the browser is told to talk to, given the internal API URL, any configured public URL, the request host/proto, and an optional `API_GATEWAY_HOST_PORT` hint.
- `services/dashboard/src/app/api/config/route.ts` — `/api/config` is now the runtime SSOT for `wsUrl`, `apiUrl`, `publicApiUrl`. Fails closed when `VEXA_API_URL` is missing. WS URL is derived from the resolver's `publicApiUrl`, then `NEXT_PUBLIC_APP_URL`, then same-origin `${proto}://${host}/ws`.
- `services/dashboard/src/lib/auth-cookies.ts` — `getAuthCookieName()` / `getUserInfoCookieName()` SSOT (overridable via `VEXA_AUTH_COOKIE_NAME` / `VEXA_USER_INFO_COOKIE_NAME`). `/api/config` reads the auth cookie through this helper.
- `services/dashboard/src/components/meetings/browser-session-view.tsx` — VNC/CDP/save/storage URLs go through the resolved `apiUrl` (relative to dashboard origin when same-origin) instead of a hardcoded `localhost:8056` fallback.
- `deploy/lite/supervisord.conf` — dashboard runs with `VEXA_API_URL=http://localhost:8056` and `VEXA_ALLOW_DIRECT_LOGIN=true`; runtime-api and meeting-api share `INTERNAL_API_SECRET="lite-internal-secret"`; TTS service env is wired (PIPER_DEFAULT_VOICES / PIPER_LOAD_VOICES / PIPER_PRELOAD_STRICT).
- `deploy/lite/Makefile` — lite container runs with `VEXA_AUTH_COOKIE_NAME=vexa-token-lite` + `VEXA_USER_INFO_COOKIE_NAME=vexa-user-info-lite` so lite cookies do not collide with a co-resident Compose dashboard. Persistent `vexa-lite-recordings` volume mounted. Accepts `LITE_TRANSCRIPTION_SERVICE_*` overrides.
- `deploy/lite/Dockerfile.lite` — dashboard build pulls real `VERSION` + `Chart.yaml` from `/repo` so the embedded release identity matches the chart, instead of a default `dev` fallback.
- `deploy/helm/charts/vexa/templates/_helpers.tpl` + `deployment-runtime-api.yaml` — `vexa.botImage` helper centralizes the runtime-api `BROWSER_IMAGE`, and runtime-api now gets `TTS_SERVICE_URL` from the chart instead of relying on a meeting-api inheritance.

## Network model and browser routing

- **Lite**: Today `make lite` uses `docker run --network host`, so all 14 supervisord services bind to host ports. The browser-edge contract still applies: only the dashboard (`:3000`) and gateway (`:8056`) are part of the documented browser surface. Admin (`:8057`), Meeting (`:8080`), Runtime (`:8090`), Agent (`:8100`), MCP (`:18888`), TTS (`:8059`), Redis (`:6379`), Xvfb (`:99`) are container-internal in intent and not addressed by the browser; the resolver enforces this by returning same-origin (empty `publicApiUrl`) when the configured public URL is a loopback that the browser cannot necessarily reach.
- **Compose**: dashboard and gateway are published on distinct host ports (per `deploy/compose/docker-compose.yml`). The resolver returns same-origin even when `API_GATEWAY_HOST_PORT` is set, because some browser/CI/sandbox environments only expose the dashboard's published port. The dashboard's `next.config.ts` `/ws` and `/b/*` rewrites point at `VEXA_API_URL` and carry WS + REST through the dashboard.
- **Helm / hosted**: a non-loopback `VEXA_PUBLIC_API_URL` (or `NEXT_PUBLIC_VEXA_API_URL`) is taken at face value and emitted to the browser as both `apiUrl` and `wsUrl` host.

## Stitched-candidate regression fixes

Two regressions found during 0.10.6.3 stitched validation, both in this pack's resolver. Fix commits live on this pack branch:

- `df87805` — `fix(pack-6): same-origin fallback when both configured + request host are loopback`. Lite supervisord sets `NEXT_PUBLIC_API_URL=http://localhost:8056`; the browser is at host port (e.g. `:41692`) for the dashboard. Old resolver emitted `wsUrl=ws://localhost:8056/ws`, unreachable from the browser. Fix: when configured + request hostnames are both loopback, return same-origin and let the dashboard `/ws` rewrite carry traffic to the gateway. Verified end-to-end (101 Switching Protocols on dashboard `:41692/ws`).
- `3566512` — `fix(pack-6): same-origin even with gatewayHostPort`. Compose publishes dashboard `:41688` + gateway `:41680`. Old resolver returned the gateway URL as `publicApiUrl` whenever `gatewayHostPort` was set, telling the browser to bypass the dashboard `/ws` rewrite and connect direct to `:41680`. That fails in single-port-exposed browser environments (sandboxes, dev-tunnel proxies, some CI browsers) with WS close 1006. Fix: when the internal URL is an internal-service hostname (`api-gateway`, `*.svc`, `*.svc.cluster.local`, or short DNS) and a `gatewayHostPort` hint is present, still prefer same-origin so the dashboard `/ws` + `/api/vexa/*` rewrites carry the traffic.

Both regressions are covered by `services/dashboard/tests/test_browser_api_url.test.ts`.

## Validation

- Synthetic: resolver tests cover the 5 regression + intended-path cases.
- Compose: dashboard `/ws` 101 verified through `:41688` against gateway-internal URL.
- Lite: dashboard `/ws` 101 verified through `:41692` against `localhost:8056` after fix `df87805` rebuilt + restitched.
- Hardenloop and live/human gates: covered by the stitched 0.10.6.3 candidate run, not by this pack in isolation.

## Known follow-ons (filed, not in pack 6's blast radius)

- **#382** — dashboard auth resilience. `getAuthCookieName()` is wired into `/api/config`, but the full SSOT rewire (login, refresh, middleware, server-action cookie writes) is not finished in this pack. Lite sets distinct cookie names but the dashboard's other cookie reads/writes still reference the literal `vexa-token` / `vexa-user-info`. Tracked for a follow-on pack.
- **#383** — stop-during-joining. Browser-session view stop semantics race when stop is called before the bot finishes joining; surfaced during stitched live runs. Out of pack 6 scope.
- **Lite `INTERNAL_API_SECRET="lite-internal-secret"` hardcoded** in `deploy/lite/supervisord.conf`. Security smell, not a regression — the value is identical inside and outside the container, so it does not weaken the runtime-api/meeting-api integrity check, but it is a known-string secret. Slated for entrypoint-driven randomization in a follow-on pack.

## PR readiness checklist

- [x] Pack branch starts from `v0.10.6^{}`.
- [x] Only this pack's committed reuse hunks are replayed.
- [x] Synthetic checks pass before live/human checks.
- [x] Compose gate is passed (stitched 0.10.6.3 candidate).
- [x] Lite gate is passed (stitched 0.10.6.3 candidate, after `df87805`).
- [x] Hardenloop is run for the pack (rolled into stitched candidate).
- [x] PR body links this epic and evidence root.
- [x] Reviewer can map each reused hunk back to the commit list above.
