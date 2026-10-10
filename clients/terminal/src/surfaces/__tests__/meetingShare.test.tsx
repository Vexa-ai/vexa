/** Sharing a meeting from the terminal: the one Share action, the access list, and explicit errors.
 *
 *  The access rules are the server's (meeting-api / agent-api deny tests); these pin what the
 *  terminal does with them: the owner gets the dialog and a recipient never does, each invite is
 *  restricted to its own address, removals call the owner routes, and every refusal is shown in
 *  words rather than swallowed (P18). */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import { MeetingShareButton, MeetingShareDialog, SharedIndicator } from "../MeetingShare";
import { parseEmails, shareUrl, sharedWithCount } from "../meetingShareApi";
import { shareRefusalSentence } from "../../app/App";
import { ApiError } from "../apiClient";
import { meetingHealth } from "../../canvas/meetingHealth";
import type { MeetingMock } from "../meetingModel";

function meeting(over: Partial<MeetingMock> = {}): MeetingMock {
  return {
    id: "42", native_id: "abc-defg-hij", title: "Weekly", when: "now", status: "past", live_status: "completed",
    platform: "Google Meet", docs: [], participants: [], mentioned: [], actions: [], transcript: [], insights: [],
    ...over,
  } as MeetingMock;
}

const ACCESS = {
  meeting_id: 42, workspace_id: null, recording: false, links: [],
  people: [{ user_id: 8, email: "reader@example.test", role: "viewer", grant_id: "g1" }],
  invites: [{ id: "g2", mode: "restricted", emails: ["pending@example.test"] }],
};

type Call = { url: string; method: string; body?: unknown };
let calls: Call[];
let respond: (c: Call) => { status: number; body: unknown };

beforeEach(() => {
  calls = [];
  respond = () => ({ status: 200, body: ACCESS });
  globalThis.fetch = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const c: Call = { url: String(url), method: (init?.method || "GET").toUpperCase(), body: init?.body ? JSON.parse(String(init.body)) : undefined };
    calls.push(c);
    const r = respond(c);
    return { ok: r.status < 400, status: r.status, json: async () => r.body } as Response;
  }) as unknown as typeof fetch;
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("pure helpers", () => {
  it("parses addresses and names what it refused", () => {
    expect(parseEmails("A@x.io, b@y.io; a@x.io  nope")).toEqual({ emails: ["a@x.io", "b@y.io"], invalid: ["nope"] });
  });
  it("builds the arrival link the terminal already redeems", () => {
    expect(shareUrl("https://app.example", "7.s3cr3t")).toBe("https://app.example/?tshare=7.s3cr3t");
    expect(shareUrl("https://app.example", "7.s", "inv")).toBe("https://app.example/?tshare=7.s&invite=inv");
  });
  it("counts readers only from the owner's row data", () => {
    expect(sharedWithCount({ transcript_viewers: [8, 9] })).toBe(2);
    expect(sharedWithCount(null)).toBe(0);
  });
});

describe("the indicator is quiet words, never a chip", () => {
  it("owner with readers", () => {
    render(<SharedIndicator meeting={{ shared: false, shared_with: 3 }} />);
    expect(screen.getByText("Shared with 3")).toBeTruthy();
  });
  it("recipient", () => {
    render(<SharedIndicator meeting={{ shared: true }} />);
    expect(screen.getByText("Shared with you")).toBeTruthy();
  });
  it("nothing when nobody else can read it", () => {
    const { container } = render(<SharedIndicator meeting={{ shared: false, shared_with: 0 }} />);
    expect(container.textContent).toBe("");
  });
});

describe("MeetingShareButton", () => {
  it("a recipient gets no Share action — managing access is the owner's", () => {
    render(<MeetingShareButton meeting={meeting({ shared: true })} />);
    expect(screen.queryByRole("button", { name: /share/i })).toBeNull();
    expect(screen.getByText("Shared with you")).toBeTruthy();
  });
  it("the owner opens the dialog", async () => {
    render(<MeetingShareButton meeting={meeting()} />);
    fireEvent.click(screen.getByRole("button", { name: /share/i }));
    expect(await screen.findByRole("dialog", { name: "Share meeting" })).toBeTruthy();
    expect(await screen.findByText("reader@example.test")).toBeTruthy();
  });
});

