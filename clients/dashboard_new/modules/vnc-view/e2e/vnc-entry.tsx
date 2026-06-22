/**
 * vnc-entry.tsx — the fixture's mount entry, bundled into vnc-bundle.js by esbuild from the brick SOURCE.
 *
 * Imports the REAL <VncView> through the brick front door (../src/index.ts) and exposes a single
 * `mountVncView(rootEl, props)` the fixture page calls. Bundling from source (not dist) means the page
 * always exercises the CURRENT component. The page reads which props to mount from the URL query
 * (`?mode=url` → golden vncUrl; `?mode=empty` → vncUrl="") so one bundle serves both L4 cases.
 */
import * as React from "react";
import { createRoot } from "react-dom/client";
import { VncView } from "../src/index.js";
import type { VncViewProps } from "../src/index.js";

export function mountVncView(rootEl: HTMLElement, props: VncViewProps): void {
  const root = createRoot(rootEl);
  root.render(React.createElement(VncView, props));
}

// Expose on window so the plain-HTML fixture (no module bundler at runtime) can call it.
(globalThis as unknown as { mountVncView: typeof mountVncView }).mountVncView = mountVncView;
