/** A CARD'S FACE, sanitized again in the browser (#1783, desktop ADR §2.2, D2). crm-api resolves the
 *  face per viewer and sanitizes it; the client drops again so a face can never draw more than the
 *  blocks.v1 face profile allows: unknown blocks dropped, 8 blocks, labels 40, text 160, rows 4,
 *  checklist 10, progress clamped, a due date only as YYYY-MM-DD, a locked field as its label only. */
import { describe, expect, it } from "vitest";
import { DEFAULT_FACE, FACE_LIMITS, sanitizeFace } from "../desktop/faceBlocks";

describe("sanitizeFace", () => {
  it("keeps the structural blocks by type only, and drops anything they carry", () => {
    expect(sanitizeFace([{ type: "stepper", html: "<b>x</b>" }, { type: "people" }, { type: "movement" }, { type: "waiting" },
      { type: "next" }, { type: "subcards" }, { type: "ref" }, { type: "status" }]))
      .toEqual([{ type: "stepper" }, { type: "people" }, { type: "movement" }, { type: "waiting" }, { type: "next" }, { type: "subcards" }, { type: "ref" }, { type: "status" }]);
  });

  it("drops unknown blocks, non-objects and data blocks without their data", () => {
    expect(sanitizeFace([{ type: "script" }, null, "text", 3, { type: "metric" }, { type: "text" }, { type: "list" }, { type: "checklist" },
      { type: "progress", value: "x" }, { type: "due", at: "next week" }, { type: "locked" }])).toEqual([]);
    expect(sanitizeFace("nope")).toEqual([]);
    expect(sanitizeFace(undefined)).toEqual([]);
  });

  it("caps the face at 8 blocks and clips labels, text, rows and checklist items", () => {
    const many = Array.from({ length: 12 }, () => ({ type: "next" }));
    expect(sanitizeFace(many)).toHaveLength(FACE_LIMITS.blocks);
    const long = "x".repeat(400);
    const [metric, text, list, check, locked] = sanitizeFace([
      { type: "metric", label: long, value: long },
      { type: "text", label: long, text: long },
      { type: "list", rows: [long, "b", "c", "d", "e", "f"] },
      { type: "checklist", items: Array.from({ length: 14 }, (_, i) => ({ text: `t${i}`, done: i < 3 })) },
      { type: "locked", label: long },
    ]) as Array<Record<string, unknown>>;
    expect((metric.label as string).length).toBe(40);
    expect((metric.value as string).length).toBe(160);
    expect((text.text as string).length).toBe(160);
    expect((list.rows as string[]).length).toBe(4);
    expect((list.rows as string[])[0].length).toBe(160);
    expect((check.items as unknown[]).length).toBe(10);
    expect(locked).toEqual({ type: "locked", label: "x".repeat(40) });
  });

  it("clamps progress to 0..1 and keeps a due date only as a calendar day", () => {
    expect(sanitizeFace([{ type: "progress", value: 1.7 }, { type: "progress", value: -1, label: "Done" }, { type: "due", at: "2026-10-22", label: "Due" }]))
      .toEqual([{ type: "progress", value: 1 }, { type: "progress", value: 0, label: "Done" }, { type: "due", at: "2026-10-22", label: "Due" }]);
  });

  it("never passes a link, a markdown string as markup, or an action through", () => {
    const [t] = sanitizeFace([{ type: "text", text: "[click](javascript:alert(1))", href: "https://x", onClick: "x" }]) as Array<Record<string, unknown>>;
    expect(Object.keys(t).sort()).toEqual(["text", "type"]);
  });

  it("has the type's default face for a card that stores none", () => {
    expect(DEFAULT_FACE.map((b) => b.type)).toEqual(["stepper", "next", "waiting", "movement", "subcards", "people"]);
  });
});
