/** F42 — A SIGN-IN WITH NOWHERE TO GO MINTS ITS OWN ARRIVAL. Founder ruling 2026-09-02.
 *
 *  Signed in as a new user (uid 127, 11:35:14Z) he got: a seeded "Personal" chat on the generic 👋
 *  greeting, an ADMIN-ONLY "Organisation setup" card offered to a plain member, and his empty
 *  desk's README template rendered as a page — *"(unset) — this workspace has not been set up
 *  yet… Purpose (unset)… Objective (unset)"*. His words: *"i logged as new user, that's what i see
 *  - not happy about that."*
 *
 *  Every one of those was the product composing a landing out of whatever was lying around. The
 *  arrival is a RECORD instead: minted server-side from the address, carrying the workspaces already
 *  shared with it and the meetings it is invited to. The client asks for it and sends nothing about
 *  its contents — no workspaces, no tabs, no prompt text.
 *
 *  The trade that had to be got the right way round is the failure direction, and it is the OPPOSITE
 *  of the admin claim's: a failed mint must never cost somebody their sign-in.
 *
 *  The claim route's half of F42 — an instance that is ALREADY set up gets a first visit rather than
 *  the setup conversation — is in claimAdmin.test.ts, which owns that route's harness.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GET as redeem } from "../redeem/route";
import { _resetJtiLedger, mintMagicToken } from "../magicToken";

function makeReq(query: Record<string, string>): import("next/server").NextRequest {
  const url = new URL("https://terminal.test/api/auth/redeem");
  for (const [k, v] of Object.entries(query)) url.searchParams.set(k, v);
  return { nextUrl: url, url: url.toString() } as unknown as import("next/server").NextRequest;
}

/** Every POST agent-api saw, so the test can assert on the BODY — which is where the rule about
 *  not sending workspaces/tabs/prompt-text actually lives. */
let minted: { url: string; body: Record<string, unknown> }[] = [];

/** Every has-history probe, so a test can assert the arrival ASKED before it minted. */
let probed: string[] = [];

function stubs(opts: { mint?: "ok" | "fail"; history?: "none" | "some" | "down" } = {}) {
  const { mint = "ok", history = "none" } = opts;
  minted = [];
  probed = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    if (u.includes("/internal/has-history")) {
      probed.push(u);
      if (history === "down") return new Response("agent-api is down", { status: 503 });
      return new Response(JSON.stringify(
        history === "some"
          ? { has_history: true, sessions: 3, desk: "warm" }
          : { has_history: false, sessions: 0, desk: "new" }), { status: 200 });
    }
    if (u.includes("/internal/scaffolds")) {
      minted.push({ url: u, body: JSON.parse(String(init?.body ?? "{}")) });
      return mint === "ok"
        ? new Response(JSON.stringify({ id: "SC1", url: "https://terminal.test/?s=SC1" }), { status: 200 })
        : new Response("agent-api is down", { status: 503 });
    }
    if (u.includes("/admin/users/email/")) {
      return new Response(JSON.stringify({ id: 127, email: "new@example.com", name: "New" }), { status: 200 });
    }
    if (u.includes("/tokens")) {
      return init?.method === "POST"
        ? new Response(JSON.stringify({ token: "minted-tok" }), { status: 200 })
        : new Response(JSON.stringify([]), { status: 200 });
    }
    if (u.includes("/admin")) return new Response(JSON.stringify({ ok: true }), { status: 200 });
    return new Response("nope", { status: 500 });
  }));
}

