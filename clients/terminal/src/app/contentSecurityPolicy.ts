/** The terminal pages' Content-Security-Policy, defence in depth behind the page renderer.
 *
 *  `default-src 'self'`, and every fetch directive the pages use names its sources. Nothing is a
 *  wildcard: a third party appears only when this deployment turned on the feature that needs it.
 *
 *  - Scripts run only when they carry this response's nonce, or when a script that carried it
 *    loaded them ('strict-dynamic', which covers Next's chunks, next/script and gtag). An inline
 *    script that came in with page content has no nonce, so it does not run, and that holds inside
 *    an `srcdoc` frame too, because such a frame inherits this policy.
 *  - No string becomes code: there is no 'unsafe-eval'. A workspace page is parsed to a tree and
 *    drawn as elements (ui-kit/MdxDoc.tsx), never compiled. 'wasm-unsafe-eval' admits WebAssembly
 *    compilation alone, for the call's background blur (MediaPipe). The development server keeps
 *    'unsafe-eval' for React's refresh runtime; a production build never carries it. A Meeting
 *    Canvas view is NOT compiled on these pages: it runs in `/canvas-frame` under its own policy
 *    (`canvasFrameContentSecurityPolicy` below), in an opaque-origin sandbox.
 *  - A `<style>` ELEMENT applies only with this response's nonce (`style-src-elem`). The server
 *    stamps the nonce on the ones it renders; an element a running script creates gets it from
 *    `styleNonceBootstrap` (below) — a script that runs is already trusted by `script-src`, and the
 *    appearance tokens, sonner, CodeMirror and mermaid all write `<style>` elements at run time. A
 *    `<style>` that arrives as markup (a page's HTML, an injected fragment) has no nonce and does
 *    not apply. Style ATTRIBUTES stay 'unsafe-inline' (`style-src-attr`): React renders `style`
 *    props into the server's HTML, and CSP has no nonce for an attribute. What an attribute can
 *    reach is bounded by `img-src` and `font-src` below, so it cannot send anything off this origin.
 *    The one foreign stylesheet is Google Fonts', for the fonts a person can pick
 *    (platform/appearance.ts).
 *  - Connections are this origin (REST, `/ws`, the event streams), plus the deployment's LiveKit
 *    server when calls are on (`LIVEKIT_URL`, the address meeting-api and agent-api hand the
 *    browser), plus Google Analytics when `NEXT_PUBLIC_GA_MEASUREMENT_ID` is set.
 *  - Pictures are this origin (every workspace image, avatar and logo comes through `/api`),
 *    `data:` and `blob:` (an attachment's preview before upload). Media is this origin: recordings
 *    stream through `/api/recordings`.
 *  - Frames are this origin only (the sign-in browser view is served from /api). `base`, `object`
 *    and form targets are closed. */
export const NONCE_HEADER = "x-nonce";

/** What the deployment turned on that the pages reach beyond their own origin for. */
export interface PolicySources {
  /** the browser's LiveKit address (`LIVEKIT_URL`); empty when this deployment has no calls */
  livekitUrl?: string;
  /** the Google Analytics measurement id (`NEXT_PUBLIC_GA_MEASUREMENT_ID`); empty when GA is off */
  gaMeasurementId?: string;
}

/** The deployment's sources, read from the same environment the rest of the stack reads. */
export function policySources(): PolicySources {
  return {
    livekitUrl: process.env.LIVEKIT_URL,
    gaMeasurementId: process.env.NEXT_PUBLIC_GA_MEASUREMENT_ID,
  };
}

/** gtag.js loads from the tag manager and reports to these hosts. Google's own guidance names them
 *  by wildcard; these are the hosts GA4 uses. */
const GA_CONNECT = [
  "https://www.googletagmanager.com",
  "https://www.google-analytics.com",
  "https://region1.google-analytics.com",
  "https://analytics.google.com",
  "https://region1.analytics.google.com",
];
const GA_IMG = ["https://www.googletagmanager.com", "https://www.google-analytics.com"];

/** LiveKit's signalling socket and the HTTP origin the client checks it against, from the URL the
 *  browser is told to join. Anything that is not a plain ws/wss/http/https origin is dropped. */
