/** Where NextAuth sends the browser after an OAuth sign-in (`callbacks.redirect`): a site path on this
 *  instance, or an absolute URL on exactly this instance's origin — nothing else. */
import { describe, expect, it, vi } from "vitest";

vi.mock("../adminApi", () => ({
  AUTH_COOKIE: "vexa-token",
  USER_INFO_COOKIE: "vexa-user-info",
  findOrCreateUserToken: async () => ({ ok: false, status: 500, error: "unused" }),
  mintFirstVisitScaffold: async () => ({ ok: false, status: 409, error: "unused" }),
}));
vi.mock("next/headers", () => ({
  cookies: async () => ({ get: () => undefined, set: () => {}, delete: () => {} }),
}));

import { authOptions, sameOrigin } from "../[...nextauth]/authOptions";

const BASE = "https://vexa.example.com";
const redirect = (url: string) => authOptions.callbacks!.redirect!({ url, baseUrl: BASE });

describe("the post-sign-in redirect", () => {
  it("keeps a site path on this instance", async () => {
    expect(await redirect("/w/abc?x=1")).toBe(`${BASE}/w/abc?x=1`);
  });

  it("keeps an absolute URL on this exact origin", async () => {
    expect(await redirect(`${BASE}/meetings/42`)).toBe(`${BASE}/meetings/42`);
  });

  it.each([
    "https://vexa.example.com.attacker.test/landing",   // a host that begins with this one's name
    "https://vexa.example.computer/landing",
    "https://vexa.example.com@attacker.test/landing",    // credentials-style prefix
    "https://vexa.example.com:8443/landing",             // same host, another port
    "http://vexa.example.com/landing",                   // same host, another scheme
    "https://attacker.test/",
  ])("sends %s back to the instance instead", async (target) => {
    expect(await redirect(target)).toBe(BASE);
  });

  it("compares whole origins", () => {
    expect(sameOrigin(`${BASE}/x`, BASE)).toBe(true);
    expect(sameOrigin("https://vexa.example.com.attacker.test/x", BASE)).toBe(false);
    expect(sameOrigin("not a url", BASE)).toBe(false);
  });
});
