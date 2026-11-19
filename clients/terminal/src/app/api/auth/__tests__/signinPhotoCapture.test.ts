// @vitest-environment node
/** The OAuth sign-in hands identity the provider's picture after the account is found or created,
 *  and a failure to do so never refuses the sign-in (UI 15g). */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const findOrCreateUserToken = vi.fn();
const capture = vi.fn();
vi.mock("../adminApi", () => ({
  AUTH_COOKIE: "vexa-token",
  USER_INFO_COOKIE: "vexa-user-info",
  findOrCreateUserToken: (...a: unknown[]) => findOrCreateUserToken(...a),
  mintFirstVisitScaffold: async () => ({ ok: false, status: 409, error: "returning" }),
}));
vi.mock("../signinPhoto", async (orig) => ({
  ...(await orig<typeof import("../signinPhoto")>()),
  captureSigninPhoto: (...a: unknown[]) => capture(...a),
}));
vi.mock("next/headers", () => ({
  cookies: async () => ({ get: () => undefined, set: () => {}, delete: () => {} }),
}));

const PICTURE = "https://lh3.googleusercontent.com/a/the-persons-picture=s96-c";

async function googleSignIn() {
  vi.resetModules();
  const { authOptions } = await import("../[...nextauth]/authOptions");
  const profile = { email: "ana@corp.example", email_verified: true, sub: "1234567890", picture: PICTURE };
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return (authOptions.callbacks!.signIn as any)({ user: { name: "Ana", image: PICTURE }, account: { provider: "google", id_token: "h.p.s" }, profile });
}

beforeEach(() => {
  vi.stubEnv("GOOGLE_CLIENT_ID", "gid");
  vi.stubEnv("GOOGLE_CLIENT_SECRET", "gsecret");
  vi.stubEnv("VEXA_SIGNIN_METHODS", "");
  findOrCreateUserToken.mockReset();
  findOrCreateUserToken.mockResolvedValue({ ok: true, token: "tok", user: { id: 7, email: "ana@corp.example" } });
  capture.mockReset();
});
afterEach(() => vi.unstubAllEnvs());

describe("the provider's picture at sign-in", () => {
  it("a Google sign-in passes its own ID token to identity, never a picture URL", async () => {
    capture.mockResolvedValue(true);
    expect(await googleSignIn()).toBe(true);
    expect(capture).toHaveBeenCalledWith(7, { provider: "google", id_token: "h.p.s" });
  });

  it("identity failing to take it never refuses the sign-in", async () => {
    capture.mockResolvedValue(false);
    expect(await googleSignIn()).toBe(true);
  });

  it("a refused sign-in tells identity nothing", async () => {
    findOrCreateUserToken.mockResolvedValue({ ok: false, refused: "not-allowed", error: "no" });
    await googleSignIn();
    expect(capture).not.toHaveBeenCalled();
  });
});
