/**
 * render-entry.tsx — the bundle entry the fixture page loads.
 *
 * esbuild bundles THIS (and the REAL TranscriptViewer source it imports, plus react/react-dom) into
 * `render-bundle.js`, a single browser-runnable ESM module. The whole point of the L4 harness is that
 * the page runs the SAME component code a human's browser would — not a re-implementation. So this file
 * does nothing but mount the real component from the brick front door (`src/index.ts`) into #root over
 * the golden props, then drop a #harness-ready marker.
 *
 * `@vexa/dash-contracts` imports inside the component are TYPE-ONLY (erased at compile), so the bundle
 * carries no contract runtime — exactly the brick's real footprint.
 */
import React from "react";
import { createRoot } from "react-dom/client";
import { TranscriptViewer } from "../../src/index.ts";
import { GOLDEN_SEGMENTS } from "./golden.js";

function mount() {
  try {
    const root = document.getElementById("root");
    if (!root) throw new Error("#root missing");

    createRoot(root).render(
      React.createElement(TranscriptViewer, {
        segments: GOLDEN_SEGMENTS,
        isLive: true,
        // onSegmentClick provided so the segments render as clickable (cursor:pointer path).
        onSegmentClick: () => {},
      }),
    );

    // React.createRoot renders asynchronously; wait a tick before signalling ready.
    requestAnimationFrame(() => {
      const ready = document.createElement("div");
      ready.id = "harness-ready";
      document.body.appendChild(ready);
    });
  } catch (err) {
    const marker = document.createElement("pre");
    marker.id = "harness-error";
    marker.textContent = String((err && err.stack) || err);
    document.body.appendChild(marker);
  }
}

mount();
