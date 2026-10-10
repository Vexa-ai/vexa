// @vitest-environment node
/** The generic OIDC door (ADFS, Keycloak) and the operator's choice of sign-in methods.
 *
 *  - The provider is registered only when `VEXA_OIDC_ISSUER` is set, from the issuer's discovery
 *    document, with PKCE + state + nonce, the profile read from the verified ID token (never from
 *    userinfo, which on ADFS answers only `sub`).
 *  - A half-configured provider refuses to start the server, naming the key.
 *  - The address comes from the configured claim of a token issued by the configured issuer; the
 *    account is bound to `oidc:<sha256(iss, sub)>`, and admission (the allow-list) still decides.
 *  - `VEXA_SIGNIN_METHODS` removes doors server-side, not only from the page.
 */
import { spawnSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const findOrCreateUserToken = vi.fn();
vi.mock("../adminApi", () => ({
  AUTH_COOKIE: "vexa-token",
  USER_INFO_COOKIE: "vexa-user-info",
  findOrCreateUserToken: (...a: unknown[]) => findOrCreateUserToken(...a),
  mintFirstVisitScaffold: async () => ({ ok: false, status: 409, error: "returning" }),
  instanceState: async () => ({ admin_exists: true }),
}));
vi.mock("next/headers", () => ({
  cookies: async () => ({ get: () => undefined, set: () => {}, delete: () => {} }),
}));

import { oidcConfig, pemCertificates, signinConfigStartupError, signinMethods } from "../oidcConfig.mjs";
import { oidcDisplayName, oidcSubject, verifiedProviderIdentity } from "../providerIdentity";
import { SIGNIN_ERROR_NOT_ALLOWED, SIGNIN_ERROR_UNVERIFIED } from "../../../signinRefusal";
import { providersFrom } from "../../../AuthGate";

const TERMINAL_DIR = fileURLToPath(new URL("../../../../../", import.meta.url));
const ISSUER = "https://idp.example.test/realms/corp";
const STRONG = "9f1c2e7a4b6d8f0a1c3e5a7b9d1f3a5c7e9b1d3f5a7c9e1b3d5f7a9c1e3b5d7f";
const CERT = "-----BEGIN CERTIFICATE-----\nMIIBszCCAVmgAwIBAgIUQ==\n-----END CERTIFICATE-----\n";

const BASE_ENV = {
  VEXA_OIDC_ISSUER: ISSUER,
  VEXA_OIDC_CLIENT_ID: "vexa-terminal",
  VEXA_OIDC_CLIENT_SECRET: "client-secret-value",
};

function idToken(claims: Record<string, unknown>): string {
  const b64 = (o: unknown) => Buffer.from(JSON.stringify(o)).toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${b64({ alg: "RS256", typ: "JWT" })}.${b64(claims)}.signature`;
}

/** An ADFS-shaped token: email and name only in the ID token, a base64 `sub`. */
const adfsClaims = (extra: Record<string, unknown> = {}) => ({
  iss: ISSUER, aud: "vexa-terminal", sub: "ZiV3uFjC0b8xQB0/2pF5Nk8hCPr0sXwN+0lQ8SMx6m0=",
  email: "Ana.Lopez@Corp.Example", name: "Ana Lopez", ...extra,
});

function stubEnv(env: Record<string, string>) {
  for (const [k, v] of Object.entries(env)) vi.stubEnv(k, v);
}

/** authOptions reads the environment when it is imported, as it does in the server. */
async function loadAuthOptions() {
  vi.resetModules();
  return (await import("../[...nextauth]/authOptions")).authOptions;
}

beforeEach(() => {
  findOrCreateUserToken.mockReset();
  findOrCreateUserToken.mockResolvedValue({ ok: true, token: "tok", user: { id: 7, email: "ana.lopez@corp.example" } });
  for (const k of ["VEXA_OIDC_ISSUER", "VEXA_OIDC_CLIENT_ID", "VEXA_OIDC_CLIENT_SECRET", "VEXA_OIDC_SCOPES",
                   "VEXA_OIDC_DISPLAY_NAME", "VEXA_OIDC_CA_FILE", "VEXA_OIDC_EMAIL_CLAIM", "VEXA_OIDC_NAME_CLAIM",
                   "VEXA_OIDC_REQUIRE_EMAIL_VERIFIED", "VEXA_SIGNIN_METHODS", "GOOGLE_CLIENT_ID",
                   "GOOGLE_CLIENT_SECRET", "MICROSOFT_CLIENT_ID", "MICROSOFT_CLIENT_SECRET"]) vi.stubEnv(k, "");
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("the configuration", () => {
  it("is off without an issuer, and has sensible defaults with one", () => {
    expect(oidcConfig({})).toEqual({ enabled: false });
    const cfg = oidcConfig(BASE_ENV);
    expect(cfg).toMatchObject({
      enabled: true, issuer: ISSUER, clientId: "vexa-terminal", scopes: "openid email profile",
      displayName: "Single sign-on", emailClaim: "email", nameClaim: "name", requireEmailVerified: false, ca: null,
      wellKnown: `${ISSUER}/.well-known/openid-configuration`,
    });
    expect(oidcConfig({ ...BASE_ENV, VEXA_OIDC_ISSUER: "https://adfs.example.test/adfs/" }))
      .toMatchObject({ wellKnown: "https://adfs.example.test/adfs/.well-known/openid-configuration" });
  });

  it.each([
    ["an http issuer", { VEXA_OIDC_ISSUER: "http://idp.example.test" }, /https/],
    ["an issuer that is not a URL", { VEXA_OIDC_ISSUER: "idp" }, /not a URL/],
    ["an issuer with credentials", { VEXA_OIDC_ISSUER: "https://u:p@idp.example.test" }, /credentials/],
    ["no client id", { VEXA_OIDC_CLIENT_ID: "" }, /CLIENT_ID/],
    ["no client secret", { VEXA_OIDC_CLIENT_SECRET: "" }, /CLIENT_SECRET/],
    ["scopes without openid", { VEXA_OIDC_SCOPES: "email profile" }, /openid/],
    ["a malformed claim name", { VEXA_OIDC_EMAIL_CLAIM: "e mail" }, /claim/],
    ["an unreadable CA file", { VEXA_OIDC_CA_FILE: "/nonexistent/ca.pem" }, /cannot be read/],
  ])("refuses %s", (_label, override, why) => {
    const cfg = oidcConfig({ ...BASE_ENV, ...override });
    expect(cfg.enabled).toBe(false);
    expect((cfg as { problem?: string }).problem).toMatch(why);
    expect(signinConfigStartupError({ ...BASE_ENV, ...override })).toMatch(why);
  });

  it("reads a CA bundle, and refuses one that holds no certificate", () => {
    const dir = mkdtempSync(join(tmpdir(), "oidc-ca-"));
    writeFileSync(join(dir, "ok.pem"), CERT + CERT);
    writeFileSync(join(dir, "empty.pem"), "not a certificate\n");
    expect(oidcConfig({ ...BASE_ENV, VEXA_OIDC_CA_FILE: join(dir, "ok.pem") })).toMatchObject({ enabled: true, ca: [CERT.trim(), CERT.trim()] });
    expect(signinConfigStartupError({ ...BASE_ENV, VEXA_OIDC_CA_FILE: join(dir, "empty.pem") })).toMatch(/no PEM certificate/);
    expect(pemCertificates(undefined)).toEqual([]);
  });

  it("VEXA_SIGNIN_METHODS: unset offers everything; unknown or empty entries refuse to start; oidc needs an issuer", () => {
    expect([...signinMethods({})].sort()).toEqual(["email", "google", "microsoft", "oidc"]);
    expect([...signinMethods({ VEXA_SIGNIN_METHODS: " OIDC , email " })].sort()).toEqual(["email", "oidc"]);
    expect(signinConfigStartupError({ VEXA_SIGNIN_METHODS: "oidc,passwords" })).toMatch(/unknown method/);
    expect(signinConfigStartupError({ VEXA_SIGNIN_METHODS: " , " })).toMatch(/names no method/);
    expect(signinConfigStartupError({ VEXA_SIGNIN_METHODS: "oidc" })).toMatch(/VEXA_OIDC_ISSUER is not set/);
    expect(signinConfigStartupError({ ...BASE_ENV, VEXA_SIGNIN_METHODS: "oidc" })).toBeNull();
    expect(signinConfigStartupError({})).toBeNull();
  });

  it("the server refuses to start on a half-configured provider", () => {
    const run = spawnSync(process.execPath, ["server.mjs"], {
      cwd: TERMINAL_DIR,
      env: { PATH: process.env.PATH, NODE_ENV: "production", PORT: "0", NEXTAUTH_SECRET: STRONG,
             VEXA_OIDC_ISSUER: ISSUER, VEXA_OIDC_CLIENT_ID: "vexa-terminal" },
      encoding: "utf8",
      timeout: 60_000,
    });
    expect(run.status).toBe(1);
    expect(run.stderr).toMatch(/refusing to start: generic OIDC sign-in is misconfigured: VEXA_OIDC_CLIENT_SECRET/);
  }, 70_000);
});

describe("the provider", () => {
  it("is registered from discovery, with PKCE, state and nonce, the profile read from the ID token", async () => {
    stubEnv({ ...BASE_ENV, VEXA_OIDC_DISPLAY_NAME: "Corporate login", VEXA_OIDC_SCOPES: "openid email profile allatclaims" });
    const opts = await loadAuthOptions();
    const p = opts.providers.find((x) => x.id === "oidc") as unknown as Record<string, unknown>;
    expect(p).toBeTruthy();
    expect(p).toMatchObject({
      type: "oauth", name: "Corporate login", wellKnown: `${ISSUER}/.well-known/openid-configuration`,
      clientId: "vexa-terminal", clientSecret: "client-secret-value", idToken: true,
      authorization: { params: { scope: "openid email profile allatclaims" } },
    });
    expect([...(p.checks as string[])].sort()).toEqual(["nonce", "pkce", "state"]);
    expect(p.httpOptions).toBeUndefined();
    const profile = (p.profile as (c: Record<string, unknown>) => Record<string, unknown>)(adfsClaims());
    expect(profile).toEqual({ id: adfsClaims().sub, email: "Ana.Lopez@Corp.Example", name: "Ana Lopez" });
  });

  it("adds a custom CA to the public roots rather than replacing them", async () => {
    const dir = mkdtempSync(join(tmpdir(), "oidc-ca-"));
    writeFileSync(join(dir, "ca.pem"), CERT);
    stubEnv({ ...BASE_ENV, VEXA_OIDC_CA_FILE: join(dir, "ca.pem") });
    const p = (await loadAuthOptions()).providers.find((x) => x.id === "oidc") as unknown as { httpOptions: { ca: string[] } };
    expect(p.httpOptions.ca).toContain(CERT.trim());
    expect(p.httpOptions.ca.length).toBeGreaterThan(10);
  });

  it("is absent without an issuer, and when VEXA_SIGNIN_METHODS leaves it out", async () => {
    expect((await loadAuthOptions()).providers.map((x) => x.id)).not.toContain("oidc");
    stubEnv({ ...BASE_ENV, VEXA_SIGNIN_METHODS: "email" });
    expect((await loadAuthOptions()).providers.map((x) => x.id)).not.toContain("oidc");
  });

  it("VEXA_SIGNIN_METHODS=oidc removes configured Google and Microsoft providers", async () => {
    stubEnv({ ...BASE_ENV, GOOGLE_CLIENT_ID: "g", GOOGLE_CLIENT_SECRET: "gs", MICROSOFT_CLIENT_ID: "m", MICROSOFT_CLIENT_SECRET: "ms" });
    // NextAuth applies a provider's `id` option when it boots, so Microsoft still says azure-ad here.
    expect((await loadAuthOptions()).providers.map((x) => x.id).sort()).toEqual(["azure-ad", "google", "oidc"]);
    vi.stubEnv("VEXA_SIGNIN_METHODS", "oidc");
    expect((await loadAuthOptions()).providers.map((x) => x.id)).toEqual(["oidc"]);
  });

  it("the sign-in card shows the configured name", () => {
    expect(providersFrom({ oidc: { id: "oidc", name: "Corporate login" } })).toEqual({ google: false, microsoft: false, oidc: "Corporate login" });
    expect(providersFrom({ oidc: { id: "oidc" } }).oidc).toBe("Single sign-on");
    expect(providersFrom({ google: {} })).toEqual({ google: true, microsoft: false, oidc: null });
  });
});

describe("the identity", () => {
  it("takes the address and name from the ID token alone (ADFS: userinfo answers only sub)", () => {
    const r = verifiedProviderIdentity("oidc", { account: { id_token: idToken(adfsClaims()) } }, BASE_ENV);
    expect(r).toEqual({ ok: true, email: "ana.lopez@corp.example", subject: oidcSubject(ISSUER, adfsClaims().sub) });
    expect(oidcSubject(ISSUER, "x")).toMatch(/^oidc:[0-9a-f]{64}$/);
    expect(oidcDisplayName({ given_name: "Ana", family_name: "Lopez" })).toBe("Ana Lopez");
  });

  it("the subject is stable per issuer and differs across issuers; a trailing slash is the same issuer", () => {
    const a = verifiedProviderIdentity("oidc", { account: { id_token: idToken(adfsClaims({ iss: `${ISSUER}/` })) } }, BASE_ENV);
    const b = verifiedProviderIdentity("oidc", { account: { id_token: idToken(adfsClaims()) } }, BASE_ENV);
    expect(a).toEqual(b);
    expect(oidcSubject(ISSUER, "s")).not.toBe(oidcSubject("https://other.example.test", "s"));
  });

  it("reads the configured email claim (e.g. upn)", () => {
    const env = { ...BASE_ENV, VEXA_OIDC_EMAIL_CLAIM: "upn" };
    const claims = adfsClaims({ email: undefined, upn: "ana.lopez@corp.example" });
    expect(verifiedProviderIdentity("oidc", { account: { id_token: idToken(claims) } }, env)).toMatchObject({ ok: true, email: "ana.lopez@corp.example" });
  });

  it.each([
    ["a token from another issuer", adfsClaims({ iss: "https://evil.example.test/realms/corp" }), BASE_ENV, /another issuer/],
    ["a token with no issuer", adfsClaims({ iss: undefined }), BASE_ENV, /another issuer/],
    ["a token with no subject", adfsClaims({ sub: undefined }), BASE_ENV, /no subject/],
    ["a token with no email", adfsClaims({ email: undefined }), BASE_ENV, /no email/],
    ["a malformed email", adfsClaims({ email: "not-an-address" }), BASE_ENV, /no email/],
    ["an unverified email when verification is required", adfsClaims({ email_verified: false }),
      { ...BASE_ENV, VEXA_OIDC_REQUIRE_EMAIL_VERIFIED: "1" }, /not verified/],
    ["a token without email_verified when verification is required", adfsClaims(),
      { ...BASE_ENV, VEXA_OIDC_REQUIRE_EMAIL_VERIFIED: "true" }, /not verified/],
  ])("refuses %s", (_label, claims, env, why) => {
    const r = verifiedProviderIdentity("oidc", { account: { id_token: idToken(claims) } }, env);
    expect(r.ok).toBe(false);
    expect((r as { why: string }).why).toMatch(why);
  });

  it("refuses when the provider is not configured, or no ID token came back", () => {
    expect(verifiedProviderIdentity("oidc", { account: { id_token: idToken(adfsClaims()) } }, {}).ok).toBe(false);
    expect(verifiedProviderIdentity("oidc", { account: {} }, BASE_ENV).ok).toBe(false);
  });

  it("accepts email_verified=true when verification is required", () => {
    const env = { ...BASE_ENV, VEXA_OIDC_REQUIRE_EMAIL_VERIFIED: "1" };
    expect(verifiedProviderIdentity("oidc", { account: { id_token: idToken(adfsClaims({ email_verified: true })) } }, env).ok).toBe(true);
  });
});

describe("the sign-in callback", () => {
  const signIn = async (claims: Record<string, unknown>, user = { email: "someone-else@corp.example", name: "X" }) => {
    const opts = await loadAuthOptions();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    return (opts.callbacks!.signIn as any)({ user, account: { provider: "oidc", id_token: idToken(claims) }, profile: claims });
  };

  it("admits through admission with the token's address and the oidc subject", async () => {
    stubEnv(BASE_ENV);
    expect(await signIn(adfsClaims())).toBe(true);
    expect(findOrCreateUserToken).toHaveBeenCalledWith("ana.lopez@corp.example", { subject: oidcSubject(ISSUER, adfsClaims().sub) });
  });

  it("refuses a token from another issuer before asking admin-api", async () => {
    stubEnv(BASE_ENV);
    expect(await signIn(adfsClaims({ iss: "https://evil.example.test" }))).toBe(`/?error=${SIGNIN_ERROR_UNVERIFIED}`);
    expect(findOrCreateUserToken).not.toHaveBeenCalled();
  });

  it("an address the allow-list does not admit is refused (and so is another identity for a bound account)", async () => {
    stubEnv(BASE_ENV);
    findOrCreateUserToken.mockResolvedValue({ ok: false, refused: "not-allowed", error: "not allowed" });
    expect(await signIn(adfsClaims())).toBe(`/?error=${SIGNIN_ERROR_NOT_ALLOWED}`);
  });

  it("refuses an oidc callback when the provider is not configured", async () => {
    expect(await signIn(adfsClaims())).toBe(`/?error=${SIGNIN_ERROR_UNVERIFIED}`);
    expect(findOrCreateUserToken).not.toHaveBeenCalled();
  });

  it("refuses an unknown provider outright", async () => {
    stubEnv(BASE_ENV);
    const opts = await loadAuthOptions();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    expect(await (opts.callbacks!.signIn as any)({ user: { email: "a@b.c" }, account: { provider: "github" } })).toBe(false);
  });
});

describe("closing the emailed link", () => {
  it("request-link and redeem answer 404, and the instance says email_link: false", async () => {
    stubEnv({ ...BASE_ENV, VEXA_SIGNIN_METHODS: "oidc", NEXTAUTH_SECRET: STRONG, NEXTAUTH_URL: "https://terminal.test" });
    vi.resetModules();
    const { POST } = await import("../request-link/route");
    const res = await POST({ json: async () => ({ email: "ana@corp.example" }), headers: new Headers() } as never);
    expect(res.status).toBe(404);
    const { GET } = await import("../redeem/route");
    const page = await GET({ nextUrl: new URL("https://terminal.test/api/auth/redeem?t=x") } as never);
    expect(page.status).toBe(404);
    const { GET: instance } = await import("../instance/route");
    expect(await (await instance()).json()).toEqual({ admin_exists: true, email_link: false });
  });
});
