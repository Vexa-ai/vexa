// THE FRONT DOOR'S ANSWER TO THE CANVAS FRAME (server.mjs, before Next sees a request).
//
// A Meeting Canvas view runs in a sandboxed, opaque-origin frame. A sandbox closes popups, forms and
// top navigation, and the frame's policy closes every fetch, but a view can still make its frame ask
// a server for a URL — navigating itself, or loading a script, a stylesheet or a font from a source
// its policy names — and put the data it was given into that URL. So the door answers two hosts:
//
//  • THE CANVAS HOST (TERMINAL_CANVAS_ORIGIN, src/app/canvasOrigin.mjs) serves the frame document and
//    the build's own files, by exact path and with no query, and nothing else: a URL a view made up is
//    refused here, before routing, and never reaches a handler or a log.
//  • THE TERMINAL refuses to be loaded INTO a frame by another origin, and refuses a script, a
//    stylesheet, a font or a worker another origin asks for, except the build's own files. A browser
//    names the requester in `Sec-Fetch-Site`; the canvas frame's origin is opaque, so every request it
//    makes is `cross-site`, whichever host it targets. The terminal's own pages are `same-origin`, and
//    a link followed from elsewhere is a `document`, which this leaves alone (the sandbox already
//    keeps the frame from navigating the top page). With a canvas host configured, `/canvas-frame` is
//    not served on the terminal at all.
//
// A refusal carries `x-vexa-refused` (what refused it), a policy that runs nothing, and a body that
// never echoes the URL.
import { statSync } from "node:fs";
import { join, normalize, sep } from "node:path";
import { CANVAS_FRAME_PATH } from "../canvasOrigin.mjs";

export const REFUSED_HEADER = "x-vexa-refused";

/** `Sec-Fetch-Dest` values that load the URL as a nested document. */
const NESTED = new Set(["iframe", "frame", "embed", "object", "fencedframe"]);
/** `Sec-Fetch-Dest` values that load the URL as code or styling. */
const SUBRESOURCE = new Set(["script", "style", "font", "worker", "sharedworker", "serviceworker", "manifest",
  "audioworklet", "paintworklet", "xslt", "json"]);
const BUILD_PREFIX = "/_next/static/";

function split(url) {
  const raw = String(url || "/");
  const q = raw.indexOf("?");
  return { path: q < 0 ? raw : raw.slice(0, q), query: q < 0 ? "" : raw.slice(q + 1) };
}

function header(req, name) {
  const v = req.headers?.[name];
  return (Array.isArray(v) ? v[0] : v ?? "").toString().toLowerCase();
}

/** A file the build wrote under `.next/static`, named exactly. In development the files appear as
 *  they compile, so the prefix alone is the test there. */
export function isBuildAsset(path, { staticDir, dev = false }) {
  if (!path.startsWith(BUILD_PREFIX)) return false;
  let rel;
  try { rel = decodeURIComponent(path.slice(BUILD_PREFIX.length)); } catch { return false; }
  if (!rel || rel.includes("\0") || rel.split("/").some((s) => s === ".." || s === ".")) return false;
  if (dev) return true;
  const file = normalize(join(staticDir, rel));
  if (!file.startsWith(normalize(staticDir) + sep)) return false;
  try { return statSync(file).isFile(); } catch { return false; }
}

/** What the canvas host refuses: everything but the frame document and the build's own files. */
export function canvasDoorRefusal(req, opts) {
  const method = String(req.method || "GET").toUpperCase();
  if (method !== "GET" && method !== "HEAD") return { status: 405, reason: "canvas-host" };
  const { path, query } = split(req.url);
  if (query) return { status: 404, reason: "canvas-host" };
  if (path === CANVAS_FRAME_PATH) {
    const dest = header(req, "sec-fetch-dest");
    return dest && dest !== "iframe" ? { status: 404, reason: "canvas-host" } : null;
  }
  return isBuildAsset(path, opts) ? null : { status: 404, reason: "canvas-host" };
}

/** What the terminal refuses: being framed by another origin, code or styling another origin asks
 *  for (but the build's files), and `/canvas-frame` itself when a canvas host serves it. */
export function terminalDoorRefusal(req, { canvasConfigured = false, ...opts }) {
  const { path, query } = split(req.url);
  if (canvasConfigured && path === CANVAS_FRAME_PATH) return { status: 404, reason: "canvas-on-its-own-origin" };
  const site = header(req, "sec-fetch-site");
  if (site !== "cross-site" && site !== "same-site") return null;
  const dest = header(req, "sec-fetch-dest");
  if (NESTED.has(dest)) return { status: 403, reason: "framed-from-another-origin" };
  if (SUBRESOURCE.has(dest) && !(query === "" && isBuildAsset(path, opts))) return { status: 403, reason: "subresource-for-another-origin" };
  return null;
}

/** Answer a refusal: no route, no log, nothing echoed. */
export function refuse(res, { status, reason }) {
  if (res.headersSent) return;
  res.writeHead(status, {
    "Content-Type": "text/plain; charset=utf-8",
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    [REFUSED_HEADER]: reason,
  });
  res.end(status === 404 ? "Not found\n" : status === 405 ? "Method not allowed\n" : "Refused\n");
}
