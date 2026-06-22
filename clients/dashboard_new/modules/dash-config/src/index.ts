/**
 * @vexa/dash-config — the browser runtime-config resolver.
 *
 * ONE concern: turn the server's runtime inputs (the configured/internal API URLs + the request's
 * host/proto + the gateway's published host port + the two token sources) into the three values the
 * browser needs — `{ apiUrl, wsUrl, authToken }` — as a PURE function. This is the `/api/config`
 * decision, lifted out of Next's request plumbing so it can be tested deterministically.
 *
 * It carries the SSOT seam decisions verbatim from the legacy dashboard:
 *   • `apiUrl`    — the browser-facing REST base (loopback/tunnel/gateway-direct vs same-origin ""),
 *                   ported from `clients/dashboard/src/lib/browser-api-url.ts`.
 *   • `wsUrl`     — derived from the resolved REST base (http→ws, https→wss, + "/ws"); when the REST
 *                   base is same-origin "", the WS is same-origin "" too (the App Router can't upgrade
 *                   a same-origin /ws — Learning #37 — so we never invent a live WS that isn't there).
 *   • `authToken` — ONE source, in order: `cookieToken || selfHostKey || null`.
 *
 * Learning #37: in the both-loopback compose/tunnel case, only trust the configured (loopback) URL —
 * and connect the browser GATEWAY-DIRECT — when its port equals the PUBLISHED gateway host port
 * (`gatewayHostPort`, reachable through the compose host-port publish or an SSH tunnel). Otherwise the
 * port is container-internal/unreachable, so fall back to same-origin "".
 */

export interface ResolveBrowserConfigInput {
  /** The service-internal API URL the dashboard server talks to (e.g. http://api-gateway:8056). */
  internalApiUrl: string;
  /** The operator-configured public API URL, if any (VEXA_PUBLIC_API_URL / NEXT_PUBLIC_*). */
  configuredPublicApiUrl?: string;
  /** The request's Host header (may carry a :port), e.g. "127.0.0.1:18030" or "app.example.com". */
  requestHost: string;
  /** The request's effective protocol (x-forwarded-proto aware). */
  requestProto: "http" | "https";
  /** The PUBLISHED gateway host port (API_GATEWAY_HOST_PORT) — reachable from the browser. */
  gatewayHostPort?: string;
  /** The user's auth cookie value, if present (preferred token). */
  cookieToken?: string | null;
  /** The self-hosted service token (VEXA_API_KEY), the fallback when no cookie. */
  selfHostKey?: string | null;
}

export interface BrowserConfig {
  /** Browser-facing REST base. "" means same-origin (use the dashboard's own /api rewrites). */
  apiUrl: string;
  /** Browser-facing WS URL. "" means same-origin (the dashboard origin's /ws). */
  wsUrl: string;
  /** The single auth token for the WS/REST calls, or null when neither source is set. */
  authToken: string | null;
}

export function isLoopbackHost(hostname: string): boolean {
  return (
    hostname === "localhost" ||
    hostname === "127.0.0.1" ||
    hostname === "0.0.0.0" ||
    hostname === "::1"
  );
}

function hostnameFromHostHeader(host: string): string {
  try {
    return new URL(`http://${host}`).hostname;
  } catch {
    return host.split(":")[0] || host;
  }
}

function normalizedUrl(value: string): string {
  return value.replace(/\/+$/, "");
}

function isInternalServiceUrl(value: string): boolean {
  try {
    const { hostname } = new URL(value);
    return (
      hostname === "api-gateway" ||
      hostname.endsWith(".svc") ||
      hostname.endsWith(".svc.cluster.local") ||
      (!hostname.includes(".") && !isLoopbackHost(hostname))
    );
  } catch {
    return false;
  }
}

/**
 * Resolve the browser-facing REST base.
 *
 * Returns "" for the same-origin case (the browser should use the dashboard's own /api rewrites),
 * otherwise the absolute gateway-direct/tunnel/loopback base. Ported verbatim from the legacy
 * `resolveBrowserApiUrl` — minus the redundant second `publicApiUrl` field, which the WS derivation
 * here folds back into the single `apiUrl`.
 */
function resolveApiUrl(input: ResolveBrowserConfigInput): string {
  const {
    internalApiUrl,
    configuredPublicApiUrl = "",
    requestHost,
    requestProto,
    gatewayHostPort,
  } = input;
  void requestProto; // request proto only drives the WS scheme; the REST base trusts the URLs as given.

  const configured = configuredPublicApiUrl.trim();
  const requestHostname = hostnameFromHostHeader(requestHost);

  if (configured) {
    try {
      const publicUrl = new URL(configured);
      if (isLoopbackHost(publicUrl.hostname) && !isLoopbackHost(requestHostname)) {
        publicUrl.hostname = requestHostname;
      } else if (isLoopbackHost(publicUrl.hostname) && isLoopbackHost(requestHostname)) {
        // Both the configured public URL and the request host are loopback. If the configured port is
        // the PUBLISHED gateway host port (API_GATEWAY_HOST_PORT) — reachable from the browser via a
        // compose host-port publish or an SSH tunnel — trust it and connect the browser gateway-DIRECT
        // (Learning #37). The App Router cannot upgrade a same-origin /ws, so a same-origin fallback
        // would have no live WS; only fall back when the configured port is NOT the published gateway
        // port (e.g. a container-internal 8056 in lite single-port).
        if (gatewayHostPort && publicUrl.port === String(gatewayHostPort)) {
          return normalizedUrl(publicUrl.toString());
        }
        return "";
      }
      return normalizedUrl(publicUrl.toString());
    } catch {
      return normalizedUrl(configured);
    }
  }

  if (gatewayHostPort && isInternalServiceUrl(internalApiUrl)) {
    // Compose case: dashboard is published on a different host port than the gateway. Some
    // browser/network environments only expose the dashboard's published port, so pointing the
    // browser directly at the gateway port breaks WS + cross-origin REST. Prefer same-origin so the
    // browser uses the dashboard's own /ws + /api rewrites — which already proxy to the gateway.
    return "";
  }

  if (isInternalServiceUrl(internalApiUrl)) {
    return "";
  }

  return normalizedUrl(internalApiUrl);
}

/** http→ws, https→wss, + "/ws". `baseUrl` is an absolute http(s) base. */
function wsUrlFromHttpBase(baseUrl: string): string {
  const trimmed = baseUrl.replace(/\/+$/, "");
  const wsProto = trimmed.startsWith("https://") ? "wss" : "ws";
  return `${wsProto}://${trimmed.replace(/^https?:\/\//, "")}/ws`;
}

/**
 * Resolve the browser's runtime config — the pure core of the `/api/config` route.
 *
 * `wsUrl` is DERIVED from the resolved REST base: when `apiUrl` is an absolute base we connect
 * gateway-direct (http→ws); when `apiUrl` is same-origin "" the WS is same-origin "" too, since the
 * App Router cannot upgrade a same-origin /ws (Learning #37) — the caller's WS client then uses the
 * dashboard origin. `authToken` has ONE source order: cookieToken || selfHostKey || null.
 */
export function resolveBrowserConfig(input: ResolveBrowserConfigInput): BrowserConfig {
  const apiUrl = resolveApiUrl(input);
  const wsUrl = apiUrl ? wsUrlFromHttpBase(apiUrl) : "";
  const authToken = input.cookieToken || input.selfHostKey || null;
  return { apiUrl, wsUrl, authToken };
}
