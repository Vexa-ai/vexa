/** analytics — a thin, SAFE wrapper over Google Analytics (gtag.js). Every call no-ops unless GA is
 *  actually configured (a measurement id was baked at build → the <Analytics> component loaded gtag) and
 *  we're in the browser, so call sites never have to guard. The id itself lives only in <Analytics>;
 *  events here just push onto the already-configured gtag. */

declare global {
  interface Window {
    dataLayer?: unknown[];
    gtag?: (...args: unknown[]) => void;
  }
}

/** True when GA is loaded (configured at build + running in the browser). */
export function gaReady(): boolean {
  return typeof window !== "undefined" && typeof window.gtag === "function";
}

/** Send a custom GA event. No-op when GA isn't loaded. */
export function track(event: string, params: Record<string, unknown> = {}): void {
  if (!gaReady()) return;
  window.gtag!("event", event, params);
}

/** A page URL as analytics may see it: origin and path only. The query string and fragment never
 *  leave — `/join?i=…` carries an invite that signs its holder in, and other pages carry tokens too. */
export function scrubbedLocation(href: string): string {
  try {
    const u = new URL(href);
    return u.origin === "null" ? u.href.split(/[?#]/)[0] : `${u.origin}${u.pathname}`;
  } catch {
    return href.split(/[?#]/)[0];
  }
}

/** Record a SPA page view — the App Router does NOT fire gtag's automatic page_view on soft (client)
 *  navigations, so <Analytics> calls this on route change. */
export function gaPageview(path: string): void {
  track("page_view", {
    page_path: path.split(/[?#]/)[0],
    page_location: scrubbedLocation(typeof location !== "undefined" ? location.href : path),
  });
}

/** Collapse a request URL to a LOW-CARDINALITY endpoint label for the api_request event: drop the query
 *  string and replace id-like segments (numeric ids, uuids) with ":id", so "/api/meetings/123" and
 *  "/api/meetings/456" aggregate as one endpoint in GA instead of exploding the dimension. */
export function endpointLabel(url: string): string {
  return (url.split("?")[0] || url)
    .replace(/\/[0-9a-f]{8}-[0-9a-f-]{27,}(?=\/|$)/gi, "/:id")  // uuids
    .replace(/\/\d+(?=\/|$)/g, "/:id");                          // numeric ids
}