beforeEach(() => {
  _resetJtiLedger();
  vi.stubEnv("NEXTAUTH_SECRET", "test-signing-secret");
  vi.stubEnv("VEXA_ADMIN_API_URL", "http://admin.test");
  vi.stubEnv("VEXA_ADMIN_API_KEY", "admin-secret");
  vi.stubEnv("VEXA_ADMIN_EMAILS", "admin@example.com");
  vi.stubEnv("TERMINAL_URL", "https://terminal.test");
  vi.stubEnv("AGENT_API_URL", "http://agent.test");
  vi.stubEnv("VEXA_INTERNAL_API_SECRET", "internal-secret");
  vi.spyOn(console, "error").mockImplementation(() => {});
  vi.spyOn(console, "info").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

const link = () => {
  const m = mintMagicToken("new@example.com");
  if (!m.ok) throw new Error("mint failed");
  return m.token;
};

describe("the magic-link door", () => {
  it("a sign-in that names no destination lands on a freshly minted first-visit", async () => {
    stubs();
    const res = await redeem(makeReq({ t: link() }));
    expect(res.status).toBe(302);
    expect(res.headers.get("location")).toBe("https://terminal.test/?s=SC1");
    // …and the person is signed in regardless: the arrival is where they land, not whether they got in
    expect(res.cookies.get("vexa-token")?.value).toBe("minted-tok");
  });

  it("asks for the RECORD and composes none of it — no workspaces, no tabs, no prompt text", async () => {
    stubs();
    await redeem(makeReq({ t: link() }));
    expect(minted).toHaveLength(1);
    const body = minted[0].body;
    expect(body.kind).toBe("first-visit");
    expect(body.opening).toBe("first-visit");
    expect(body.who).toBe("new@example.com");
    // The server derives the mount set from the address — which workspaces are shared with it, which
    // meetings it is invited to. A client that sent its own would be a second opinion about context.
    for (const forbidden of ["workspaces", "tabs", "focus", "prompt", "opening_text", "text"]) {
      expect(body).not.toHaveProperty(forbidden);
    }
  });

  it("A FAILED MINT COSTS NOTHING BUT THE ARRIVAL — they are still signed in, and land on /", async () => {
    stubs({ mint: "fail" });
    const res = await redeem(makeReq({ t: link() }));
    expect(res.status).toBe(302);
    expect(res.headers.get("location")).toBe("/");
    expect(res.cookies.get("vexa-token")?.value).toBe("minted-tok");
  });

  it("a link that NAMES a destination keeps it — nothing is minted over it", async () => {
    stubs();
    const res = await redeem(makeReq({ t: link(), next: "/?ask=catch-up" }));
    expect(res.headers.get("location")).toBe("/?ask=catch-up");
    expect(minted).toHaveLength(0);
  });

  it("…and a destination that already carries `?s=` is never overwritten", async () => {
    // Minting a second arrival over the one somebody was SENT would open a conversation on top of
    // the one they clicked.
    stubs();
    const res = await redeem(makeReq({ t: link(), next: "/?s=SENT-TO-THEM" }));
    expect(res.headers.get("location")).toBe("/?s=SENT-TO-THEM");
    expect(minted).toHaveLength(0);
  });
});

/** #1591 — A FIRST VISIT IS FOR SOMEBODY WITH NO FIRST VISIT BEHIND THEM.
 *
 *  F42 above minted on every sign-in that named no destination, which is a fact about the LINK.
 *  Whether there is anywhere to go is a fact about the PERSON, and the two are not the same
 *  question: the admin who had spent a morning on this instance signed in again in a new window and
 *  was introduced to the product — *"i logged in again and now see no chats and it's starting over
 *  again while it has the context"*.
 *
 *  The server holds both halves of the answer (their chat threads, their desk) and the client holds
 *  neither, which is exactly why this had to become a probe rather than a smarter guess. */
describe("the arrival asks first", () => {
  it("a returning person is NOT introduced again — they land on `/`, where the rail is theirs", async () => {
    stubs({ history: "some" });
    const res = await redeem(makeReq({ t: link() }));
    expect(res.headers.get("location")).toBe("/");
    expect(minted).toHaveLength(0);
    // …and they are signed in, which is the whole of what they came for
    expect(res.cookies.get("vexa-token")?.value).toBe("minted-tok");
  });

  it("a genuinely new person still gets the arrival — and the question was asked before it", async () => {
    stubs({ history: "none" });
    const res = await redeem(makeReq({ t: link() }));
    expect(probed).toHaveLength(1);
    expect(probed[0]).toContain("who=new%40example.com");
    expect(res.headers.get("location")).toBe("https://terminal.test/?s=SC1");
  });

  it("a probe that cannot answer mints NOTHING", async () => {
    // It fails towards no arrival, and that costs almost nothing: the probe talks to the same
    // agent-api the mint needs one line later, so an outage lands them on `/` either way. What it
    // buys is that a blip can never re-commit the reported defect — a returning person told, again,
    // that we have never met.
    stubs({ history: "down" });
    const res = await redeem(makeReq({ t: link() }));
    expect(res.headers.get("location")).toBe("/");
    expect(minted).toHaveLength(0);
    expect(res.cookies.get("vexa-token")?.value).toBe("minted-tok");
  });

  it("a link that names a destination is not even probed", async () => {
    stubs({ history: "none" });
    await redeem(makeReq({ t: link(), next: "/?ask=catch-up" }));
    expect(probed).toHaveLength(0);
  });
});
