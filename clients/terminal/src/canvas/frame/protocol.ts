/** The Meeting Canvas frame's wire: what the terminal page and the isolated view frame say to each
 *  other, and nothing else.
 *
 *  A canvas view is workspace-written code. It runs in its own document (`/canvas-frame`), served
 *  with `sandbox allow-scripts` and no `allow-same-origin` — an OPAQUE origin, so it holds none of
 *  the terminal's cookies, cannot read the terminal's DOM or storage, and its own policy
 *  (`canvasFrameContentSecurityPolicy`) lets it connect nowhere. Its only channel is this one:
 *  the page posts the source and the meeting in; the frame posts its status, its height and the
 *  actions a person pressed out, and the page decides what each action does. */
import type { HarnessActions, MeetingState } from "../types";

/** Where the frame document is served, on the canvas origin when the deployment names one
 *  (`TERMINAL_CANVAS_ORIGIN`, app/canvasOrigin.mjs), else on the terminal's own. */
export const CANVAS_FRAME_PATH = "/canvas-frame";
/** The `<meta>` the root layout writes the canvas origin into, for the page to mount the frame from. */
export const CANVAS_ORIGIN_META = "vexa-canvas-origin";

/** The frame's URL for a page: on the canvas origin the layout names, else this origin's path. */
export function canvasFrameSrc(doc: Document | undefined = typeof document === "undefined" ? undefined : document): string {
  const origin = doc?.querySelector(`meta[name="${CANVAS_ORIGIN_META}"]`)?.getAttribute("content") ?? "";
  try {
    const u = new URL(origin);
    if (u.protocol === "https:" || u.protocol === "http:") return `${u.origin}${CANVAS_FRAME_PATH}`;
  } catch { /* none named */ }
  return CANVAS_FRAME_PATH;
}

/** The origin a message from the frame arrives with: a sandboxed document's origin is opaque, and a
 *  browser serializes it as "null" — whichever host served it. */
export const FRAME_MESSAGE_ORIGIN = "null";

/** The page's origins the frame takes a message from and posts back to: those the server named
 *  (the terminal's public origins, when the frame has an origin of its own), else the origin the
 *  frame document was served from, which is then the terminal's. */
export function parentOriginsFor(named: readonly string[], frameUrl: string): string[] {
  if (named.length) return [...named];
  try { return [new URL(frameUrl).origin]; } catch { return []; }
}
/** The iframe's sandbox: scripts, and nothing that would give the frame an origin, a popup, a form,
 *  a top-level navigation or a download. NEVER add `allow-same-origin`: with it the frame would be
 *  the terminal's origin again, and the view could lift its own sandbox. */
export const CANVAS_FRAME_SANDBOX = "allow-scripts";

const PREFIX = "vexa-canvas:";

/** page → frame */
export type ToFrame =
  | { type: "vexa-canvas:render"; source: string; state: MeetingState; theme: string }
  | { type: "vexa-canvas:state"; state: MeetingState; theme: string };

/** frame → page */
export type FromFrame =
  | { type: "vexa-canvas:ready" }
  | { type: "vexa-canvas:status"; source: string; ok: boolean; errors: string[] }
  | { type: "vexa-canvas:size"; height: number }
  | { type: "vexa-canvas:action"; name: ActionName; args: unknown[] };

/** Every action a view may ask the page for — `HarnessActions`, by name. */
export const ACTION_NAMES = [
  "ask", "research", "openDoc", "copyRef", "note", "pin", "dismiss", "setMetric", "tag", "export",
] as const satisfies readonly (keyof HarnessActions)[];
export type ActionName = (typeof ACTION_NAMES)[number];

function text(v: unknown, max = 4000): string {
  return typeof v === "string" ? v.slice(0, max) : typeof v === "number" ? String(v) : "";
}

/** A message the frame posted, checked into its typed shape — or null. The frame is untrusted: a
 *  message is read only for the fields this protocol names, and an action only with arguments of
 *  the types its `HarnessActions` signature declares. */
export function readFromFrame(data: unknown): FromFrame | null {
  if (!data || typeof data !== "object") return null;
  const m = data as Record<string, unknown>;
  if (typeof m.type !== "string" || !m.type.startsWith(PREFIX)) return null;
  switch (m.type) {
    case "vexa-canvas:ready":
      return { type: m.type };
    case "vexa-canvas:status":
      return {
        type: m.type, source: text(m.source, 200_000), ok: m.ok === true,
        errors: Array.isArray(m.errors) ? m.errors.slice(0, 50).map((e) => text(e)) : [],
      };
    case "vexa-canvas:size": {
      const h = typeof m.height === "number" && Number.isFinite(m.height) ? m.height : 0;
      return { type: m.type, height: Math.max(0, Math.min(Math.round(h), 20_000)) };
    }
    case "vexa-canvas:action": {
      const name = m.name as ActionName;
      if (!(ACTION_NAMES as readonly string[]).includes(name)) return null;
      return { type: m.type, name, args: Array.isArray(m.args) ? m.args.slice(0, 2) : [] };
    }
    default:
      return null;
  }
}

/** Run one action the frame asked for against the page's real actions, each argument coerced to
 *  the type its signature declares. */
export function runAction(actions: HarnessActions, name: ActionName, args: unknown[]): void {
  const [a, b] = args;
  switch (name) {
    case "ask": return actions.ask(text(a));
    case "research": {
      const e = (a && typeof a === "object" ? a : {}) as Record<string, unknown>;
      return actions.research({ name: text(e.name, 300), kind: text(e.kind, 40) });
    }
    case "openDoc": return actions.openDoc(text(a, 1000));
    case "copyRef": return actions.copyRef(text(a, 1000));
    case "note": return actions.note(text(a));
    case "pin": return actions.pin(text(a, 300));
    case "dismiss": return actions.dismiss(text(a, 300));
    case "setMetric": return actions.setMetric(text(a, 200), typeof b === "number" && Number.isFinite(b) ? b : text(b, 200));
    case "tag": return actions.tag(text(a, 300), text(b, 100));
    case "export": return actions.export();
  }
}

/** The meeting as plain data — what crosses the frame boundary (structured clone refuses functions,
 *  and nothing but data belongs on the other side). */
export function plainState(state: MeetingState): MeetingState {
  return JSON.parse(JSON.stringify(state)) as MeetingState;
}

/** A message the page posted, as the frame reads it — or null. */
export function readToFrame(data: unknown): ToFrame | null {
  if (!data || typeof data !== "object") return null;
  const m = data as Record<string, unknown>;
  if (m.type !== "vexa-canvas:render" && m.type !== "vexa-canvas:state") return null;
  if (!m.state || typeof m.state !== "object") return null;
  const theme = m.theme === "light" ? "light" : "dark";
  if (m.type === "vexa-canvas:render") {
    if (typeof m.source !== "string") return null;
    return { type: m.type, source: m.source, state: m.state as MeetingState, theme };
  }
  return { type: m.type, state: m.state as MeetingState, theme };
}
