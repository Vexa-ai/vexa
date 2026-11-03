// THE MEETING CANVAS ORIGIN. A canvas view is workspace-written code, and it runs in a sandboxed
// frame (`/canvas-frame`, canvas/frame/protocol.ts) that connects nowhere. The one thing a sandbox
// cannot stop is the frame navigating ITSELF, and a navigation can carry the view's data in its URL.
// The embedding page's `frame-src` decides where such a navigation may go, so the frame is served
// from an origin of its own: the page frames that origin and no other, and every way out a view has
// lands on a host that serves the frame's own files and nothing else.
//
// TERMINAL_CANVAS_ORIGIN names that origin (`https://canvas.vexa.example`, or the same host on a
// second port, `http://localhost:3002`). The same server answers it (server.mjs routes by Host, or by
// the TERMINAL_CANVAS_PORT listener). Unset, the frame stays on the terminal's own origin, as it
// always was, and the terminal says so at start. Pure: read by server.mjs, the middleware and the
// pages, from the same environment.

/** The frame document's path, on whichever origin serves it (= canvas/frame/protocol.ts). */
export const CANVAS_FRAME_PATH = "/canvas-frame";

/** An http(s) origin with nothing after it, or null. */
function httpOrigin(raw) {
  let u;
  try { u = new URL(raw); } catch { return null; }
  if (u.protocol !== "http:" && u.protocol !== "https:") return null;
  if (u.username || u.password || u.search || u.hash || (u.pathname !== "/" && u.pathname !== "")) return null;
  if (/[\s;,'*]/.test(raw)) return null;
  return u.origin;
}

/** The canvas origin this deployment serves the frame from, or "" (the terminal's own origin). */
export function canvasOrigin(env) {
  const raw = String(env.TERMINAL_CANVAS_ORIGIN ?? "").trim();
  return raw ? (httpOrigin(raw) ?? "") : "";
}

/** The terminal's own public origins (NEXTAUTH_URL, else TERMINAL_URL): the pages that may frame
 *  the canvas, and the only origin the canvas frame takes a message from. */
export function terminalOrigins(env) {
  const out = [];
  for (const v of [env.NEXTAUTH_URL, env.TERMINAL_URL]) {
    const raw = String(v ?? "").trim();
    if (!raw) continue;
    let u;
    try { u = new URL(raw); } catch { continue; }
    if ((u.protocol === "http:" || u.protocol === "https:") && !out.includes(u.origin)) out.push(u.origin);
  }
  return out;
}

/** The second listener's port (compose, Lite), or 0 for none. */
export function canvasPort(env) {
  const raw = String(env.TERMINAL_CANVAS_PORT ?? "").trim();
  if (!/^\d{1,5}$/.test(raw)) return 0;
  const n = Number(raw);
  return n >= 1 && n <= 65535 ? n : 0;
}

/** Why this environment's canvas settings cannot run, naming the key, or null. */
export function canvasStartupError(env) {
  const raw = String(env.TERMINAL_CANVAS_ORIGIN ?? "").trim();
  const port = String(env.TERMINAL_CANVAS_PORT ?? "").trim();
  if (port && !canvasPort(env)) return `TERMINAL_CANVAS_PORT must be a port number (1-65535), got "${port}"`;
  if (!raw) return null;
  const origin = httpOrigin(raw);
  if (!origin) return `TERMINAL_CANVAS_ORIGIN must be an http(s) origin with no path, e.g. https://canvas.vexa.example or http://localhost:3002 (got "${raw}")`;
  const terminal = terminalOrigins(env);
  if (!terminal.length) return "TERMINAL_CANVAS_ORIGIN is set but NEXTAUTH_URL (or TERMINAL_URL) is not: the canvas origin lets only the terminal's own origin frame it, so the terminal's public origin must be configured";
  if (terminal.includes(origin)) return `TERMINAL_CANVAS_ORIGIN (${origin}) must differ from the terminal's own origin: it is what keeps a canvas view away from the terminal`;
  return null;
}

/** What the terminal says at start when the canvas shares its origin, or null when it does not. */
export function canvasStartupWarning(env) {
  if (canvasOrigin(env)) return null;
  return "[terminal] TERMINAL_CANVAS_ORIGIN is unset: Meeting Canvas views run in a sandboxed frame on the terminal's own origin. " +
    "The terminal refuses the requests that frame can make to it, but full isolation needs an origin of the canvas's own " +
    "(a second host or port; see docs: Meeting Canvas isolation).";
}

/** The canvas frame's URL as the page should mount it: absolute on the canvas origin, else the path. */
export function canvasFrameUrl(origin) {
  return origin ? `${origin}${CANVAS_FRAME_PATH}` : CANVAS_FRAME_PATH;
}
