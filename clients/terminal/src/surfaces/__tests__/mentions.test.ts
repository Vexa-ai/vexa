/** ADR-0048 — @-mentions as pure functions: the picker offers only the people the server listed,
 *  choosing one writes `@Label ` and remembers the account id, and a mention deleted from the text is
 *  not sent. */
import { describe, it, expect } from "vitest";
import { activeMention, insertMention, labelOf, mentionsInText, pickerRows } from "../mentions";

const anna = { subject: "u_anna", name: "Anna Berg", email: "anna@acme.test", workspaces: ["acme"] };
const joe = { subject: "u_joe", name: "", email: "joe@acme.test", workspaces: ["acme"] };

describe("mentions", () => {
  it("an @ at a word start opens the picker; an address does not", () => {
    expect(activeMention("hi @an", 6)).toEqual({ start: 3, query: "an" });
    expect(activeMention("@", 1)).toEqual({ start: 0, query: "" });
    expect(activeMention("mail joe@acme", 13)).toBeNull();
    expect(activeMention("hi @an there", 12)).toBeNull();
  });

  it("the picker offers only who the server listed, agents first", () => {
    expect(pickerRows([anna, joe], [{ name: "vexa" }], "").map((r) => r.label)).toEqual(["vexa", "Anna", "joe"]);
    expect(pickerRows([anna, joe], [{ name: "vexa" }], "jo").map((r) => r.label)).toEqual(["joe"]);
    expect(pickerRows([anna, joe], [], "dave")).toEqual([]);
    expect(labelOf(anna)).toBe("Anna");
  });

  it("choosing writes @Label and the mention carries the account id until the text drops it", () => {
    const at = activeMention("share with @an", 14)!;
    const next = insertMention("share with @an", at, "Anna");
    expect(next.text).toBe("share with @Anna ");
    const picked = [{ subject: "u_anna", label: "Anna", name: "Anna Berg" }];
    expect(mentionsInText(next.text, picked).map((m) => m.subject)).toEqual(["u_anna"]);
    expect(mentionsInText("share with Anna", picked)).toEqual([]);
    expect(mentionsInText("share with @Annabel", picked)).toEqual([]);
  });
});
