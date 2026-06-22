/**
 * chat-entry.tsx — the bundle entry the L4 fixture page loads.
 *
 * esbuild bundles THIS (and the real @vexa/dash-chat source it imports) into `chat-bundle.js`, a single
 * browser-runnable ESM module with react + react-dom inlined. The point of the L4 harness is that the
 * page mounts the SAME component a human's browser would render — not a re-implementation. So this entry
 * does nothing but mount the real `ChatPanel` (imported by relative source path from the brick's front
 * door) over the golden messages, into #root, and drop a deterministic ready marker the spec awaits.
 *
 * The @vexa/dash-contracts import inside the component is TYPE-ONLY (erased at compile), so the bundle
 * carries no contract runtime — exactly the brick's real footprint.
 */
import * as React from "react";
import { createRoot } from "react-dom/client";
// import the brick by its real front-door SOURCE path (like the dash-ws e2e does), so the bundle
// exercises the actual component file, not a built artifact.
import { ChatPanel } from "../src/index.ts";
import { GOLDEN_MESSAGES } from "./golden.js";

function fail(err: unknown): void {
  const marker = document.createElement("pre");
  marker.id = "harness-error";
  marker.textContent = String((err as Error)?.stack ?? err);
  document.body.appendChild(marker);
}

try {
  const rootEl = document.getElementById("root");
  if (!rootEl) throw new Error("no #root element in fixture page");

  // mount the REAL component over the golden props, the way a dashboard page would
  createRoot(rootEl).render(React.createElement(ChatPanel, { messages: GOLDEN_MESSAGES }));

  // deterministic ready marker for the spec to await (React commits synchronously enough here, but the
  // spec also waits on the rendered bubbles, so this just signals "mount didn't throw")
  const ready = document.createElement("div");
  ready.id = "harness-ready";
  document.body.appendChild(ready);
} catch (err) {
  fail(err);
}
