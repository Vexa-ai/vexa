/**
 * form-entry.tsx — the bundle entry the fixture page loads.
 *
 * esbuild bundles THIS (plus the real JoinForm brick source it imports, plus react/react-dom) into
 * `form-bundle.js`, a single browser-runnable ESM module. The point of the L4 harness is that the page
 * mounts the SAME component a human's browser would — not a re-implementation. So this file does the
 * minimum: import `JoinForm` from the brick front door (../../src/index.ts), mount it into #root with
 * react-dom, and wire `onSubmit` to record the request onto `window.__submitted` so the Playwright spec
 * can assert exactly what the component produced.
 *
 * We import the brick by relative SOURCE path (not `@vexa/dash-join-form`) on purpose: the brick's one
 * front door is `src/index.ts`. The `@vexa/dash-contracts` imports inside it are type-only (erased at
 * compile), so the bundle carries no contract runtime — exactly the brick's real footprint.
 */
import { createRoot } from "react-dom/client";
import { JoinForm } from "../../src/index.ts";
import type { CreateBotRequest } from "../../src/index.ts";
import { GOLDEN_DEFAULT_BOT_NAME } from "./golden.js";

declare global {
  interface Window {
    /** Every onSubmit payload the component fires, in order. The spec reads this. */
    __submitted: CreateBotRequest[];
  }
}

window.__submitted = [];

const root = createRoot(document.getElementById("root")!);
root.render(
  <JoinForm
    defaultBotName={GOLDEN_DEFAULT_BOT_NAME}
    onSubmit={(request) => {
      window.__submitted.push(request);
    }}
  />,
);

// deterministic ready marker for the spec to await — the component is mounted once this exists.
const ready = document.createElement("div");
ready.id = "harness-ready";
document.body.appendChild(ready);
