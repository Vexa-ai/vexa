/** Low `location.assign`: a URL that came from data is followed only when it stays on this origin. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { navigateWithin, pathWithin } from "../navigate";
import { openRef } from "../../ui-kit/RefChip";
import { openNotice } from "../../minutes/notifications";

const ORIGIN = "https://app.example.test";
let assign: ReturnType<typeof vi.fn>;
function stubLocation() {
  assign = vi.fn();
  vi.stubGlobal("location", { ...window.location, origin: ORIGIN, assign });
}
afterEach(() => vi.unstubAllGlobals());

describe("pathWithin", () => {
  it("keeps a path on this origin", () => {
    expect(pathWithin("/w/notes?x=1#h", ORIGIN)).toBe("/w/notes?x=1#h");
    expect(pathWithin("/?s=abc", ORIGIN)).toBe("/?s=abc");
  });
  it.each(["javascript:alert(1)", "https://evil.example/x", "//evil.example/x", "/\\evil.example", "\\\\evil", "data:text/html,x", "/a\nb", "", null, 7])(
    "refuses %s", (bad) => { expect(pathWithin(bad, ORIGIN)).toBeNull(); });
});

describe("the data-driven navigations use it", () => {
  it("a reference's answer that leaves the origin lands on /", () => {
    stubLocation();
    openRef({ url: "javascript:alert(document.cookie)" } as never);
    openRef({ url: "https://evil.example/phish" } as never);
    expect(assign.mock.calls).toEqual([["/"], ["/"]]);
  });

  it("a join grant's token is encoded into one path segment", () => {
    stubLocation();
    openRef({ open: { join: "../../api/x?y" } } as never);
    expect(assign).toHaveBeenCalledWith("/join/..%2F..%2Fapi%2Fx%3Fy");
  });

  it("a notice's object URL that leaves the origin lands on /", () => {
    stubLocation();
    openNotice({ id: "n1", kind: "agent", read: false, object: { kind: "page", id: "p", url: "javascript:alert(1)" } } as never);
    expect(assign).toHaveBeenCalledWith("/");
  });

  it("positive control: an on-origin URL is followed as is", () => {
    stubLocation();
    navigateWithin("/w/notes");
    openRef({ url: "/r/page/1" } as never);
    expect(assign.mock.calls).toEqual([["/w/notes"], ["/r/page/1"]]);
  });
});
