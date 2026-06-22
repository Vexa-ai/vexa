# @vexa/dash-config — the browser runtime-config resolver

_dashboard_new/ · brick · one concern: the `/api/config` decision, as a pure function._

The dashboard server can't bake browser config in at build time (Next's `NEXT_PUBLIC_*` are
build-time only), so it serves `/api/config` per request. This brick is the **pure core** of that
route — given the server's runtime inputs it returns the three values the browser needs:

```ts
resolveBrowserConfig({
  internalApiUrl,          // the service-internal API URL the dashboard server talks to
  configuredPublicApiUrl?, // operator-configured public API URL, if any
  requestHost,             // the request Host header (may carry :port)
  requestProto,            // "http" | "https" (x-forwarded-proto aware)
  gatewayHostPort?,        // the PUBLISHED gateway host port (API_GATEWAY_HOST_PORT)
  cookieToken?,            // the user's auth cookie value
  selfHostKey?,            // the self-hosted service token (VEXA_API_KEY)
}) => { apiUrl, wsUrl, authToken }
```

It is a faithful lift of the legacy
[`clients/dashboard/src/lib/browser-api-url.ts`](../../../dashboard/src/lib/browser-api-url.ts) +
[`.../app/api/config/route.ts`](../../../dashboard/src/app/api/config/route.ts) — collapsed into one
function, no Next/request plumbing.

## The decision

- **`apiUrl`** — the browser-facing REST base, or `""` for same-origin (use the dashboard's own
  `/api` rewrites). Loopback configured-URL is rewritten onto a public request host; an internal
  service URL (`api-gateway`, `*.svc`) with no public override is same-origin.
- **`wsUrl`** — DERIVED from the resolved REST base: `http→ws`, `https→wss`, `+ "/ws"`. When `apiUrl`
  is same-origin `""`, `wsUrl` is `""` too — the App Router **cannot upgrade** a same-origin `/ws`
  (Learning #37), so we never hand back a WS URL that has no live socket behind it.
- **`authToken`** — ONE source, in order: `cookieToken || selfHostKey || null`.

### Learning #37 (carried)

In the both-loopback compose/tunnel case (configured URL `127.0.0.1:<p>` **and** request host
loopback, via an SSH tunnel), only trust the loopback URL — and connect the browser **gateway-direct**
— when its port equals the **published** gateway host port (`gatewayHostPort`). That port is reachable
through the compose host-port publish / tunnel; any other loopback port is container-internal, so we
fall back to same-origin `""`. Connecting gateway-direct is the only way the browser gets a live `/ws`,
because the dashboard's own App Router can't proxy the upgrade.

## Surface

Front door: [`src/index.ts`](src/index.ts) — `resolveBrowserConfig(input) => { apiUrl, wsUrl,
authToken }`, plus the `ResolveBrowserConfigInput` / `BrowserConfig` types and the `isLoopbackHost`
helper. Zero deps, ESM.

## Verify

`npm run build` — `tsc` clean. `npm test` runs [`src/config.test.ts`](src/config.test.ts) via `tsx`
(exit code is the signal): both-loopback gateway-direct keeps the configured ws URL; both-loopback
non-gateway-port → same-origin; loopback-config + public host rewrite; `https→wss`; internal-service
same-origin; and `authToken` cookie vs selfHost vs null.

```bash
cd clients/dashboard_new/modules/dash-config
npm i --no-audit --no-fund
npx tsx src/config.test.ts
```
