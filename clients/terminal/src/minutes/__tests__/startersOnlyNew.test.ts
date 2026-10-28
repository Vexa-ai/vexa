/** STARTERS ONLY IN A NEW, EMPTY CHAT (UI decisions 4; live app.dev: the pills showed in a routine
 *  chat with history). */
import { describe, expect, it } from "vitest";
import { startersFor } from "../proposals";

const chips = [{ id: "a" }, { id: "b" }] as never[];

describe("starters", () => {
  it("show in the new chat the person just opened", () => {
    expect(startersFor({ chat: "d1", draft: "d1", spent: null, chips })).toHaveLength(2);
  });
  it("never in a chat that already exists — a routine's, a meeting's, any with history", () => {
    expect(startersFor({ chat: "pchat-x", draft: "d1", spent: null, chips })).toEqual([]);
    expect(startersFor({ chat: "pchat-x", draft: null, spent: null, chips })).toEqual([]);
  });
  it("and not once the new chat spent its offer", () => {
    expect(startersFor({ chat: "d1", draft: "d1", spent: "d1", chips })).toEqual([]);
  });
});
