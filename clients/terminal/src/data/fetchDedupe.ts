/** ONE REQUEST IN FLIGHT PER READ (founder 2026-10-16, "the app is not very reactive": a home load
 *  sent 66 /api calls in 18 s, `/api/connections/list` four times). Identical same-origin GETs to
 *  `/api/` that overlap share one network request; each caller gets its own clone of the answer.
 *  Never for a request with a body, an abort signal (streams and cancellable views own theirs) or
 *  an event stream. */
type Fetch = typeof fetch;

const SKIP = /\/stream\b|\/events?\b|\/attach\b|\/sse\b/;

export function dedupeKey(input: RequestInfo | URL, init?: RequestInit): string | null {
  const method = String(init?.method ?? (input instanceof Request ? input.method : "GET")).toUpperCase();
  if (method !== "GET" || init?.body != null || init?.signal) return null;
  if (input instanceof Request && input.signal && (input.signal as AbortSignal).aborted !== undefined && input !== undefined) return null;
  const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
  let url: URL;
  try { url = new URL(raw, typeof window !== "undefined" ? window.location.origin : "http://localhost"); } catch { return null; }
  if (typeof window !== "undefined" && url.origin !== window.location.origin) return null;
  if (!url.pathname.startsWith("/api/") || SKIP.test(url.pathname)) return null;
  const accept = new Headers(init?.headers).get("accept") ?? "";
  if (accept.includes("text/event-stream")) return null;
  return url.pathname + url.search;
}

export function dedupedFetch(base: Fetch): Fetch {
  const inflight = new Map<string, Promise<Response>>();
  return ((input: RequestInfo | URL, init?: RequestInit) => {
    const key = input instanceof Request ? null : dedupeKey(input, init);
    if (!key) return base(input as RequestInfo, init);
    let p = inflight.get(key);
    if (!p) {
      p = base(input as RequestInfo, init).finally(() => { inflight.delete(key); });
      inflight.set(key, p);
    }
    return p.then((r) => r.clone());
  }) as Fetch;
}

let installed = false;
/** Wrap `window.fetch` once, at boot. */
export function installFetchDedupe(): void {
  if (installed || typeof window === "undefined" || typeof window.fetch !== "function") return;
  installed = true;
  window.fetch = dedupedFetch(window.fetch.bind(window));
}
