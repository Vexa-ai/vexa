// @vitest-environment node
/** A sign-in tells identity about the person's photo (UI decision 15g).
 *
 *  - Google and OIDC: the provider's `picture` URL (https only); Microsoft: the Graph photo bytes the
 *    sign-in already fetched with the token the app has today (no new scope).
 *  - identity (admin-api) fetches, checks and stores it; the terminal never stores or serves a face.
 *  - it never holds up or refuses a sign-in: identity down is a sign-in that works without a photo.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { captureSigninPhoto, signinPhotoBody } from "../signinPhoto";

const JPEG_DATA = "data:image/jpeg;base64, /9j/4AAQSkZJRgABAQAAAQABAAD/2Q==";

beforeEach(() => {
  vi.stubEnv("VEXA_ADMIN_API_URL", "http://admin-api:8001");
  vi.stubEnv("VEXA_INTERNAL_API_SECRET", "internal-secret");
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("what a sign-in says about the photo", () => {
  it("Google: the sign-in's raw ID token — identity verifies it and reads `picture` itself", () => {
    expect(signinPhotoBody("google", { profile: { picture: "https://lh3.googleusercontent.com/a/x=s96-c" }, account: { id_token: "h.p.s" } }))
      .toEqual({ provider: "google", id_token: "h.p.s" });
  });
  it("OIDC: the sign-in's raw ID token", () => {
    expect(signinPhotoBody("oidc", { profile: { picture: "https://idp.example.test/p/1.png" }, account: { id_token: "h.p.s" } }))
      .toEqual({ provider: "oidc", id_token: "h.p.s" });
  });
  it("never a picture URL: identity takes the address only from the verified token", () => {
    for (const p of ["google", "oidc", "microsoft"]) {
      const body = signinPhotoBody(p, { profile: { picture: "https://x.example/p.png" }, user: { image: "https://x.example/p.png" }, account: { id_token: "h.p.s", access_token: "t" } });
      expect(body).not.toHaveProperty("url");
      expect(JSON.stringify(body)).not.toContain("x.example");
    }
  });
  it("Microsoft: the sign-in's own access token, so identity fetches Graph itself — never bytes", () => {
    expect(signinPhotoBody("microsoft", { user: { image: JPEG_DATA }, account: { access_token: "eyJ.tok" } }))
      .toEqual({ provider: "microsoft", access_token: "eyJ.tok" });
  });
  it("no body ever carries image bytes", () => {
    for (const p of ["google", "microsoft", "oidc"]) {
      const body = signinPhotoBody(p, { user: { image: JPEG_DATA }, profile: { picture: JPEG_DATA }, account: {} });
      expect(JSON.stringify(body)).not.toContain("base64");
      expect(body).not.toHaveProperty("data");
    }
  });
  it("a sign-in without a token passes on nothing but the provider", () => {
    expect(signinPhotoBody("google", { profile: { picture: "http://example.com/p.jpg" } })).toEqual({ provider: "google" });
    expect(signinPhotoBody("oidc", { account: { id_token: "not-a-jwt" } })).toEqual({ provider: "oidc" });
    expect(signinPhotoBody("microsoft", { account: {} })).toEqual({ provider: "microsoft" });
  });
  it("an emailed link carries no picture (identity may still check Gravatar)", () => {
    expect(signinPhotoBody("", {})).toEqual({});
  });
});

describe("captureSigninPhoto", () => {
  it("PUTs to identity's internal sign-in-photo door with the internal secret", async () => {
    const fetchSpy = vi.fn(async () => new Response(JSON.stringify({ provider: "stored" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchSpy);
    await captureSigninPhoto(7, { provider: "google", id_token: "h.p.s" });
    const [url, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://admin-api:8001/internal/users/7/signin-photo");
    expect(init.method).toBe("PUT");
    expect((init.headers as Record<string, string>)["X-Internal-Secret"]).toBe("internal-secret");
    expect(JSON.parse(String(init.body))).toEqual({ provider: "google", id_token: "h.p.s" });
  });
  it("never throws: identity down is a sign-in without a photo", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("ECONNREFUSED"); }));
    await expect(captureSigninPhoto(7, { provider: "google" })).resolves.toBe(false);
  });
  it("does nothing without the internal edge configured", async () => {
    vi.stubEnv("VEXA_INTERNAL_API_SECRET", "");
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    await expect(captureSigninPhoto(7, {})).resolves.toBe(false);
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
