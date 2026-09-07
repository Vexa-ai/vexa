/** AN ACT RENDERS AS ITS LABEL — the second half of Vexa-ai/vexa#1588.
 *
 *  The founder pressed Extend and the chat painted the whole composed `[extend]` preset back at him
 *  as his own message — its "Expand means EVERY direction" section and all. An earlier Extend had
 *  shown `Extend: kg/entities/person/james-spadafora.md`.
 *
 *  The two halves of an act diverge on purpose and this is where the client half is decided: the
 *  BUBBLE is the label, because that is what the person did; the PROMPT is the whole selection,
 *  because that is what the agent has to read. The server half — the record a reload renders from —
 *  is pinned in `core/agent/tests/test_act_label.py`.
 */
import { describe, it, expect } from "vitest";
import { compactLabel, fallbackText } from "../extend";
import { normalizeIntent } from "../../surfaces/chatIntent";

describe("an act renders as its label — the composed prompt is the agent's business", () => {
  it("is the verb and the page, on both page acts", () => {
    const extend = normalizeIntent({ kind: "extend", path: "kg/entities/person/james-spadafora.md" })!;
    expect(compactLabel(extend)).toBe("Extend: kg/entities/person/james-spadafora.md");
    expect(compactLabel(normalizeIntent({ kind: "create", path: "kg/plan.md" })!)).toBe("Create: kg/plan.md");
  });

  it("stays one line, and never grows into the prompt", () => {
    const long = "Expand means EVERY direction. ".repeat(40);
    const i = normalizeIntent({ kind: "extend", path: "kg/plan.md", selection: long })!;
    const label = compactLabel(i);
    expect(label).not.toContain("\n");
    expect(label.length).toBeLessThan(160);
    // the AGENT still gets the whole selection — the two halves diverge on purpose
    expect(fallbackText(i)).toContain(long.trim().slice(0, 200));
  });
});
