"use client";
/** canvasSource — ONE shared loader for the agent-authored meeting view (`views/meeting.tsx`).
 *
 *  Previously every mounted MeetingCanvasView ran its own 4s poll; dockview keeps inactive tabs mounted,
 *  so N open meeting tabs meant N× the same fetch forever (hundreds/min). This module owns a single,
 *  refcounted poll for the whole app: the interval runs only while at least one canvas is mounted, and
 *  all canvases read the same source via `useSyncExternalStore`. Subscribers are notified only when the
 *  source actually changes, so an unchanged poll costs one fetch and zero re-renders. */
import { useSyncExternalStore } from "react";

// No client subject: the gateway injects X-User-Id and agent-api derives `subject` from it (P20 scope).
const VIEW_PATH = "views/meeting.tsx";
const POLL_MS = 4000;

export const FALLBACK_SOURCE = `
export default function Component() {
  return React.createElement(ui.Empty, { title: "No canvas view", body: "Create views/meeting.tsx in the workspace." });
}
`;

interface SourceState { source: string; stamp: string }

let state: SourceState = { source: FALLBACK_SOURCE, stamp: "" };
const subs = new Set<() => void>();
let refs = 0;
let timer: number | undefined;
let onFocus: (() => void) | undefined;
let inflight = false;

function stampNow(): string {
  return new Date().toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

async function load(): Promise<void> {
  if (inflight) return;
  inflight = true;
  try {
    const r = await fetch(`/api/workspace/file?path=${encodeURIComponent(VIEW_PATH)}`, { cache: "no-store" });
    const next = r.ok ? (((await r.json()) as { content?: string }).content?.trim() || FALLBACK_SOURCE) : FALLBACK_SOURCE;
    if (next !== state.source) {
      // Only the actual reload bumps the "reloaded" stamp and re-renders consumers.
      state = { source: next, stamp: stampNow() };
      subs.forEach((f) => f());
    }
  } catch {
    /* keep last-good source */
  } finally {
    inflight = false;
  }
}

function start(): void {
  if (timer != null || typeof window === "undefined") return;
  void load();
  timer = window.setInterval(() => void load(), POLL_MS);
  onFocus = () => void load();
  window.addEventListener("focus", onFocus);
}

function stop(): void {
  if (timer != null) { window.clearInterval(timer); timer = undefined; }
  if (onFocus) { window.removeEventListener("focus", onFocus); onFocus = undefined; }
}

/** Read the shared canvas source. Mounting subscribes (and starts the single poll); the last unmount
 *  stops it. Returns the last-good source + the stamp of when it last actually reloaded. */
export function useCanvasSource(): SourceState {
  return useSyncExternalStore(
    (cb) => {
      subs.add(cb);
      if (++refs === 1) start();
      return () => {
        subs.delete(cb);
        if (--refs <= 0) stop();
      };
    },
    () => state,
    () => state,
  );
}
