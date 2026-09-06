/** `/join?i=<token>` — the invite page's states.
 *
 *  The defect this suite stands on: the founder minted an invite, opened the link, and read
 *  *"not found"* (Vexa-ai/vexa#1635). There was no page. So every test here asserts the shape of a
 *  SENTENCE a person reads, and the one property that binds them all is that none of the five ways
 *  an invite can fail produces a blank screen or a status code.
 *
 *    preview        an unauthenticated visitor is told what the invite is BEFORE being asked who
 *                   they are, and is offered the instance's own sign-in carrying `next=/join?i=…`
 *    bound sign-in  a one-address invite prefills that address and locks the field — it is the only
 *                   address the redeem will accept
 *    expired        one sentence
 *    spent          one sentence
 *    wrong address  one sentence, from the redeem's own 403, after a sign-in that looked fine
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";

import JoinPage from "../join/page";
import {
  boundAddress,
  inviteSentence,
  inviterName,
  landingPath,
  refusal,
  refusalForAcceptStatus,
  refusalForPreviewStatus,
  refusalForReason,
  returnPath,
  roleSentence,
  type InvitePreview,
} from "../join/joinState";

// next-auth's client helper reaches for a session endpoint on import-time use; the page only ever
// calls signIn() from a click, and what matters is the callbackUrl it would carry.
const signInMock = vi.fn();
vi.mock("next-auth/react", () => ({ signIn: (...a: unknown[]) => signInMock(...a) }));

const TOKEN = "tok_pilot_invite";

/** A valid, bound invite from the pilot workspace. No real person, no real address (repo rule). */
function preview(over: Partial<InvitePreview> = {}): InvitePreview {
  return {
    workspace_id: "pilot-a1b2c3",
    id: "abcdefghij",
    name: "pilot",
    purpose: null,
    role: "contributor",
    mode: "restricted",
    restricted_to: ["jsmith@example.com"],
    shared_by: "jane@example.com",
    valid: true,
    reason: null,
    ...over,
  };
}

type Stub = {
  /** The preview endpoint's answer. */
  previewStatus?: number;
  previewBody?: InvitePreview | null;
  /** 200 = signed in, 401 = signed out. */
  meStatus?: number;
  /** What `POST /api/workspace/invites/accept` answers. */
  acceptStatus?: number;
  providers?: Record<string, boolean>;
};

/** One fetch double for every call the page makes, recording what was ASKED FOR — the `next=` a
 *  sign-in carries and the token a redeem sends are the two things that must be right. */
function stub(s: Stub) {
  const calls: { url: string; method: string; body?: string }[] = [];
  const json = (status: number, body: unknown) =>
    Promise.resolve({
      ok: status >= 200 && status < 300,
      status,
      json: () => Promise.resolve(body),
      text: () => Promise.resolve(JSON.stringify(body)),
    } as Response);

  vi.stubGlobal("fetch", (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, method: init?.method || "GET", body: init?.body as string | undefined });
    if (url.startsWith("/api/join/preview")) {
      return json(s.previewStatus ?? 200, s.previewBody === undefined ? preview() : s.previewBody);
    }
    if (url.startsWith("/api/auth/me")) {
      const st = s.meStatus ?? 401;
      return json(st, st === 200 ? { authenticated: true, user: { email: "jsmith@example.com" } } : { authenticated: false });
    }
    if (url.startsWith("/api/auth/providers")) return json(200, s.providers ?? {});
    if (url.startsWith("/api/auth/request-link")) return json(200, { ok: true });
    if (url.startsWith("/api/workspace/invites/accept")) {
      const st = s.acceptStatus ?? 200;
      return json(st, st === 200 ? { workspace_id: "pilot-a1b2c3", role: "contributor" } : { detail: "no" });
    }
    if (url.startsWith("/api/workspaces/by-slug/")) return json(200, { id: "abcdefghij", name: "pilot" });
    return json(404, {});
  });
  return calls;
}

