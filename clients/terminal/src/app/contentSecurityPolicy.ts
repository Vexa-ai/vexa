/** The terminal pages' Content-Security-Policy, defence in depth behind the page renderer.
 *
 *  `default-src 'self'`, and every fetch directive the pages use names its sources. Nothing is a
 *  wildcard: a third party appears only when this deployment turned on the feature that needs it.
 *
 *  - Scripts run only when they carry this response's nonce, or when a script that carried it
 *    loaded them ('strict-dynamic', which covers Next's chunks, next/script and gtag). An inline
 *    script that came in with page content has no nonce, so it does not run, and that holds inside
 *    an `srcdoc` frame too, because such a frame inherits this policy.
 *  - 'unsafe-eval' stays, for two runtimes that turn text into code in the browser: the MDX
 *    renderer (`@mdx-js/mdx` `evaluate`, ui-kit/MdxDoc.tsx) and the canvas runtime, which compiles
 *    a view's source and runs it with `new Function` (canvas/runtime.tsx). It also lets the call
 *    blur's WebAssembly compile. Dropping it needs both runtimes off eval, not one.
 *  - Styles allow 'unsafe-inline': React renders `style` attributes, and the appearance tokens,
 *    mermaid's diagrams and LiveKit write `<style>` elements at run time. A nonce cannot cover
 *    attributes, and naming one would switch 'unsafe-inline' off for the elements. The one foreign
 *    stylesheet is Google Fonts', for the fonts a person can pick (platform/appearance.ts).
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

export function contentSecurityPolicy(nonce: string, sources: PolicySources = policySources()): string {
  const ga = (sources.gaMeasurementId ?? "").trim() !== "";
  const connect = ["'self'", ...livekitOrigins(sources.livekitUrl), ...(ga ? GA_CONNECT : [])];
  const img = ["'self'", "data:", "blob:", ...(ga ? GA_IMG : [])];
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic' 'unsafe-eval'`,
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
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

/** A fresh nonce: 128 random bits, base64. */
export function newNonce(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s);
}
