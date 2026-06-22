/**
 * list-entry.tsx — the bundle entry the fixture page loads.
 *
 * esbuild bundles THIS (and the real MeetingsList source it imports) into `list-bundle.js`, a single
 * browser-runnable ESM module. The point of the L4 harness is that the page mounts the SAME component
 * a human's browser would — not a re-implementation. So this file mounts the REAL brick:
 *
 *   • imports `MeetingsList` straight from the brick's front door (../../src/index.ts)
 *   • exposes a `mount(el, meetings)` that React-renders it, wiring `onOpen` to push the clicked
 *     meeting onto `window.__opened` and stamp `window.__lastOpenedId` — the seam the spec reads to
 *     prove the click reached the callback.
 *
 * The `@vexa/dash-contracts` import inside MeetingsList is TYPE-ONLY (erased at compile), so the
 * bundle carries no contract runtime — exactly the brick's real footprint. React + react-dom DO get
 * bundled (the component's real runtime deps), resolved from this brick's node_modules.
 */
import * as React from "react";
import { createRoot } from "react-dom/client";
import { MeetingsList } from "../../src/index.js";

declare global {
  interface Window {
    __opened: unknown[];
    __lastOpenedId: string | null;
    mountMeetingsList: (el: HTMLElement, meetings: any[]) => void;
  }
}

export function mount(el: HTMLElement, meetings: any[]): void {
  window.__opened = [];
  window.__lastOpenedId = null;
  const root = createRoot(el);
  root.render(
    React.createElement(MeetingsList, {
      meetings,
      onOpen: (m: any) => {
        window.__opened.push(m);
        window.__lastOpenedId = String(m.id);
      },
    }),
  );
}

// expose for the inline page script (which is plain JS, not bundled)
window.mountMeetingsList = mount;
