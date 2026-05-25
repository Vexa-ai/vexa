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
      }
      const normalized = normalizedUrl(publicUrl.toString());
      return { apiUrl: normalized, publicApiUrl: normalized };
    } catch {
      const normalized = normalizedUrl(configured);
      return { apiUrl: normalized, publicApiUrl: normalized };
    }
  }

  if (gatewayHostPort && isInternalServiceUrl(internalApiUrl)) {
    const inferred = publicUrlFromRequestHost(requestHost, requestProto, gatewayHostPort);
    return { apiUrl: inferred, publicApiUrl: inferred };
  }

  if (isInternalServiceUrl(internalApiUrl)) {
    return { apiUrl: "", publicApiUrl: "" };
  }

  const normalizedInternal = normalizedUrl(internalApiUrl);
  return { apiUrl: normalizedInternal, publicApiUrl: "" };
}
