/** The operator's printed sign-in link (scripts/signin-link.mjs, Lite's `vexa-signin-link`) — the
 *  first-run door of an install with no mail and no OAuth, now that Lite ships with zero-login off.
 *
 *  It must be the emailed door's own token: the redeem route signs the person in with it exactly once,
 *  it lives an hour at most, and it is refused when the email door is switched off or there is no key.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { POST as redeem } from "../redeem/route";
import { MAX_TTL_SECONDS, verifyMagicToken } from "../magicToken";
import { mintToken } from "../magicTokenMint.mjs";
import { linkLedger } from "./linkLedgerDouble";
import { DEFAULT_EMAIL, signinLink } from "../../../../../scripts/signin-link.mjs";

const SECRET = "test-signing-secret-0123456789abcdef";
const ENV = { NEXTAUTH_SECRET: SECRET, TERMINAL_PUBLIC_URL: "https://terminal.test/" };

function post(token: string): import("next/server").NextRequest {
  const url = new URL("https://terminal.test/api/auth/redeem");
  const form = new FormData();
  form.set("t", token);
  return {
    nextUrl: url, url: url.toString(), method: "POST",
    headers: new Headers({ origin: "https://terminal.test", "sec-fetch-site": "same-origin" }),
    formData: async () => form,
  } as unknown as import("next/server").NextRequest;
}

function adminApi() {
  return vi.fn(async (url: string, init?: RequestInit) => {
    if (linkLedger.isRedeem(url)) return linkLedger.respond(init);
    if (url.includes("/internal/signin-admission")) {
      return new Response(JSON.stringify({ admitted: true, why: "existing-user" }), { status: 200 });
    }
    if (url.includes("/admin/users/email/")) {
      return new Response(JSON.stringify({ id: 1, email: DEFAULT_EMAIL, name: "self host" }), { status: 200 });
    }
    if (url.includes("/tokens")) {
      return init?.method === "POST"
        ? new Response(JSON.stringify({ token: "minted-tok" }), { status: 200 })
        : new Response(JSON.stringify([]), { status: 200 });
    }
    return new Response("nope", { status: 500 });
  });
}

beforeEach(() => {
  linkLedger.reset();
  vi.stubEnv("NEXTAUTH_SECRET", SECRET);
  vi.stubEnv("MAGIC_LINK_SECRET", "");
  vi.stubEnv("VEXA_ADMIN_API_URL", "http://admin.test");
  vi.stubEnv("VEXA_ADMIN_API_KEY", "admin-secret");
  vi.stubEnv("VEXA_INTERNAL_API_SECRET", "internal-secret");
  vi.stubEnv("TERMINAL_URL", "https://terminal.test");
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("the printed first-run link", () => {
  it("is a redeem link on the public origin for the self-host person, valid for an hour", () => {
    const now = Date.UTC(2026, 9, 22, 12, 0, 0);
    const out = signinLink(DEFAULT_EMAIL, ENV, { now });
    expect(out.error).toBeUndefined();
    const url = new URL(out.url!);
    expect(`${url.origin}${url.pathname}`).toBe("https://terminal.test/api/auth/redeem");
    expect(out.expiresAt).toBe(now / 1000 + MAX_TTL_SECONDS);
    const v = verifyMagicToken(url.searchParams.get("t")!, { now });
    expect(v.ok && v.email).toBe(DEFAULT_EMAIL);
  });

  it("signs the person in through the redeem route, once", async () => {
    vi.stubGlobal("fetch", adminApi());
    const token = new URL(signinLink(DEFAULT_EMAIL, ENV).url!).searchParams.get("t")!;
    const first = await redeem(post(token));
    expect(first.status).toBe(303);
    expect(first.cookies.get("vexa-token")?.value).toBe("minted-tok");
    const replay = await redeem(post(token));
    expect(replay.status).not.toBe(303);
    expect(replay.cookies.get("vexa-token")).toBeUndefined();
  });

  it("is refused without a usable key, without a public origin, or with the email door off", () => {
    expect(signinLink(DEFAULT_EMAIL, { TERMINAL_PUBLIC_URL: "https://terminal.test" }).error).toMatch(/NEXTAUTH_SECRET/);
    expect(signinLink(DEFAULT_EMAIL, { NEXTAUTH_SECRET: "change-me", TERMINAL_PUBLIC_URL: "https://terminal.test" }).error).toMatch(/NEXTAUTH_SECRET/);
    expect(signinLink(DEFAULT_EMAIL, { NEXTAUTH_SECRET: SECRET }).error).toMatch(/TERMINAL_PUBLIC_URL/);
    expect(signinLink(DEFAULT_EMAIL, { ...ENV, VEXA_SIGNIN_METHODS: "google" }).error).toMatch(/VEXA_SIGNIN_METHODS/);
  });

  it("is signed with a key another instance does not hold (a forged link is refused)", () => {
    const token = new URL(signinLink(DEFAULT_EMAIL, { ...ENV, NEXTAUTH_SECRET: "another-instance-secret-0123456789abcdef" }).url!)
      .searchParams.get("t")!;
    expect(verifyMagicToken(token)).toEqual({ ok: false, reason: "bad-signature" });
  });
  it("is the operator's unbound link; nothing else may be unbound for an ordinary address", () => {
    const now = Date.UTC(2026, 9, 22, 12, 0, 0);
    const token = new URL(signinLink(DEFAULT_EMAIL, ENV, { now }).url!).searchParams.get("t")!;
    const claims = JSON.parse(Buffer.from(token.split(".")[0], "base64url").toString("utf8"));
    expect(claims).toMatchObject({ e: DEFAULT_EMAIL, b: "", o: 1 });
    expect(verifyMagicToken(token, { now })).toMatchObject({ ok: true, binding: "" });
    // Deny: an unbound link without the operator's mark (what a request route would mint) is refused.
    const unbound = mintToken(DEFAULT_EMAIL, ENV, { now, binding: "" });
    expect(unbound.ok && verifyMagicToken(unbound.token, { now })).toEqual({ ok: false, reason: "malformed" });
    // Deny: the mint refuses a link that is neither bound nor the operator's, and one that claims both.
    expect(mintToken(DEFAULT_EMAIL, ENV, { now }).ok).toBe(false);
    expect(mintToken(DEFAULT_EMAIL, ENV, { now, binding: "x", operator: true }).ok).toBe(false);
  });
});
