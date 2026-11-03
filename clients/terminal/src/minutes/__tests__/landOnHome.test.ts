/** JTBD-1 blocker 1: opening the app lands on the home page (home2), not the last chat — unless the
 *  address names something to open. */
import { describe, expect, it } from "vitest";
import { landsOnHome } from "../landOnHome";

describe("landsOnHome", () => {
  it("lands on home at / with home2 on", () => {
    expect(landsOnHome({ home2: true, pathname: "/", search: "", hash: "", deepLinked: false })).toBe(true);
  });
  it("keeps the old restore with home2 off", () => {
    expect(landsOnHome({ home2: false, pathname: "/", search: "", hash: "", deepLinked: false })).toBe(false);
  });
  it("never overrides an address that names something", () => {
    expect(landsOnHome({ home2: true, pathname: "/w/acme/README.md", search: "", hash: "", deepLinked: false })).toBe(false);
    expect(landsOnHome({ home2: true, pathname: "/", search: "?session=abc", hash: "", deepLinked: false })).toBe(false);
    expect(landsOnHome({ home2: true, pathname: "/", search: "", hash: "#m=1", deepLinked: false })).toBe(false);
    expect(landsOnHome({ home2: true, pathname: "/", search: "", hash: "", deepLinked: true })).toBe(false);
  });
});