export function livekitOrigins(url: string | undefined): string[] {
  const raw = (url ?? "").trim();
  if (!raw || /[*\s;,']/.test(raw)) return [];
  let u: URL;
  try { u = new URL(raw); } catch { return []; }
  const secure = u.protocol === "wss:" || u.protocol === "https:";
  if (!secure && u.protocol !== "ws:" && u.protocol !== "http:") return [];
  if (!u.host) return [];
  return [`${secure ? "wss" : "ws"}://${u.host}`, `${secure ? "https" : "http"}://${u.host}`];
}

/** Whether this process is Next's development server (React refresh evaluates modules). */
export const isDevelopmentServer = (): boolean => process.env.NODE_ENV === "development";

export function contentSecurityPolicy(nonce: string, sources: PolicySources = policySources(),
  development: boolean = isDevelopmentServer()): string {
  const ga = (sources.gaMeasurementId ?? "").trim() !== "";
  const connect = ["'self'", ...livekitOrigins(sources.livekitUrl), ...(ga ? GA_CONNECT : [])];
  const img = ["'self'", "data:", "blob:", ...(ga ? GA_IMG : [])];
  const styles = `'self' 'nonce-${nonce}' https://fonts.googleapis.com`;
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic' 'wasm-unsafe-eval'${development ? " 'unsafe-eval'" : ""}`,
    `style-src ${styles}`,
    `style-src-elem ${styles}`,
    "style-src-attr 'unsafe-inline'",
    "font-src 'self' https://fonts.gstatic.com",
    `connect-src ${connect.join(" ")}`,
    `img-src ${img.join(" ")}`,
    "media-src 'self'",
    "worker-src 'self' blob:",
    "object-src 'none'",
    "base-uri 'none'",
    "frame-src 'self'",
    "form-action 'self'",
    "frame-ancestors 'self'",
  ].join("; ");
}

/** THE MEETING CANVAS FRAME'S POLICY (`/canvas-frame`, canvas/frame/protocol.ts). The document runs
 *  workspace-written view code, so:
 *  - `sandbox allow-scripts` — the response itself is sandboxed, embedded or opened directly: an
 *    opaque origin, so no cookie, storage or DOM of the terminal's, no popup, form, top navigation
 *    or download. Never `allow-same-origin`.
 *  - `connect-src 'none'` and nothing to load but this response's own scripts and styles: the view
 *    reaches no server at all — not the terminal's API, not anybody's. Its one channel is
 *    `postMessage` to the page that frames it.
 *  - 'unsafe-eval' because compiling the view is this document's job (`new Function`). This is the
 *    one document that keeps it, and it is the one whose origin is opaque and whose network is shut.
 *  - It cannot carry the view's data to another host by NAVIGATING either: the frame's own
 *    navigation is checked against the embedding page's `frame-src 'self'`, so the browser refuses
 *    one to any other host. A navigation to this origin is allowed and reaches only this server,
 *    without the reader's cookies (canvas/frame/lockdown.ts says why nothing can cancel it).
 *    `canvas/frame/lockdown.ts` removes WebRTC before any view code runs, and
 *    `X-DNS-Prefetch-Control: off` (middleware.ts) asks the browser not to resolve names a view
 *    writes into links.
 *  - `frame-ancestors 'self'`: only the terminal's own pages may frame it. */
export function canvasFrameContentSecurityPolicy(nonce: string): string {
  return [
    "default-src 'none'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic' 'unsafe-eval'`,
    "style-src 'self' 'unsafe-inline'",
    "font-src 'self'",
    "img-src data:",
    "connect-src 'none'",
    "media-src 'none'",
    "object-src 'none'",
    "frame-src 'none'",
    "worker-src 'none'",
    "manifest-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
    "frame-ancestors 'self'",
    "sandbox allow-scripts",
  ].join("; ");
}

/** THE STYLE NONCE FOR ELEMENTS A SCRIPT CREATES. Runs first in the page's `<head>`, under the
 *  response's nonce: every `<style>` element created through `document.createElement` /
 *  `createElementNS` from then on carries the nonce, so the libraries that write styles at run time
 *  keep working under `style-src-elem`. Markup is never stamped — a `<style>` parsed out of HTML is
 *  refused. `window.__vxStyleNonce` lets the renderer stamp mermaid's own `<style>` in the SVG text
 *  it inserts (ui-kit/docDiagrams.tsx). */
export function styleNonceBootstrap(nonce: string): string {
  return `(function(n){try{var D=Document.prototype,c=D.createElement,s=D.createElementNS;` +
    `function t(e){if(e&&String(e.localName).toLowerCase()==="style")e.setAttribute("nonce",n);return e}` +
    `D.createElement=function(){return t(c.apply(this,arguments))};` +
    `D.createElementNS=function(){return t(s.apply(this,arguments))};` +
    `Object.defineProperty(window,"__vxStyleNonce",{value:n})}catch(e){}})(${JSON.stringify(nonce)})`;
}

/** A fresh nonce: 128 random bits, base64. */
export function newNonce(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s);
}
