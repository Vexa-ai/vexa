/**
 * render-entry.tsx — the bundle entry the fixture page loads.
 *
 * esbuild bundles THIS (and the real StatusHistory source it imports) into render-bundle.js, a single
 * browser-runnable ESM module. The L4 point: the page mounts the SAME component a human's browser would
 * render — not a re-implementation. So this entry pulls StatusHistory straight from the brick SOURCE
 * (src/index.ts, the one front door), mounts it with the golden transitions into #root via react-dom,
 * and drops a #harness-ready marker (or #harness-error on throw) for the spec to await.
 *
 * The @vexa/dash-contracts import inside StatusHistory is TYPE-ONLY (erased at compile), so the bundle
 * carries no contract runtime — exactly the brick's real footprint.
 */
import React from "react";
import { createRoot } from "react-dom/client";
import { StatusHistory } from "../../src/index.ts";
import { GOLDEN_TRANSITIONS } from "./golden.js";

function fail(err: unknown) {
  const marker = document.createElement("pre");
  marker.id = "harness-error";
  marker.textContent = String((err as Error)?.stack ?? err);
  document.body.appendChild(marker);
}

try {
  const root = document.getElementById("root")!;
  createRoot(root).render(
    React.createElement(StatusHistory, { transitions: GOLDEN_TRANSITIONS as any }),
  );

  // React renders synchronously on the first commit in this path, but flush a microtask so the DOM is
  // settled before we plant the ready marker the spec waits on.
  queueMicrotask(() => {
    const ready = document.createElement("div");
    ready.id = "harness-ready";
    document.body.appendChild(ready);
  });
} catch (err) {
  fail(err);
}
