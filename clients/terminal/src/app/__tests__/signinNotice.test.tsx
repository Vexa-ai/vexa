/** The sign-in card after a REFUSED OAuth round-trip (Vexa-ai/vexa#1783).
 *
 *  The OAuth door refuses an address that may not sign in by sending the browser back to
 *  `/?error=SigninNotAllowed` (or `SigninUnavailable` when admin-api could not answer). The card must
 *  say the one shared sentence — naming no list and no domain — and take the code out of the address
 *  bar, so a reload does not repeat it and the next emailed link does not carry it in `next=`.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import React from "react";

import { AuthGate } from "../AuthGate";
import { SIGNIN_NOT_ALLOWED, SIGNIN_UNAVAILABLE, signinErrorMessage } from "../signinRefusal";

function signedOut() {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const u = String(url);
    if (u.startsWith("/api/auth/me")) return new Response("{}", { status: 401 });
    if (u.startsWith("/api/auth/providers")) return new Response(JSON.stringify({ google: {} }), { status: 200 });
    if (u.startsWith("/api/auth/instance")) return new Response(JSON.stringify({ admin_exists: true }), { status: 200 });
    return new Response("{}", { status: 200 });
  }));
}

beforeEach(() => { signedOut(); });

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

describe("a refused OAuth sign-in lands on the card with one sentence", () => {
  it("SigninNotAllowed → the shared refusal, and the code leaves the address bar", async () => {
    window.history.replaceState(null, "", "/?ask=catch-up&error=SigninNotAllowed");
    render(<AuthGate><div>the app</div></AuthGate>);
    const notice = await screen.findByTestId("signin-notice");
    expect(notice.textContent).toBe(SIGNIN_NOT_ALLOWED);
    expect(window.location.search).toBe("?ask=catch-up");
    expect(screen.queryByText("the app")).toBeNull();
  });

  it("SigninUnavailable → the fail-closed sentence", async () => {
    window.history.replaceState(null, "", "/?error=SigninUnavailable");
    render(<AuthGate><div>the app</div></AuthGate>);
    expect((await screen.findByTestId("signin-notice")).textContent).toBe(SIGNIN_UNAVAILABLE);
    expect(window.location.search).toBe("");
  });

  it("no code, no notice", async () => {
    render(<AuthGate><div>the app</div></AuthGate>);
    await screen.findByText("Continue with Google");
    expect(screen.queryByTestId("signin-notice")).toBeNull();
  });

  it("the sentence names no list, no domain and no address", () => {
    for (const s of [SIGNIN_NOT_ALLOWED, SIGNIN_UNAVAILABLE, signinErrorMessage("AccessDenied")!]) {
      expect(s).not.toMatch(/@|allow-list|allowlist|domain/i);
    }
  });
});
