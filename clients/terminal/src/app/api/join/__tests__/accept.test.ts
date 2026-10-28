/** `POST /api/join/accept` — the one click from the invite mail (PRD 0.13.4 S2).
 *
 *  The scanner property first: a GET or a HEAD of anything on the join path changes nothing — no
 *  claim, no account, no session. Then the click: same-origin only, counted, the sign-in half
 *  claimed once, the address admitted `via_invite`, the invite accepted AS that person, and the
 *  landing is the room the invite named. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { NextRequest } from "next/server";

const claimInviteSignin = vi.fn();
const findOrCreateUserToken = vi.fn();
const acceptInviteAs = vi.fn();
vi.mock("../../auth/adminApi", () => ({
  AUTH_COOKIE: "vexa-token",
  USER_INFO_COOKIE: "vexa-user-info",
  claimInviteSignin: (...a: unknown[]) => claimInviteSignin(...a),
  findOrCreateUserToken: (...a: unknown[]) => findOrCreateUserToken(...a),
  acceptInviteAs: (...a: unknown[]) => acceptInviteAs(...a),
  rateTake: async () => "unavailable",
}));

import { GET, HEAD, POST } from "../accept/route";
import { GET as previewGET } from "../preview/route";
import { _resetLinkRateLimits } from "../../auth/linkRateLimit";

const TOKEN = "tok_mailed_invite";
const ORIGIN = "https://app.example.test";

function req(method: string, opts: { headers?: Record<string, string>; form?: Record<string, string>; url?: string } = {}) {
  const url = new URL(opts.url || `${ORIGIN}/api/join/accept`);
  const fd = new FormData();
  for (const [k, v] of Object.entries(opts.form || {})) fd.set(k, v);
  return {
    method,
    nextUrl: url,
    headers: new Headers({ host: "app.example.test", "x-forwarded-proto": "https", ...(opts.headers || {}) }),
    formData: async () => fd,
  } as unknown as NextRequest;
}

const sameSite = { origin: ORIGIN, "sec-fetch-site": "same-origin", "x-vexa-client-address": "203.0.113.9" };

beforeEach(() => {
  _resetLinkRateLimits();
  vi.stubEnv("NEXTAUTH_URL", ORIGIN);
  vi.stubEnv("NEXTAUTH_SECRET", "test-signing-secret-0123456789abcdef");
  claimInviteSignin.mockReset();
  findOrCreateUserToken.mockReset();
  acceptInviteAs.mockReset();
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("a mail scanner opening the link changes nothing", () => {
  it("GET of the action URL redirects to the page that asks, and claims nothing", async () => {
    const res = await GET(req("GET", { url: `${ORIGIN}/api/join/accept?i=${TOKEN}` }));
    expect(res.status).toBe(303);
    expect(res.headers.get("location")).toBe(`/join?i=${TOKEN}`);
    expect(claimInviteSignin).not.toHaveBeenCalled();
    expect(findOrCreateUserToken).not.toHaveBeenCalled();
    expect(res.headers.get("set-cookie")).toBeNull();
  });

  it("HEAD answers 200 and does nothing", async () => {
    const res = await HEAD();
    expect(res.status).toBe(200);
    expect(claimInviteSignin).not.toHaveBeenCalled();
  });

  it("GET of the preview, any number of times, is a read of agent-api's preview and nothing else", async () => {
    vi.stubEnv("AGENT_API_URL", "http://agent.test");
    const seen: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      seen.push(`${init?.method || "GET"} ${url}`);
      return new Response(JSON.stringify({ valid: true, signin: true }), { status: 200 });
    }));
    for (let i = 0; i < 3; i++) {
      const r = await previewGET(req("GET", { url: `${ORIGIN}/api/join/preview?i=${TOKEN}` }));
      expect(r.status).toBe(200);
    }
    expect(seen.filter((s) => !s.includes("/internal/rate/take"))).toEqual(
      Array(3).fill(`GET http://agent.test/api/workspace/invites/preview?token=${TOKEN}`));
    expect(claimInviteSignin).not.toHaveBeenCalled();
  });
});

describe("the click", () => {
  it("refuses a cross-site POST before anything is claimed", async () => {
    const res = await POST(req("POST", { form: { i: TOKEN }, headers: { origin: "https://evil.test", "sec-fetch-site": "cross-site" } }));
    expect(res.status).toBe(303);
    expect(res.headers.get("location")).toBe(`/join?i=${TOKEN}&n=crosssite`);
    expect(res.headers.get("set-cookie")).toBeNull();
    expect(claimInviteSignin).not.toHaveBeenCalled();
  });

  it("refuses a POST that carries neither Origin nor Sec-Fetch-Site", async () => {
    const res = await POST(req("POST", { form: { i: TOKEN } }));
    expect(res.headers.get("location")).toBe(`/join?i=${TOKEN}&n=crosssite`);
    expect(claimInviteSignin).not.toHaveBeenCalled();
  });

  it("signs the invited address in, joins as them, and lands in the room", async () => {
    claimInviteSignin.mockResolvedValue({ ok: true, email: "valeriia@fresh.test", workspaceId: "acme", room: { id: "s-room", title: "#test" } });
    findOrCreateUserToken.mockResolvedValue({ ok: true, user: { id: 42, email: "valeriia@fresh.test", name: "" }, token: "vxa_login" });
    acceptInviteAs.mockResolvedValue({ ok: true, workspaceId: "acme", room: { id: "s-room" } });
    const res = await POST(req("POST", { form: { i: TOKEN }, headers: sameSite }));
    expect(res.status).toBe(303);
    expect(res.headers.get("location")).toBe("/r/chat/s-room");
    expect(claimInviteSignin).toHaveBeenCalledWith(TOKEN);
    expect(findOrCreateUserToken).toHaveBeenCalledWith("valeriia@fresh.test", { emailVerifiedVia: "invite_link", viaInvite: true });
    expect(acceptInviteAs).toHaveBeenCalledWith("vxa_login", TOKEN);
    const cookies = res.headers.get("set-cookie") || "";
    expect(cookies).toContain("vexa-token=vxa_login");
    expect(cookies.toLowerCase()).toContain("httponly");
  });

  it("lands on the workspace when the invite names no room", async () => {
    claimInviteSignin.mockResolvedValue({ ok: true, email: "a@fresh.test", workspaceId: "acme", room: null });
    findOrCreateUserToken.mockResolvedValue({ ok: true, user: { id: 1, email: "a@fresh.test" }, token: "t" });
    acceptInviteAs.mockResolvedValue({ ok: true, workspaceId: "acme", room: null });
    const res = await POST(req("POST", { form: { i: TOKEN }, headers: sameSite }));
    expect(res.headers.get("location")).toBe("/w/acme");
  });

  it("a link already spent goes back to the invite page and creates nothing", async () => {
    claimInviteSignin.mockResolvedValue({ ok: false, reason: "used" });
    const res = await POST(req("POST", { form: { i: TOKEN }, headers: sameSite }));
    expect(res.status).toBe(303);
    expect(res.headers.get("location")).toBe(`/join?i=${TOKEN}&n=signin`);
    expect(findOrCreateUserToken).not.toHaveBeenCalled();
    expect(res.headers.get("set-cookie")).toBeNull();
  });

  it("an address the instance refuses goes back to the page with that sentence, and no session", async () => {
    claimInviteSignin.mockResolvedValue({ ok: true, email: "a@fresh.test", workspaceId: "acme", room: null });
    findOrCreateUserToken.mockResolvedValue({ ok: false, status: 403, error: "no", refused: "not-allowed" });
    const res = await POST(req("POST", { form: { i: TOKEN }, headers: sameSite }));
    expect(res.headers.get("location")).toBe(`/join?i=${TOKEN}&n=notallowed`);
    expect(acceptInviteAs).not.toHaveBeenCalled();
    expect(res.headers.get("set-cookie")).toBeNull();
  });

  it("signed in but not joined lands on the invite page, which then asks to confirm", async () => {
    claimInviteSignin.mockResolvedValue({ ok: true, email: "a@fresh.test", workspaceId: "acme", room: null });
    findOrCreateUserToken.mockResolvedValue({ ok: true, user: { id: 1, email: "a@fresh.test" }, token: "t" });
    acceptInviteAs.mockResolvedValue({ ok: false, status: 502 });
    const res = await POST(req("POST", { form: { i: TOKEN }, headers: sameSite }));
    expect(res.headers.get("location")).toBe(`/join?i=${TOKEN}`);
    expect(res.headers.get("set-cookie") || "").toContain("vexa-token=t");
  });

  it("is counted per client address (M6)", async () => {
    vi.stubEnv("JOIN_RATE_PER_IP", "2");
    claimInviteSignin.mockResolvedValue({ ok: false, reason: "used" });
    for (let i = 0; i < 2; i++) expect((await POST(req("POST", { form: { i: TOKEN }, headers: sameSite }))).status).toBe(303);
    expect((await POST(req("POST", { form: { i: TOKEN }, headers: sameSite }))).headers.get("location")).toBe(`/join?i=${TOKEN}&n=rate`);
    expect(claimInviteSignin).toHaveBeenCalledTimes(2);
  });

  it("is closed when the operator closed the emailed door", async () => {
    vi.stubEnv("VEXA_SIGNIN_METHODS", "google");
    const res = await POST(req("POST", { form: { i: TOKEN }, headers: sameSite }));
    expect(res.headers.get("location")).toBe(`/join?i=${TOKEN}&n=signin`);
    expect(claimInviteSignin).not.toHaveBeenCalled();
  });
});
