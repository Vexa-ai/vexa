/** A session that dies while the app is open must LOOK like a dead session (2026-09-01).
 *
 *  The defect these pin: a login token was revoked server-side, `/api/auth/me` answered "signed in"
 *  from the mere presence of a cookie, the login gate therefore never re-checked, and the full shell
 *  rendered over an app where every request 401'd. The only thing the user was told was a chat turn
 *  ending in "Something went wrong — details are in the browser console" — which came from the
 *  gateway's JSON refusal body being too payload-shaped for the presenter to read as prose.
 *
 *  Three seams, three groups below: the raiser (any auth-shaped refusal reports itself), the chat
 *  stream (an SSE-folded 401 is the session, not a mystery), and the gate (a confirmed 401 takes the
 *  screen and offers exactly one way back, landing where the user was).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";

import { SESSION_EXPIRED_EVENT, noteAuthFailure, onSessionSuspect } from "../session";
import { getJson, SESSION_ENDED_HEADLINE } from "../../surfaces/apiClient";
import { streamChatTurn, type ChatStreamCallbacks } from "../../surfaces/chatStream";
import { AuthGate } from "../AuthGate";

vi.mock("next-auth/react", () => ({ signIn: vi.fn() }));

function noopCallbacks(over: Partial<ChatStreamCallbacks> = {}): ChatStreamCallbacks {
  return {
    onDelta: () => {}, onTool: () => {}, onCommit: () => {}, onRejected: () => {},
    onModelFailure: () => {}, onError: () => {}, onStarting: () => {}, ...over,
  };
}

/** One SSE body, delivered as a single chunk. */
function sseResponse(lines: string): Response {
  const body = new ReadableStream<Uint8Array>({
    start(c) { c.enqueue(new TextEncoder().encode(lines)); c.close(); },
  });
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

beforeEach(() => {
  vi.spyOn(console, "warn").mockImplementation(() => undefined);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("session-suspect signal", () => {
  it("raises on 401 and 403, and on nothing else", () => {
    const seen: number[] = [];
    const off = onSessionSuspect((d) => seen.push(d.status));
    for (const s of [200, 404, 422, 500, 502, 401, 403]) noteAuthFailure(s, "/api/x");
    off();
    expect(seen).toEqual([401, 403]);
  });

  it("stops delivering once unsubscribed", () => {
    const seen: number[] = [];
    onSessionSuspect((d) => seen.push(d.status))();
    noteAuthFailure(401, "/api/x");
    expect(seen).toEqual([]);
  });

  it("getJson reports an auth-shaped refusal before it throws", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Invalid token" }), { status: 401 })));
    const seen: number[] = [];
    const off = onSessionSuspect((d) => seen.push(d.status));
    await expect(getJson("/api/meetings")).rejects.toThrow();
    off();
    expect(seen).toEqual([401]);
  });

  it("getJson stays silent on a non-auth failure", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("boom", { status: 500 })));
    const seen: number[] = [];
    const off = onSessionSuspect((d) => seen.push(d.status));
    await expect(getJson("/api/meetings")).rejects.toThrow();
    off();
    expect(seen).toEqual([]);
  });
});

describe("chatStream — an auth refusal folded into the stream", () => {
  it("a 401 arriving as an SSE error event reads as the session, not as a mystery", async () => {
    // THE FOUNDER'S EXACT SYMPTOM. /api/chat answers 200 and folds the gateway's refusal into an
    // `error` event; the message is the gateway's raw JSON body, which the presenter could not read
    // as prose, so the turn died under the generic console-pointer headline.
    const raw = '{"detail":"Invalid API key"}';
    vi.stubGlobal("fetch", vi.fn(async () =>
      sseResponse(`data: ${JSON.stringify({ type: "error", message: raw, status: 401 })}\n\n`)));

    const errors: string[] = [];
    const seen: number[] = [];
    const off = onSessionSuspect((d) => seen.push(d.status));
    await streamChatTurn(
      { prompt: "hi", session: "s", active: undefined },
      noopCallbacks({ onError: (m) => errors.push(m) }),
      { signal: new AbortController().signal, hardTimeoutMs: 50, sleep: async () => {} },
    );
    off();

    expect(errors).toEqual([SESSION_ENDED_HEADLINE]);
    expect(errors[0]).not.toContain("{");
    expect(errors[0]).not.toContain("Something went wrong");
    expect(seen).toEqual([401]);
  });

  it("a non-auth stream error still surfaces its own message verbatim", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      sseResponse(`data: ${JSON.stringify({ type: "error", message: "the model provider is down", status: 503 })}\n\n`)));

    const errors: string[] = [];
    const seen: number[] = [];
    const off = onSessionSuspect((d) => seen.push(d.status));
    await streamChatTurn(
      { prompt: "hi", session: "s", active: undefined },
      noopCallbacks({ onError: (m) => errors.push(m) }),
      { signal: new AbortController().signal, hardTimeoutMs: 50, sleep: async () => {} },
    );
    off();

    expect(errors).toEqual(["the model provider is down"]);
    expect(seen).toEqual([]);
  });

  it("a bare 401 RESPONSE (no SSE) is also the session", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("nope", { status: 401 })));
    const errors: string[] = [];
    const seen: number[] = [];
    const off = onSessionSuspect((d) => seen.push(d.status));
    await streamChatTurn(
      { prompt: "hi", session: "s", active: undefined },
      noopCallbacks({ onError: (m) => errors.push(m) }),
      { signal: new AbortController().signal, hardTimeoutMs: 50, sleep: async () => {} },
    );
    off();
    expect(errors).toEqual([SESSION_ENDED_HEADLINE]);
    expect(seen).toEqual([401]);
  });
});