describe("MeetingShareDialog", () => {
  it("lists readers and pending invites, and removes through the owner routes", async () => {
    render(<MeetingShareDialog meeting={meeting()} onClose={() => {}} origin="https://app.example" />);
    await screen.findByText("reader@example.test");
    expect(screen.getByText("pending@example.test")).toBeTruthy();
    const removes = screen.getAllByRole("button", { name: "Remove" });
    fireEvent.click(removes[0]);
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.url === "/api/meetings/42/viewers/8")).toBe(true));
    fireEvent.click(screen.getAllByRole("button", { name: "Remove" })[1]);
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.url === "/api/meetings/42/share/g2")).toBe(true));
  });

  it("invites one person per address, each restricted to that address, and shows each link", async () => {
    respond = (c) => c.url.endsWith("/share") && c.method === "POST"
      ? { status: 200, body: { id: "g9", token: "42.tok", mode: "restricted", expires_at: "x",
          notified: { [(c.body as { allowed_emails: string[] }).allowed_emails[0]]: (c.body as { allowed_emails: string[] }).allowed_emails[0] === "a@x.io" } } }
      : { status: 200, body: ACCESS };
    render(<MeetingShareDialog meeting={meeting()} onClose={() => {}} origin="https://app.example" />);
    await screen.findByText("reader@example.test");
    fireEvent.change(screen.getByLabelText("Email addresses"), { target: { value: "a@x.io, b@y.io" } });
    fireEvent.click(screen.getByRole("button", { name: "Invite" }));
    await screen.findByText("a@x.io");
    const mints = calls.filter((c) => c.method === "POST" && c.url === "/api/meetings/42/share");
    expect(mints.map((c) => c.body)).toEqual([
      expect.objectContaining({ mode: "restricted", allowed_emails: ["a@x.io"], notify: true }),
      expect.objectContaining({ mode: "restricted", allowed_emails: ["b@y.io"], notify: true }),
    ]);
    expect(screen.getAllByRole("button", { name: "Copy link" })).toHaveLength(2);
    // the mail that landed is said, and the one that did not is said too — with the way round it
    expect(screen.getByText(/Emailed/).textContent).toMatch(/a@x\.io/);
    expect(screen.getByText(/could not be sent/).textContent).toMatch(/b@y\.io/);
  });

  it("refuses a malformed address in words, and sends nothing", async () => {
    render(<MeetingShareDialog meeting={meeting()} onClose={() => {}} origin="https://app.example" />);
    await screen.findByText("reader@example.test");
    fireEvent.change(screen.getByLabelText("Email addresses"), { target: { value: "not-an-email" } });
    fireEvent.click(screen.getByRole("button", { name: "Invite" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/Not an email address: not-an-email/);
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("a failed load is an explicit, retryable error — never an empty list", async () => {
    respond = () => ({ status: 502, body: { detail: "upstream down" } });
    render(<MeetingShareDialog meeting={meeting()} onClose={() => {}} origin="https://app.example" />);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/Could not load who has access/);
    expect(screen.queryByText("Only you.")).toBeNull();
  });

  it("the recording switch is the owner's PATCH", async () => {
    render(<MeetingShareDialog meeting={meeting()} onClose={() => {}} origin="https://app.example" />);
    await screen.findByText("reader@example.test");
    fireEvent.click(screen.getByRole("checkbox"));
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ recording: true }));
  });
});

describe("a recipient whose access ends", () => {
  it("a refused share link says why", () => {
    expect(shareRefusalSentence(new ApiError(403, "not_allowed", "/x"))).toMatch(/different email address/);
    expect(shareRefusalSentence(new ApiError(403, "revoked", "/x"))).toMatch(/withdrawn/);
    expect(shareRefusalSentence(new ApiError(404, "invalid or unknown share token", "/x"))).toMatch(/not valid/);
  });
  it("a removal mid-meeting is an error state, not a quiet 'ended'", () => {
    const issue = { kind: "access" as const, message: "You no longer have access to this meeting.", status: 403, at: 1 };
    const h = meetingHealth({ ended: true, issues: [issue] }, 2, true);
    expect(h.kind).toBe("error");
    expect(h.latestIssue?.kind).toBe("access");
  });
});

describe("arriving through a share link opens that meeting in either shell", () => {
  afterEach(() => { vi.unstubAllEnvs(); localStorage.clear(); });
  it("the workbench key always, and the minutes shell's ref in the minutes product", async () => {
    const { stashSharedMeeting } = await import("../../app/App");
    stashSharedMeeting(13);
    expect(localStorage.getItem("vexa.openMeeting")).toBe("13");
    expect(localStorage.getItem("vexa.openMeetingRef")).toBeNull();
    vi.stubEnv("NEXT_PUBLIC_TERMINAL_MODE", "minutes");
    stashSharedMeeting(14);
    expect(localStorage.getItem("vexa.openMeetingRef")).toBe("14");
  });
});
