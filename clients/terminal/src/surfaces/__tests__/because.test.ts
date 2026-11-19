/** R1 2.2 — the room agent's reason line. An act nobody asked for begins with `_because …_`
 *  (founder: "it acts only when it adds something, and says why"); the room shows it as one quiet
 *  line above the act, never as italic prose inside it. */
import { describe, expect, it } from "vitest";
import { splitBecause } from "../because";

describe("splitBecause", () => {
  it("lifts a leading _because …_ line off the turn", () => {
    expect(splitBecause("_because the agenda still says 80_\n\n**Proposal:** capacity 120")).toEqual({ because: "because the agenda still says 80", body: "**Proposal:** capacity 120" });
  });
  it("accepts *because …* and a capital B", () => {
    expect(splitBecause("*Because you asked in the call*\nDone.").because).toBe("because you asked in the call");
  });
  it("leaves a turn without one alone, and never lifts a because from the middle", () => {
    expect(splitBecause("Done.\n_because x_")).toEqual({ because: null, body: "Done.\n_because x_" });
    expect(splitBecause("_because_ alone").because).toBeNull();
  });
});