/** jsdom cannot navigate; `window.location.assign` is [Unforgeable] so a spy on it records
 *  nothing either way. The tests below assert what IS observable — the accept POST and the screen
 *  — never the navigation. */
beforeEach(() => {
  window.history.replaceState({}, "", `/join?i=${TOKEN}`);
  try { vi.spyOn(window.location, "assign").mockImplementation(() => {}); } catch { /* jsdom warning */ }
  signInMock.mockReset();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

// ── the sentences, with no DOM ────────────────────────────────────────────────────────────────
describe("the sentence a person reads", () => {
  it("names the inviter, the workspace, the role and what the role can do", () => {
    expect(inviteSentence(preview({ name: "pilot", shared_by: "jane@example.com" })))
      .toBe("Jane invited you to pilot as a contributor: you can read and write its pages.");
  });

  it("says what a viewer can do, and never claims a write", () => {
    expect(roleSentence("viewer")).toBe("you can read its pages");
    expect(roleSentence("contributor")).toContain("write");
  });

  it("never invents a name it does not have", () => {
    expect(inviterName("")).toBe("Someone");
    expect(inviterName("u_7f3a")).toBe("u_7f3a");   // an opaque subject, said plainly
    expect(inviterName("jsmith@example.com")).toBe("Jsmith");
  });

  it("has one sentence for every refusal, and a sentence for the ones it does not know", () => {
    for (const k of ["no-token", "unknown", "expired", "spent", "revoked", "wrong-address", "unreachable"] as const) {
      expect(refusal(k).length).toBeGreaterThan(20);
      expect(refusal(k)).not.toMatch(/404|not found/i);
    }
  });

  it("maps every reason and status onto a refusal, never onto nothing", () => {
    expect(refusalForReason("expired")).toBe("expired");
    expect(refusalForReason("used_up")).toBe("spent");
    expect(refusalForReason("revoked")).toBe("revoked");
    expect(refusalForReason("something-new")).toBe("unknown");
    expect(refusalForPreviewStatus(404)).toBe("unknown");
    expect(refusalForPreviewStatus(502)).toBe("unreachable");
    expect(refusalForAcceptStatus(403)).toBe("wrong-address");
    expect(refusalForAcceptStatus(410)).toBe("spent");
  });

  it("locks a single bound address and nothing else", () => {
    expect(boundAddress(preview())).toBe("jsmith@example.com");
    expect(boundAddress(preview({ mode: "open", restricted_to: [] }))).toBeNull();
    // Two named people share one invite: we cannot know which of them is at the keyboard.
    expect(boundAddress(preview({ restricted_to: ["a@example.com", "b@example.com"] }))).toBeNull();
  });

  it("lands on the workspace's front page, and on the terminal when the id did not resolve", () => {
    expect(landingPath("abcdefghij")).toBe("/w/abcdefghij");
    expect(landingPath("")).toBe("/");
    expect(returnPath("t o k")).toBe("/join?i=t%20o%20k");
  });
});

// ── the page ──────────────────────────────────────────────────────────────────────────────────
describe("the join page", () => {
  it("tells an unauthenticated visitor what the invite is BEFORE asking who they are", async () => {
    stub({ meStatus: 401 });
    render(<JoinPage />);
    await waitFor(() =>
      expect(screen.getByTestId("join-sentence").textContent)
        .toBe("Jane invited you to pilot as a contributor: you can read and write its pages."));
    expect(screen.getByTestId("join-email")).toBeTruthy();
  });

  it("prefills and LOCKS the bound address, and the sign-in link returns to this invite", async () => {
    const calls = stub({ meStatus: 401 });
    render(<JoinPage />);
    const field = (await screen.findByTestId("join-email")) as HTMLInputElement;
    expect(field.value).toBe("jsmith@example.com");
    expect(field.readOnly).toBe(true);

    fireEvent.submit(field.closest("form")!);
    await waitFor(() => expect(calls.some((c) => c.url.startsWith("/api/auth/request-link"))).toBe(true));
    const req = calls.find((c) => c.url.startsWith("/api/auth/request-link"))!;
    expect(JSON.parse(req.body!)).toEqual({ email: "jsmith@example.com", next: `/join?i=${TOKEN}` });
  });

  it("leaves the field open when the invite is open, and says the link works once", async () => {
    stub({ meStatus: 401, previewBody: preview({ mode: "open", restricted_to: [] }) });
    render(<JoinPage />);
    const field = (await screen.findByTestId("join-email")) as HTMLInputElement;
    expect(field.value).toBe("");
    expect(field.readOnly).toBe(false);
  });

  it("carries the same return path through an OAuth door", async () => {
    stub({ meStatus: 401, providers: { google: true } });
    render(<JoinPage />);
    const btn = await screen.findByText("Continue with Google");
    fireEvent.click(btn);
    expect(signInMock).toHaveBeenCalledWith("google", { callbackUrl: `/join?i=${TOKEN}` });
  });

  it("redeems immediately for a visitor who is already signed in", async () => {
    const calls = stub({ meStatus: 200 });
    render(<JoinPage />);
    await waitFor(() =>
      expect(calls.some((c) => c.url.startsWith("/api/workspace/invites/accept"))).toBe(true));
    const accept = calls.find((c) => c.url.startsWith("/api/workspace/invites/accept"))!;
    expect(JSON.parse(accept.body!)).toEqual({ token: TOKEN });
    // and it asks the registry which page to land on
    await waitFor(() => expect(calls.some((c) => c.url.startsWith("/api/workspaces/by-slug/"))).toBe(true));
  });

  it("says an expired invite is expired — never a 404", async () => {
    stub({ meStatus: 401, previewBody: preview({ valid: false, reason: "expired" }) });
    render(<JoinPage />);
    await waitFor(() => expect(screen.getByTestId("join-refused").textContent).toBe(refusal("expired")));
    expect(screen.queryByTestId("join-email")).toBeNull();
  });

  it("says a spent invite has been used", async () => {
    stub({ meStatus: 401, previewBody: preview({ valid: false, reason: "used_up" }) });
    render(<JoinPage />);
    await waitFor(() => expect(screen.getByTestId("join-refused").textContent).toBe(refusal("spent")));
  });

  it("says an unknown token is not valid, without saying whether the workspace exists", async () => {
    stub({ meStatus: 401, previewStatus: 404, previewBody: null });
    render(<JoinPage />);
    await waitFor(() => expect(screen.getByTestId("join-refused").textContent).toBe(refusal("unknown")));
  });

  it("says WHICH address, when a signed-in person is not the one the invite named", async () => {
    stub({ meStatus: 200, acceptStatus: 403 });
    render(<JoinPage />);
    await waitFor(() => expect(screen.getByTestId("join-refused").textContent).toBe(refusal("wrong-address")));
  });

  it("accepts the older ?invite= spelling of the parameter", async () => {
    // The terminal's own in-app share controls mint `<origin>/?invite=` and are redeemed by
    // InviteGate inside the signed-in shell. This page does not take those over; the alias only
    // means a /join url carrying the older parameter name redeems, instead of reading as a link
    // with no invite in it.
    window.history.replaceState({}, "", `/join?invite=${TOKEN}`);
    stub({ meStatus: 401 });
    render(<JoinPage />);
    await waitFor(() => expect(screen.getByTestId("join-sentence")).toBeTruthy());
  });

  it("says something when the link carries no invite at all", async () => {
    window.history.replaceState({}, "", "/join");
    stub({});
    render(<JoinPage />);
    await waitFor(() => expect(screen.getByTestId("join-refused").textContent).toBe(refusal("no-token")));
  });
});
