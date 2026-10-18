/** Universal references in the client (#1783): one grammar (`/r/<kind>/<id>` and `[[kind:id|label]]`),
 *  a pasted link becomes a chip, and every chip asks the SERVER what it points at for this reader —
 *  the client never builds a slug or a path from a name (the ü bug). */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const answers: Record<string, unknown> = {};
const requested: string[] = [];
vi.mock("../../data/workspaceApi", () => ({
  resolveRef: vi.fn(async (ref: string) => answers[ref] ?? { access: "gone", kind: "chat", id: "x", label: null, open: null, request_access: false, url: "/r/chat/x" }),
  requestRefAccess: vi.fn(async (ref: string) => { requested.push(ref); return { requested: true }; }),
  resolveLinks: vi.fn(async () => []),
  listWorkspaceTree: vi.fn(async () => []),
  readActiveSet: vi.fn(async () => ({ subject: "u", active: [] })),
}));

import { parseUniversalRef, pastedRefs, refUrl, splitRefs, invalidateRefCache } from "../universalRefs";
import { RefChip } from "../RefChip";

beforeEach(() => { for (const k of Object.keys(answers)) delete answers[k]; requested.length = 0; invalidateRefCache(); });
afterEach(cleanup);

describe("the grammar", () => {
  it("reads a URL from any host and a chip, for every kind", () => {
    for (const kind of ["workspace", "chat", "page", "entity", "meeting", "flow", "sensor", "room", "message", "routine", "person", "series"]) {
      const id = kind === "page" ? "abcdefghij/kg/entities/company/muhlemann-popp-ag.md" : "x1";
      const u = parseUniversalRef(`https://app.dev.vexa.ai${refUrl(kind, id)}`);
      expect(u).toEqual({ kind, id, label: undefined });
      expect(parseUniversalRef(`[[${kind}:${id}|Shown]]`)).toEqual({ kind, id, label: "Shown" });
    }
    expect(parseUniversalRef("[[mühlemann+popp AG]]")).toBeNull();
    expect(parseUniversalRef("https://app.dev.vexa.ai/r/chat/../../x")).toBeNull();
  });

  it("splits text into words and refs, chips and bare URLs alike", () => {
    const parts = splitRefs("see [[chat:c1|Board]] and https://app.dev.vexa.ai/r/meeting/147.");
    expect(parts.map((p) => p.kind)).toEqual(["text", "ref", "text", "ref", "text"]);
    expect(parts[3]).toMatchObject({ kind: "ref", ref: { kind: "meeting", id: "147" } });
  });

  it("keeps a meeting moment and a heading as the anchor", () => {
    expect(parseUniversalRef("[[meeting:147@0:12:30]]")).toEqual({ kind: "meeting", id: "147", label: undefined, anchor: "t=00:12:30" });
    expect(parseUniversalRef("https://app.dev.vexa.ai/r/meeting/147#t=00:12:30")).toEqual({ kind: "meeting", id: "147", label: undefined, anchor: "t=00:12:30" });
    expect(parseUniversalRef("[[call:147]]")).toEqual({ kind: "meeting", id: "147", label: undefined });
    expect(pastedRefs("https://app.dev.vexa.ai/r/meeting/147#t=00:12:30")).toBe("[[meeting:147@00:12:30]]");
  });

  it("a pasted link becomes a chip; other text is left alone", () => {
    expect(pastedRefs("https://app.dev.vexa.ai/r/chat/c-123")).toBe("[[chat:c-123]]");
    expect(pastedRefs("look at https://app.dev.vexa.ai/r/workspace/abcdefghij please"))
      .toBe("look at [[workspace:abcdefghij]] please");
    expect(pastedRefs("just words")).toBeNull();
  });
});

describe("RefChip — the server's answer for this reader", () => {
  it("readable: the server's label, never the writer's", async () => {
    answers["[[chat:c1|Typed by writer]]"] = { access: "readable", kind: "chat", id: "c1", label: "Board prep", open: { chat: "c1" }, request_access: false, url: "/r/chat/c1" };
    render(<RefChip text="[[chat:c1|Typed by writer]]" />);
    expect(await screen.findByText("Board prep")).toBeTruthy();
  });

  it("not yours: says so, names nothing, and offers to ask the owner", async () => {
    answers["[[chat:c2|Secret title]]"] = { access: "not-yours", kind: "chat", id: "c2", label: null, open: null, request_access: true, url: "/r/chat/c2" };
    render(<RefChip text="[[chat:c2|Secret title]]" />);
    expect(await screen.findByText("Restricted")).toBeTruthy();
    expect(screen.queryByText("Secret title")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Request access" }));
    await waitFor(() => expect(requested).toEqual(["[[chat:c2|Secret title]]"]));
    expect(await screen.findByText("Access requested")).toBeTruthy();
  });

  it("gone: a calm word, not a dead chip", async () => {
    render(<RefChip text="https://app.dev.vexa.ai/r/chat/nope" />);
    expect(await screen.findByText("Not found")).toBeTruthy();
  });
});