describe("AuthGate — the gate reacts to a session dying under it", () => {
  /** Route the gate's probes: /api/auth/me answers whatever `meOk` currently says. */
  function stubGate(meOk: () => boolean, onRequestLink?: (body: unknown) => void) {
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.startsWith("/api/auth/me")) {
        return meOk()
          ? new Response(JSON.stringify({ authenticated: true, user: { email: "a@b.c" } }), { status: 200 })
          : new Response(JSON.stringify({ authenticated: false, reason: "session_ended" }), { status: 401 });
      }
      if (u.startsWith("/api/auth/providers")) return new Response("{}", { status: 200 });
      if (u.startsWith("/api/auth/instance")) return new Response(JSON.stringify({ admin_exists: true }), { status: 200 });
      if (u.startsWith("/api/auth/request-link")) {
        onRequestLink?.(JSON.parse(String(init?.body ?? "{}")));
        return new Response("{}", { status: 200 });
      }
      return new Response("{}", { status: 200 });
    }));
  }

  it("a confirmed 401 replaces the app with the signed-out state — never a console pointer", async () => {
    let alive = true;
    stubGate(() => alive);
    render(<AuthGate><div>the app</div></AuthGate>);
    await screen.findByText("the app");

    // the session dies server-side, and some surface's request notices
    alive = false;
    noteAuthFailure(401, "/api/meetings");

    await screen.findByTestId("session-ended");
    expect(screen.getByText(SESSION_ENDED_HEADLINE)).toBeTruthy();
    expect(screen.queryByText("the app")).toBeNull();
    expect(screen.getByRole("button", { name: "Sign in again" })).toBeTruthy();
  });

  it("a suspicion the probe does NOT confirm leaves the app alone", async () => {
    // A 403 on one resource is usually "you are not in that workspace" and says nothing about the
    // session. Signing somebody out on an unverified guess would be its own defect.
    stubGate(() => true);
    render(<AuthGate><div>the app</div></AuthGate>);
    await screen.findByText("the app");

    noteAuthFailure(403, "/api/workspace/other-team");
    await new Promise((r) => setTimeout(r, 20));

    expect(screen.getByText("the app")).toBeTruthy();
    expect(screen.queryByTestId("session-ended")).toBeNull();
  });

  it("the one button leads to the sign-in card, which carries the URL the user was on", async () => {
    window.history.replaceState({}, "", "/?ask=catch-up&view=readme");
    let alive = true;
    let linkBody: { next?: string } | undefined;
    stubGate(() => alive, (b) => { linkBody = b as { next?: string }; });

    render(<AuthGate><div>the app</div></AuthGate>);
    await screen.findByText("the app");
    alive = false;
    noteAuthFailure(401, "/api/meetings");
    const card = await screen.findByTestId("session-ended");
    expect(card).toBeTruthy();

    // one button → the real sign-in card
    screen.getByRole("button", { name: "Sign in again" }).click();
    const input = (await screen.findByPlaceholderText("you@company.com")) as HTMLInputElement;

    // ask for a link; the `next` must be where the session died, not "/"
    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")!.set!;
    nativeSetter.call(input, "founder@vexa.ai");
    input.dispatchEvent(new Event("input", { bubbles: true }));
    (input.form as HTMLFormElement).requestSubmit();

    await waitFor(() => expect(linkBody).toBeDefined());
    expect(linkBody!.next).toBe("/?ask=catch-up&view=readme");
  });

  it("many simultaneous 401s produce ONE probe, not a storm", async () => {
    const spy = vi.fn();
    let alive = true;
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const u = String(url);
      if (u.startsWith("/api/auth/me")) {
        spy();
        return alive
          ? new Response(JSON.stringify({ authenticated: true }), { status: 200 })
          : new Response("{}", { status: 401 });
      }
      return new Response("{}", { status: 200 });
    }));

    render(<AuthGate><div>the app</div></AuthGate>);
    await screen.findByText("the app");
    const afterMount = spy.mock.calls.length;

    alive = false;
    for (let i = 0; i < 12; i++) noteAuthFailure(401, `/api/thing-${i}`);
    await screen.findByTestId("session-ended");

    expect(spy.mock.calls.length - afterMount).toBe(1);
  });

  it("the event name is stable — the raiser and the gate agree on one string", () => {
    expect(SESSION_EXPIRED_EVENT).toBe("vexa:session-suspect");
  });
});
