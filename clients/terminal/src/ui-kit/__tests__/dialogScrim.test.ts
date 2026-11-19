import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

// "delete does not work" (founder, app.dev, 2026-10-11 — Vexa-ai/vexa#1829): the delete confirm
// opened, and its "Delete chat" button could not be pressed. Inside `.vx-dialog-layer` the scrim
// kept the shared `.vx-scrim` z-index (the drawer stratum) while `.vx-dialog` had none, so the
// scrim painted OVER the dialog and every click on the dialog hit the scrim's onClick → cancel.
// jsdom has no hit-testing, so the component tests clicked the button straight through; this reads
// the cascade the browser applies to the two boxes instead.
const css = ["primitives/controls.css", "layout/layout.css"].map((f) => readFileSync(join(__dirname, "..", f), "utf8")).join("\n");

/** The last declaration of `prop` in rules whose selector list contains exactly `selector`. */
function decl(selector: string, prop: string): string | undefined {
  let found: string | undefined;
  for (const m of css.replace(/\/\*[\s\S]*?\*\//g, "").matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const sels = m[1].split(",").map((s) => s.trim().replace(/\s+/g, " "));
    if (!sels.includes(selector)) continue;
    for (const d of m[2].split(";")) {
      const [k, ...v] = d.split(":");
      if (k?.trim() === prop) found = v.join(":").trim();
    }
  }
  return found;
}

describe("a dialog is above its own scrim", () => {
  it("the scrim in the dialog layer does not out-stack the dialog", () => {
    // `.vx-dialog-layer > .vx-scrim` outranks `.vx-scrim` (two classes against one), so its value
    // wins when it sets one; otherwise the shared rule's stratum applies.
    const scrim = decl(".vx-dialog-layer > .vx-scrim", "z-index") ?? decl(".vx-scrim", "z-index") ?? "auto";
    const dialog = decl(".vx-dialog", "z-index") ?? "auto";
    expect(dialog === "auto" ? scrim === "auto" : scrim === "auto" || Number(scrim) < Number(dialog)).toBe(true);
  });
});
