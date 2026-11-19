/** S7 client (PRD 0.13.4 D17–D19; UI 7aa, 7cc, 7ee, 7gg): the README draws the server's per-viewer
 *  tree as one document; Share is "Send to…" with one quiet access line; references copy and paste. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ReadmeDoc, type PageTree } from "../ReadmeView";
import { accessLine, sendReference } from "../ShareDialog";
import { clipboardPayload, meetingLinkIn, pasteToChip, referenceFromPaste } from "../../../surfaces/reference";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const TREE: PageTree = {
  v: 1, workspace: "tinkerers", path: "README.md", role: "reader",
  nodes: [
    { t: "md", text: "# Tinkerers" },
    { t: "line", parts: [{ t: "text", text: "Venue: " }, { t: "ref", key: "page:w/venue.md" }, { t: "text", text: " and " }, { t: "ref", key: "chat:s-1" }] },
    { t: "block", name: "rooms", title: "Rooms", rows: [{ key: "s-1", title: "general", detail: "3 members" }], more: 2 },
    { t: "block", name: "crm", rows: [{ key: "co1", title: "OeNB", type: "Company", fields: [{ name: "Name", value: "OeNB" }, { name: "Stage", value: "Negotiation", only_you: true }] }] },
    { t: "block", name: "commitments", state: "pending" },
    { t: "line", parts: [{ t: "action", verb: "new-room", label: "New room", args: { name: "x" }, enabled: false, reason: "Read-only for you here", confirm: true }] },
  ],
  refs: { "page:w/venue.md": { kind: "page", access: "readable", label: "Venue notes", open: { path: "venue.md" } }, "chat:s-1": { kind: "chat", access: "not-yours", request_access: true } },
};

describe("ReadmeDoc", () => {
  it("draws one document for this viewer: chips, rows, only-you, restricted without a name", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const { container } = render(<ReadmeDoc tree={TREE} />);
    expect(screen.getByText("Venue notes")).toBeTruthy();
    expect(screen.getByText("Restricted")).toBeTruthy();
    expect(screen.getByText("general")).toBeTruthy();
    expect(screen.getByText("Show 2 more")).toBeTruthy();
    expect(screen.getByText(/only you see this/)).toBeTruthy();
    expect(screen.getByText("Cards will show here.")).toBeTruthy();
    const act = screen.getByRole("button", { name: /New room/ }) as HTMLButtonElement;
    expect(act.disabled).toBe(true);
    expect(screen.getByText("Read-only for you here")).toBeTruthy();
    expect(container.textContent).not.toMatch(/\{\{|\}\}/);
    expect(fetchSpy).not.toHaveBeenCalled();          // nothing ran on render
  });
});

describe("Share = send a reference", () => {
  it("one quiet line only when access differs", async () => {
    const f = vi.fn(async () => new Response(JSON.stringify({ cannot: ["Maya"], cannot_count: 1, where: "your desk", everyone: false }), { status: 200 }));
    expect(await accessLine("[[chat:s-1]]", { kind: "room", id: "r1", name: "general" }, f as unknown as typeof fetch))
      .toBe("Maya can't open this: it's in your desk. Share anyway?");
    const all = vi.fn(async () => new Response(JSON.stringify({ cannot: [], cannot_count: 0, everyone: true }), { status: 200 }));
    expect(await accessLine("[[chat:s-1]]", { kind: "room", id: "r1", name: "general" }, all as unknown as typeof fetch)).toBeNull();
  });
  it("Send posts the chip as a message, nothing else", async () => {
    const f = vi.fn(async () => new Response("{}", { status: 200 }));
    expect(await sendReference("r1", "[[meeting:153@00:12:30]]", f as unknown as typeof fetch)).toBe(true);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/sessions/r1/messages");
    expect(JSON.parse(String(init.body))).toEqual({ text: "[[meeting:153@00:12:30]]" });
  });
});

describe("copy reference and paste", () => {
  it("puts the reference and the URL on the clipboard", () => {
    const p = clipboardPayload({ kind: "meeting", id: "153", anchor: "t=00:12:30", label: "Call" });
    expect(p.text).toMatch(/\/r\/meeting\/153#t=00:12:30$/);
    expect(p.html).toContain('data-vexa-ref="[[meeting:153@00:12:30]]"');
    const data = { getData: (t: string) => (t === "text/html" ? p.html : p.text) };
    expect(referenceFromPaste(data)).toBe("[[meeting:153@00:12:30]]");
  });
  it("a pasted Vexa URL becomes a chip in two edits (⌘Z brings the link back)", () => {
    const ta = document.createElement("textarea");
    document.body.appendChild(ta);
    ta.value = "see ";
    ta.setSelectionRange(4, 4);
    const out = pasteToChip(ta, "https://app.test/r/chat/c-1", "[[chat:c-1]]");
    expect(out).toBe("see [[chat:c-1]]");
  });
  it("a pasted meeting link offers Send Vexa", () => {
    expect(meetingLinkIn("join https://meet.google.com/abc-defg-hij now")?.meeting.platform).toBe("google_meet");
    expect(meetingLinkIn("nothing here")).toBeNull();
  });
});
