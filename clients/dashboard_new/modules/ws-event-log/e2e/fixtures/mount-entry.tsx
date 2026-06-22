/**
 * mount-entry.tsx — the bundle entry the fixture page loads.
 *
 * esbuild bundles THIS (plus the REAL component source it imports, plus react/react-dom) into
 * `mount-bundle.js`, a single browser-runnable ESM module. The whole point of the L4 gate is that the
 * page mounts the SAME component a human's browser would — not a re-implementation — so this file does
 * nothing but mount the brick's real front-door component over the golden events into #root.
 *
 * We import the component by its relative source front door (`../../src/index.ts`, not the package name)
 * so the gate exercises the actual brick file. The @vexa/dash-contracts import inside is TYPE-ONLY and
 * erased at compile, so the bundle carries no contract runtime.
 */
import * as React from "react";
import { createRoot } from "react-dom/client";
import { WsEventLog } from "../../src/index.js";
import { GOLDEN_EVENTS } from "./golden.js";

const root = document.getElementById("root");
if (!root) throw new Error("missing #root");

createRoot(root).render(
  React.createElement(WsEventLog, { events: GOLDEN_EVENTS }),
);

// deterministic ready marker for the spec to await (appended after the synchronous render call)
const ready = document.createElement("div");
ready.id = "harness-ready";
document.body.appendChild(ready);
