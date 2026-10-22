// THE POLICY FOR EVERYTHING THAT IS NOT A PAGE. Pages get theirs from middleware.ts (a per-response
// nonce, src/app/contentSecurityPolicy.ts). The API, the `/b/` bot proxy, Next's build assets and the
// files in public/ are left alone by that middleware — and a response the browser can be sent to as a
// DOCUMENT (an API answer opened in a tab, an SVG, a proxied page) needs a policy of its own. So
// server.mjs gives every response that leaves without a Content-Security-Policy the one below for its
// path. A route that writes its own (a workspace file's `default-src 'none'; sandbox`, the sign-in
// link's card, the connections callback) keeps it: this fills a gap, it never overrides.

/** A browser session's live view (noVNC: its own scripts, styles and images, and the socket back to
 *  this origin) — under /api, and the same shape behind the `/b/` bot proxy. No inline script. */
export const LIVE_VIEW_POLICY = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self'",
  "connect-src 'self'",
  "worker-src 'self' blob:",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'none'",
  "frame-ancestors 'self'",
].join("; ");

/** An API answer opened as a document: nothing runs and nothing loads, but a picture or a recording
 *  the route serves still shows. */
export const API_POLICY = [
  "default-src 'none'",
  "img-src 'self'",
  "media-src 'self'",
  "base-uri 'none'",
  "form-action 'none'",
  "frame-ancestors 'self'",
].join("; ");

/** A build asset or a public file opened as a document (an SVG logo, the README): it may style
 *  itself and show its own pictures; it runs nothing. */
export const STATIC_POLICY = [
  "default-src 'none'",
  "style-src 'unsafe-inline'",
  "img-src 'self' data:",
  "base-uri 'none'",
  "form-action 'none'",
  "frame-ancestors 'self'",
].join("; ");

const LIVE_VIEW = /^\/api\/bot-accounts\/sessions\/[a-f0-9]{32}\/view\//;

/** The policy a response on `url` carries when its route wrote none. */
export function fallbackPolicy(url) {
  let path = "/";
  try { path = new URL(String(url || "/"), "http://x").pathname; } catch { /* the root's */ }
  if (LIVE_VIEW.test(path) || path.startsWith("/b/")) return LIVE_VIEW_POLICY;
  if (path.startsWith("/api/") || path === "/api") return API_POLICY;
  return STATIC_POLICY;
}

const CSP = "content-security-policy";

function namesPolicy(headers) {
  if (!headers) return false;
  if (Array.isArray(headers)) {
    for (let i = 0; i < headers.length; i += Array.isArray(headers[i]) ? 1 : 2) {
      const name = Array.isArray(headers[i]) ? headers[i][0] : headers[i];
      if (String(name).toLowerCase() === CSP) return true;
    }
    return false;
  }
  return Object.keys(headers).some((k) => k.toLowerCase() === CSP);
}

/** Make `res` carry `fallbackPolicy(req.url)` unless the handler set a policy of its own by the time
 *  the head is written (`setHeader` or `writeHead`'s headers). */
export function applyFallbackPolicy(req, res) {
  const writeHead = res.writeHead;
  res.writeHead = function (statusCode, ...rest) {
    const headers = rest.find((a) => a && typeof a === "object");
    if (!res.headersSent && !res.hasHeader(CSP) && !namesPolicy(headers)) {
      res.setHeader(CSP, fallbackPolicy(req.url));
    }
    return writeHead.call(this, statusCode, ...rest);
  };
  return res;
}
