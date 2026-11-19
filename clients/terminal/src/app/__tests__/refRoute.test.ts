/** `/r/<kind>/<id>` lands where the server says, or says one sentence (#1783). */
import { describe, it, expect } from "vitest";
import { refLanding } from "../r/refRoute";

const base = { kind: "chat", id: "c1", url: "/r/chat/c1", label: "x", request_access: false };

describe("refLanding", () => {
  it("opens what the reader may see, where it lives", () => {
    expect(refLanding({ ...base, access: "readable", open: { chat: "c1" } })).toEqual({ go: "/", session: "c1" });
    expect(refLanding({ ...base, kind: "meeting", id: "147", access: "readable", open: { meeting: 147 } })).toEqual({ go: "/meetings/147" });
    expect(refLanding({ ...base, kind: "workspace", id: "abcdefghij", access: "readable", open: { workspace: "deal" } })).toEqual({ go: "/w/abcdefghij" });
    expect(refLanding({ ...base, kind: "page", id: "abcdefghij/kg/x.md", access: "readable", open: { path: "kg/x.md", slug: "deal" } }))
      .toEqual({ go: "/w/abcdefghij/kg/x.md" });
    expect(refLanding({ ...base, kind: "join", id: "t", access: "grant", open: { join: "t" } })).toEqual({ go: "/join/t" });
  });
  it("says one sentence otherwise, and offers to ask only when an owner can be asked", () => {
    expect(refLanding({ ...base, access: "not-yours", label: null, open: null, request_access: true }))
      .toEqual({ say: "You don't have access to this.", ask: true });
    expect("say" in refLanding({ ...base, access: "gone", label: null, open: null })).toBe(true);
    expect("say" in refLanding(null)).toBe(true);
  });
});
