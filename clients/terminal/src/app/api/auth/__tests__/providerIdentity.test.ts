/** An OAuth sign-in uses the provider's email only when the provider verified that mailbox.
 *
 *  Google: `email_verified === true`. Microsoft: a pinned tenant id must match the token's `tid`; on
 *  a multi-tenant authority the token must also carry `xms_edov: true`. Anything else is refused
 *  before admin-api is asked about the address at all.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const findOrCreateUserToken = vi.fn();
vi.mock("../adminApi", () => ({
  AUTH_COOKIE: "vexa-token",
  USER_INFO_COOKIE: "vexa-user-info",
  findOrCreateUserToken: (...a: unknown[]) => findOrCreateUserToken(...a),
  mintFirstVisitScaffold: async () => ({ ok: false, status: 409, error: "returning" }),
}));
vi.mock("next/headers", () => ({
  cookies: async () => ({ get: () => undefined, set: () => {}, delete: () => {} }),
}));

import { authOptions } from "../[...nextauth]/authOptions";
import { jwtClaims, verifiedProviderIdentity } from "../providerIdentity";
import { SIGNIN_ERROR_UNVERIFIED, signinErrorMessage } from "../../../signinRefusal";

const TENANT = "11111111-2222-3333-4444-555555555555";
const OTHER_TENANT = "99999999-8888-7777-6666-555555555555";

function idToken(claims: Record<string, unknown>): string {
  const b64 = (o: unknown) => Buffer.from(JSON.stringify(o)).toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${b64({ alg: "RS256", typ: "JWT" })}.${b64(claims)}.signature`;
}

const msClaims = (extra: Record<string, unknown> = {}) =>
  ({ tid: TENANT, oid: "aaaaaaaa-0000-0000-0000-000000000001", email: "Ana@Example.com", ...extra });

const signIn = (provider: string, input: { profile?: unknown; account?: Record<string, unknown> }, email = "ana@example.com") =>
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (authOptions.callbacks!.signIn as any)({ user: { email, name: "Ana" }, account: { provider, ...input.account }, profile: input.profile });

beforeEach(() => {
  findOrCreateUserToken.mockReset();
  findOrCreateUserToken.mockResolvedValue({ ok: true, token: "tok", user: { id: 7, email: "ana@example.com" } });
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("Google", () => {
  it("accepts a verified email and names the subject", () => {
    expect(verifiedProviderIdentity("google", { profile: { email: "Ana@Example.com", email_verified: true, sub: "g-1" } }))
      .toEqual({ ok: true, email: "ana@example.com", subject: "google:g-1" });
  });

  it.each([false, "true", undefined, 1])("refuses email_verified=%s", (verified) => {
    expect(verifiedProviderIdentity("google", { profile: { email: "ana@example.com", email_verified: verified, sub: "g-1" } }).ok)
      .toBe(false);
  });

  it("the sign-in callback refuses an unverified Google email before asking admin-api", async () => {
    const r = await signIn("google", { profile: { email: "ana@example.com", email_verified: false, sub: "g-1" } });
    expect(r).toBe(`/?error=${SIGNIN_ERROR_UNVERIFIED}`);
    expect(findOrCreateUserToken).not.toHaveBeenCalled();
    expect(signinErrorMessage(SIGNIN_ERROR_UNVERIFIED)).toMatch(/verified/);
  });

  it("the sign-in callback admits with the verified address", async () => {
    expect(await signIn("google", { profile: { email: "Ana@Example.com", email_verified: true, sub: "g-1" } })).toBe(true);
    expect(findOrCreateUserToken).toHaveBeenCalledWith("ana@example.com");
  });
});

describe("Microsoft", () => {
  it("on a multi-tenant authority requires xms_edov", () => {
    for (const tenant of ["", "common", "organizations", "consumers"]) {
      vi.stubEnv("MICROSOFT_TENANT_ID", tenant);
      expect(verifiedProviderIdentity("microsoft", { account: { id_token: idToken(msClaims()) } }).ok).toBe(false);
      expect(verifiedProviderIdentity("microsoft", { account: { id_token: idToken(msClaims({ xms_edov: false })) } }).ok).toBe(false);
      expect(verifiedProviderIdentity("microsoft", { account: { id_token: idToken(msClaims({ xms_edov: true })) } }))
        .toEqual({ ok: true, email: "ana@example.com", subject: `microsoft:${TENANT}:aaaaaaaa-0000-0000-0000-000000000001` });
    }
  });

  it("with a pinned tenant id accepts only that tenant", () => {
    vi.stubEnv("MICROSOFT_TENANT_ID", TENANT.toUpperCase());
    expect(verifiedProviderIdentity("microsoft", { account: { id_token: idToken(msClaims()) } }).ok).toBe(true);
    expect(verifiedProviderIdentity("microsoft", { account: { id_token: idToken(msClaims({ tid: OTHER_TENANT, xms_edov: true })) } }).ok)
      .toBe(false);
  });

  it("refuses a token with no tenant, object id or email, and a missing token", () => {
    vi.stubEnv("MICROSOFT_TENANT_ID", TENANT);
    for (const claims of [msClaims({ tid: undefined }), msClaims({ oid: undefined }), msClaims({ email: undefined }),
                          msClaims({ email: "not-an-address" })]) {
      expect(verifiedProviderIdentity("microsoft", { account: { id_token: idToken(claims) } }).ok).toBe(false);
    }
    expect(verifiedProviderIdentity("microsoft", { account: {} }).ok).toBe(false);
    expect(verifiedProviderIdentity("microsoft", { account: { id_token: "garbage" } }).ok).toBe(false);
  });

  it("the sign-in callback uses the token's email, not the profile's, and refuses an unverified one", async () => {
    vi.stubEnv("MICROSOFT_TENANT_ID", "common");
    expect(await signIn("microsoft", { account: { id_token: idToken(msClaims()) } })).toBe(`/?error=${SIGNIN_ERROR_UNVERIFIED}`);
    expect(findOrCreateUserToken).not.toHaveBeenCalled();
    expect(await signIn("microsoft", { account: { id_token: idToken(msClaims({ xms_edov: true })) } }, "someone-else@example.com"))
      .toBe(true);
    expect(findOrCreateUserToken).toHaveBeenCalledWith("ana@example.com");
  });
});

describe("the claims reader", () => {
  it("reads a JWT payload and nothing else", () => {
    expect(jwtClaims(idToken({ a: 1 }))).toEqual({ a: 1 });
    for (const bad of [undefined, "", "a", "a.", "a.!!!.c", 7]) expect(jwtClaims(bad)).toBeNull();
  });

  it("an unknown provider is refused", () => {
    expect(verifiedProviderIdentity("github", {}).ok).toBe(false);
  });
});
