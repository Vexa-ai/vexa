type BrowserApiUrlInput = {
  internalApiUrl: string;
  configuredPublicApiUrl?: string;
  requestHost: string;
  requestProto: "http" | "https";
  gatewayHostPort?: string;
};

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

function publicUrlFromRequestHost(requestHost: string, requestProto: "http" | "https", port: string): string {
  const requestUrl = new URL(`${requestProto}://${requestHost}`);
  requestUrl.port = port;
  requestUrl.pathname = "";
  requestUrl.search = "";
  requestUrl.hash = "";
  return normalizedUrl(requestUrl.toString());
}

export function resolveBrowserApiUrl({
  internalApiUrl,
  configuredPublicApiUrl = "",
  requestHost,
  requestProto,
  gatewayHostPort,
}: BrowserApiUrlInput): { apiUrl: string; publicApiUrl: string } {
  const configured = configuredPublicApiUrl.trim();
  const requestHostname = hostnameFromHostHeader(requestHost);

  if (configured) {
    try {
      const publicUrl = new URL(configured);
      if (isLoopbackHost(publicUrl.hostname) && !isLoopbackHost(requestHostname)) {
        publicUrl.hostname = requestHostname;
      } else if (isLoopbackHost(publicUrl.hostname) && isLoopbackHost(requestHostname)) {
        // Both the configured public URL and the request host are loopback.
        // The configured port likely points at a container-internal gateway port
        // (e.g. 8056) that is unreachable from the browser when the dashboard
        // exposes a different host port (e.g. lite single-port publish). Fall
        // back to same-origin so Next.js /ws + /api rewrites carry the traffic.
        return { apiUrl: "", publicApiUrl: "" };
      }
      const normalized = normalizedUrl(publicUrl.toString());
      return { apiUrl: normalized, publicApiUrl: normalized };
    } catch {
      const normalized = normalizedUrl(configured);
      return { apiUrl: normalized, publicApiUrl: normalized };
    }
  }

  if (gatewayHostPort && isInternalServiceUrl(internalApiUrl)) {
    // Compose case: dashboard is published on a different host port than the
    // gateway (e.g. dashboard :41688, gateway :41680). Some browser/network
    // environments only expose the dashboard's published port, so pointing the
    // browser directly at the gateway port breaks WS + cross-origin REST.
    // Prefer same-origin (empty publicApiUrl) so the browser uses the
    // dashboard's own /ws + /api/vexa/* rewrites — which already proxy to the
    // gateway service-internal URL. Curl-from-host can still reach the
    // gateway port directly; this only affects what the browser is told.
    return { apiUrl: "", publicApiUrl: "" };
  }

  if (isInternalServiceUrl(internalApiUrl)) {
    return { apiUrl: "", publicApiUrl: "" };
  }

  const normalizedInternal = normalizedUrl(internalApiUrl);
  return { apiUrl: normalizedInternal, publicApiUrl: "" };
}
