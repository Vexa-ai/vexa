/** Sign-out ends the credential, not only the cookie (#1868). Before, logout expired `vexa-token`
 *  and the session it carried stayed valid for the rest of its 30 days. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const cookieJar: Record<string, string> = {};
vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) => (cookieJar[name] !== undefined ? { name, value: cookieJar[name] } : undefined),
  }),
  headers: async () => new Headers({ host: "app.example.test" }),
}));

import { POST as logout } from "../logout/route";

const fetchMock = vi.fn(async () => new Response(null, { status: 204 }));

beforeEach(() => {
  for (const k of Object.keys(cookieJar)) delete cookieJar[k];
  process.env.VEXA_ADMIN_API_URL = "http://admin-api:8001";
  process.env.VEXA_INTERNAL_API_SECRET = "internal-test-secret";
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

const ended = () => fetchMock.mock.calls.map(([url, init]) => {
  const i = init as RequestInit;
  return { url: String(url), secret: (i.headers as Record<string, string>)["X-Internal-Secret"],
           token: JSON.parse(String(i.body)).token };
});

describe("logout", () => {
  it("ends the session the cookie carried, and clears the cookie", async () => {
    cookieJar["vexa-token"] = "vxa_browser_session_value";
    const res = await logout();
    expect(res.status).toBe(200);
    expect(ended()).toEqual([{ url: "http://admin-api:8001/internal/sessions/end",
                               secret: "internal-test-secret", token: "vxa_browser_session_value" }]);
    expect(res.headers.get("set-cookie")).toContain("vexa-token=;");
  });

  it("in company mode ends the person's own session too", async () => {
    cookieJar["vexa-token"] = "vxa_bot_company_credential";
    cookieJar["vexa-person-token"] = "vxa_browser_person_session";
    await logout();
    expect(ended().map((c) => c.token).sort()).toEqual(["vxa_bot_company_credential", "vxa_browser_person_session"]);
  });

  it("still clears the cookies when identity cannot be reached", async () => {
    cookieJar["vexa-token"] = "vxa_browser_session_value";
    fetchMock.mockRejectedValueOnce(new Error("down"));
    const res = await logout();
    expect(res.status).toBe(200);
    expect(res.headers.get("set-cookie")).toContain("vexa-token=;");
  });

  it("calls nothing without a cookie", async () => {
    await logout();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
