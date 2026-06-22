/**
 * mount-empty-entry.tsx — the empty-state bundle entry.
 *
 * Mounts the REAL WsEventLog with an empty events array so the L4 gate can assert the empty-state DOM
 * branch ([data-testid="ws-event-empty"]) in a real browser, the same way mount-entry.tsx asserts the
 * populated branch.
 */
import * as React from "react";
import { createRoot } from "react-dom/client";
import { WsEventLog } from "../../src/index.js";

const root = document.getElementById("root");
if (!root) throw new Error("missing #root");

createRoot(root).render(React.createElement(WsEventLog, { events: [] }));

const ready = document.createElement("div");
ready.id = "harness-ready";
document.body.appendChild(ready);
