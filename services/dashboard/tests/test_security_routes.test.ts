import { createHmac } from "crypto";
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const findUserByEmail = vi.fn();
const getUserById = vi.fn();
const updateUser = vi.fn();
const getAuthenticatedUserId = vi.fn();
const getAuthenticatedUser = vi.fn();
const cookieValues = new Map<string, string>();

vi.mock("@/lib/vexa-admin-api", () => ({
  findUserByEmail: (...args: unknown[]) => findUserByEmail(...args),
  getUserById: (...args: unknown[]) => getUserById(...args),
  updateUser: (...args: unknown[]) => updateUser(...args),
}));
vi.mock("@/lib/auth-utils", () => ({
  getAuthenticatedUserId: () => getAuthenticatedUserId(),
  getAuthenticatedUser: () => getAuthenticatedUser(),
}));
vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) =>
      cookieValues.has(name) ? { name, value: cookieValues.get(name)! } : undefined,
  }),
}));

const ENV_KEYS = [
  "ZOOM_OAUTH_CLIENT_ID",
  "ZOOM_OAUTH_CLIENT_SECRET",
  "ZOOM_OAUTH_STATE_SECRET",
  "ZOOM_OAUTH_REDIRECT_URI",
  "GOOGLE_CLIENT_ID",
  "GOOGLE_OAUTH_STATE_SECRET",
  "GOOGLE_CALENDAR_REDIRECT_URI",
  "VEXA_API_URL",
  "VEXA_ADMIN_API_URL",
  "VEXA_ADMIN_API_KEY",
  "VEXA_API_KEY",
  "JWT_SECRET",
  "SMTP_HOST",
  "SMTP_USER",
  "SMTP_PASS",
];
const savedEnv: Record<string, string | undefined> = {};

beforeEach(() => {
  for (const key of ENV_KEYS) savedEnv[key] = process.env[key];
  process.env.ZOOM_OAUTH_CLIENT_ID = "zoom-client";
  process.env.ZOOM_OAUTH_CLIENT_SECRET = "zoom-client-secret";
  process.env.ZOOM_OAUTH_STATE_SECRET = "zoom-state-secret";
  delete process.env.ZOOM_OAUTH_REDIRECT_URI;
  process.env.GOOGLE_CLIENT_ID = "google-client";
  process.env.GOOGLE_OAUTH_STATE_SECRET = "google-state-secret";
  delete process.env.GOOGLE_CALENDAR_REDIRECT_URI;
  process.env.VEXA_API_URL = "http://gateway.test";
  process.env.VEXA_ADMIN_API_URL = "http://admin.test";
  process.env.VEXA_ADMIN_API_KEY = "admin-key";
  getAuthenticatedUser.mockResolvedValue({ id: "42", email: "user@example.com" });
});

afterEach(() => {
  for (const key of ENV_KEYS) {
    if (savedEnv[key] === undefined) delete process.env[key];
    else process.env[key] = savedEnv[key];
  }
  cookieValues.clear();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

function jsonRequest(url: string, body: unknown, init: { cookie?: string } = {}) {
  return new NextRequest(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(init.cookie ? { cookie: init.cookie } : {}),
    },
    body: JSON.stringify(body),
  });
}

// The encoding the start routes used before: base64, then +/ -> -_ and padding stripped.
function legacyBase64Url(value: string): string {
  return Buffer.from(value, "utf8")
    .toString("base64")
    .split("+")
    .join("-")
    .split("/")
    .join("_")
    .split("=")
    .join("");
}

function stateParts(authUrl: string) {
  const state = new URL(authUrl).searchParams.get("state")!;
  const [data, signature] = state.split(".");
  return { data, signature, payload: JSON.parse(Buffer.from(data, "base64url").toString("utf8")) };
}

describe.each([
  ["zoom", "@/app/api/zoom/oauth/start/route", "zoom-state-secret", "/auth/zoom/callback"],
  [
    "calendar",
    "@/app/api/calendar/oauth/start/route",
    "google-state-secret",
    "/auth/google-calendar/callback",
  ],
])("%s OAuth start state", (_name, modulePath, secret, callbackPath) => {
  it("is unpadded base64url, byte-identical to the previous encoding, and HMAC-signed", async () => {
    const { POST } = await import(modulePath);
    const res = await POST(
      jsonRequest("https://dashboard.example.com/api/x", {
        userEmail: "user@example.com",
        returnTo: "/meetings/7",
      })
    );
    expect(res.status).toBe(200);
    const { authUrl } = await res.json();
    const { data, signature, payload } = stateParts(authUrl);

    expect(data).not.toMatch(/[+/=]/);
    expect(data).toBe(legacyBase64Url(JSON.stringify(payload)));
    expect(signature).toBe(createHmac("sha256", secret).update(data).digest("base64url"));
    expect(payload).toMatchObject({
      userId: "42",
      email: "user@example.com",
      returnTo: "/meetings/7",
      redirectUri: `https://dashboard.example.com${callbackPath}`,
    });
  });
});

describe("Zoom OAuth pending bot request", () => {
  const pendingRequest = {
    platform: "zoom",
    native_meeting_id: "89237402037",
    passcode: "123456",
    meeting_url: "https://us05web.zoom.us/j/89237402037?pwd=abc",
  };

  it("is carried encrypted in an httpOnly cookie and returned on completion", async () => {
    const start = await import("@/app/api/zoom/oauth/start/route");
    const startRes = await start.POST(
      jsonRequest("https://dashboard.example.com/api/zoom/oauth/start", {
        userEmail: "user@example.com",
        pendingRequest,
      })
    );
    expect(startRes.status).toBe(200);
    const setCookie = startRes.headers.get("set-cookie") ?? "";
    expect(setCookie).toContain("vexa_zoom_pending_bot=");
    expect(setCookie.toLowerCase()).toContain("httponly");
    expect(setCookie.toLowerCase()).toContain("samesite=lax");
    expect(setCookie).not.toContain("123456");
    const sealed = startRes.cookies.get("vexa_zoom_pending_bot")!.value;
    const { authUrl } = await startRes.json();
    const state = new URL(authUrl).searchParams.get("state")!;

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ access_token: "at", refresh_token: "rt", expires_in: 3600 }))
      )
    );
    getUserById.mockResolvedValue({ success: true, data: { id: 42, data: {} } });
    updateUser.mockResolvedValue({ success: true });

    const complete = await import("@/app/api/zoom/oauth/complete/route");
    const completeRes = await complete.POST(
      jsonRequest(
        "https://dashboard.example.com/api/zoom/oauth/complete",
        { code: "c", state },
        { cookie: `vexa_zoom_pending_bot=${sealed}` }
      )
    );
    expect(completeRes.status).toBe(200);
    const body = await completeRes.json();
    expect(body).toMatchObject({ success: true, pendingRequest });
    expect(completeRes.cookies.get("vexa_zoom_pending_bot")?.value).toBe("");
  });

  it("completes without a pending request when none was carried", async () => {
    const start = await import("@/app/api/zoom/oauth/start/route");
    const startRes = await start.POST(
      jsonRequest("https://dashboard.example.com/api/zoom/oauth/start", {
        userEmail: "user@example.com",
      })
    );
    const { authUrl } = await startRes.json();
    const state = new URL(authUrl).searchParams.get("state")!;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ access_token: "at", refresh_token: "rt" }))
      )
    );
    getUserById.mockResolvedValue({ success: true, data: { id: 42, data: {} } });
    updateUser.mockResolvedValue({ success: true });

    const complete = await import("@/app/api/zoom/oauth/complete/route");
    const body = await (
      await complete.POST(
        jsonRequest("https://dashboard.example.com/api/zoom/oauth/complete", { code: "c", state })
      )
    ).json();
    expect(body.success).toBe(true);
    expect(body.pendingRequest).toBeUndefined();
  });

  it("rejects a state with a wrong signature", async () => {
    const start = await import("@/app/api/zoom/oauth/start/route");
    const { authUrl } = await (
      await start.POST(
        jsonRequest("https://dashboard.example.com/api/zoom/oauth/start", {
          userEmail: "user@example.com",
        })
      )
    ).json();
    const [data] = new URL(authUrl).searchParams.get("state")!.split(".");
    const complete = await import("@/app/api/zoom/oauth/complete/route");
    const res = await complete.POST(
      jsonRequest("https://dashboard.example.com/api/zoom/oauth/complete", {
        code: "c",
        state: `${data}.AAAA`,
      })
    );
    expect(res.status).toBe(500);
    expect((await res.json()).error).toContain("Invalid state signature");
  });
});

describe("POST /api/webhooks/test", () => {
  it.each([
    "http://169.254.169.254/latest/meta-data/",
    "http://localhost:8080/hook",
    "http://10.0.0.5/hook",
    "http://admin-api:8001/admin/users",
    "file:///etc/passwd",
  ])("refuses %s without sending anything", async (url) => {
    getAuthenticatedUserId.mockResolvedValue("42");
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const { POST } = await import("@/app/api/webhooks/test/route");
    const res = await POST(jsonRequest("https://dashboard.example.com/api/webhooks/test", { url }));
    expect(res.status).toBe(400);
    expect((await res.json()).success).toBe(false);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("requires a signed-in user", async () => {
    getAuthenticatedUserId.mockResolvedValue(null);
    const { POST } = await import("@/app/api/webhooks/test/route");
    const res = await POST(
      jsonRequest("https://dashboard.example.com/api/webhooks/test", {
        url: "https://hooks.example.com/",
      })
    );
    expect(res.status).toBe(401);
  });
});

describe("GET /api/webhooks/deliveries", () => {
  it("reads test deliveries for the signed-in user, ignoring a userId parameter", async () => {
    cookieValues.set("vexa-token", "user-token");
    getAuthenticatedUserId.mockResolvedValue("42");
    const fetchSpy = vi.fn(async (url: string) => {
      if (url.endsWith("/meetings")) return new Response(JSON.stringify({ meetings: [] }));
      return new Response(JSON.stringify({ data: { webhook_deliveries: [] } }));
    });
    vi.stubGlobal("fetch", fetchSpy);

    const { GET } = await import("@/app/api/webhooks/deliveries/route");
    const res = await GET(
      new NextRequest("https://dashboard.example.com/api/webhooks/deliveries?userId=7&time_range=7d")
    );
    expect(res.status).toBe(200);
    const urls = fetchSpy.mock.calls.map((call) => String(call[0]));
    expect(urls).toContain("http://admin.test/admin/users/42");
    expect(urls.some((u) => u.includes("/admin/users/7"))).toBe(false);
  });

  it("skips user-data deliveries when the session does not resolve to a user", async () => {
    cookieValues.set("vexa-token", "user-token");
    getAuthenticatedUserId.mockResolvedValue(null);
    const fetchSpy = vi.fn(async (_url: string) => new Response(JSON.stringify({ meetings: [] })));
    vi.stubGlobal("fetch", fetchSpy);

    const { GET } = await import("@/app/api/webhooks/deliveries/route");
    const res = await GET(
      new NextRequest("https://dashboard.example.com/api/webhooks/deliveries?userId=..%2Fusers")
    );
    expect(res.status).toBe(200);
    expect(fetchSpy.mock.calls.map((call) => String(call[0]))).toEqual([
      "http://gateway.test/meetings",
    ]);
  });
});

describe.each([
  ["zoom", "@/app/api/zoom/oauth/start/route"],
  ["calendar", "@/app/api/calendar/oauth/start/route"],
])("%s OAuth start is bound to the signed-in user", (_name, modulePath) => {
  it("refuses a request without a session, whatever email the body names", async () => {
    getAuthenticatedUser.mockResolvedValue(null);
    const { POST } = await import(modulePath);
    const res = await POST(
      jsonRequest("https://dashboard.example.com/api/x", { userEmail: "victim@example.com" })
    );
    expect(res.status).toBe(401);
    expect(await res.json()).toEqual({ error: "Not authenticated" });
    expect(findUserByEmail).not.toHaveBeenCalled();
  });

  it("signs state for the session user and ignores a different body email", async () => {
    const { POST } = await import(modulePath);
    const res = await POST(
      jsonRequest("https://dashboard.example.com/api/x", { userEmail: "victim@example.com" })
    );
    expect(res.status).toBe(200);
    const { payload } = stateParts((await res.json()).authUrl);
    expect(payload).toMatchObject({ userId: "42", email: "user@example.com" });
    expect(findUserByEmail).not.toHaveBeenCalled();
  });

  it("does not reveal whether an email is registered", async () => {
    getAuthenticatedUser.mockResolvedValue(null);
    const { POST } = await import(modulePath);
    const known = await POST(
      jsonRequest("https://dashboard.example.com/api/x", { userEmail: "user@example.com" })
    );
    const unknown = await POST(
      jsonRequest("https://dashboard.example.com/api/x", { userEmail: "nobody@example.com" })
    );
    expect(known.status).toBe(unknown.status);
    expect(await known.json()).toEqual(await unknown.json());
  });

  it("looks up the email by the session user id when the session carries none", async () => {
    getAuthenticatedUser.mockResolvedValue({ id: "42", email: "" });
    getUserById.mockResolvedValue({ success: true, data: { id: 42, email: "user@example.com" } });
    const { POST } = await import(modulePath);
    const res = await POST(jsonRequest("https://dashboard.example.com/api/x", {}));
    expect(res.status).toBe(200);
    expect(getUserById).toHaveBeenCalledWith("42");
    expect(stateParts((await res.json()).authUrl).payload.email).toBe("user@example.com");
  });
});

describe("GET /api/webhooks/deliveries/:meetingId", () => {
  const params = (meetingId: string) => ({ params: Promise.resolve({ meetingId }) });

  it("refuses a request without a user session, even with a service key configured", async () => {
    process.env.VEXA_API_KEY = "service-key";
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const { GET } = await import("@/app/api/webhooks/deliveries/[meetingId]/route");
    const res = await GET(
      new NextRequest("https://dashboard.example.com/api/webhooks/deliveries/5"),
      params("5")
    );
    expect(res.status).toBe(401);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("calls the gateway with the user's own token", async () => {
    process.env.VEXA_API_KEY = "service-key";
    cookieValues.set("vexa-token", "user-token");
    const fetchSpy = vi.fn(
      async (_url: string, _init?: RequestInit) => new Response(JSON.stringify({ attempts: [] }))
    );
    vi.stubGlobal("fetch", fetchSpy);
    const { GET } = await import("@/app/api/webhooks/deliveries/[meetingId]/route");
    const res = await GET(
      new NextRequest("https://dashboard.example.com/api/webhooks/deliveries/5"),
      params("5")
    );
    expect(res.status).toBe(200);
    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe("http://gateway.test/admin/webhooks/deliveries/5");
    expect((init?.headers as Record<string, string>)["X-API-Key"]).toBe("user-token");
  });
});

describe("magic-link signing secret", () => {
  function enableSmtp() {
    process.env.SMTP_HOST = "smtp.example.com";
    process.env.SMTP_USER = "u";
    process.env.SMTP_PASS = "p";
  }

  it.each([undefined, "", "default-secret-change-me"])(
    "refuses to send a link when JWT_SECRET is %j",
    async (value) => {
      enableSmtp();
      if (value === undefined) delete process.env.JWT_SECRET;
      else process.env.JWT_SECRET = value;
      const fetchSpy = vi.fn();
      vi.stubGlobal("fetch", fetchSpy);
      const { POST } = await import("@/app/api/auth/send-magic-link/route");
      const res = await POST(
        jsonRequest("https://dashboard.example.com/api/auth/send-magic-link", {
          email: "user@example.com",
        })
      );
      expect(res.status).toBe(503);
      expect((await res.json()).code).toBe("JWT_SECRET_NOT_CONFIGURED");
      expect(fetchSpy).not.toHaveBeenCalled();
    }
  );

  it("refuses to verify a link when JWT_SECRET is unset", async () => {
    delete process.env.JWT_SECRET;
    const { POST } = await import("@/app/api/auth/verify/route");
    const res = await POST(
      jsonRequest("https://dashboard.example.com/api/auth/verify", { token: "anything" })
    );
    expect(res.status).toBe(503);
    expect((await res.json()).code).toBe("JWT_SECRET_NOT_CONFIGURED");
  });

  it("rejects a link signed with the former default secret", async () => {
    process.env.JWT_SECRET = "the-real-secret";
    const jwt = (await import("jsonwebtoken")).default;
    const forged = jwt.sign({ email: "user@example.com", type: "magic-link" }, "default-secret-change-me");
    const { POST } = await import("@/app/api/auth/verify/route");
    const res = await POST(
      jsonRequest("https://dashboard.example.com/api/auth/verify", { token: forged })
    );
    expect(res.status).toBe(401);
    expect(findUserByEmail).not.toHaveBeenCalled();
  });

  it("accepts a link signed with JWT_SECRET", async () => {
    process.env.JWT_SECRET = "the-real-secret";
    const jwt = (await import("jsonwebtoken")).default;
    const token = jwt.sign({ email: "user@example.com", type: "magic-link" }, "the-real-secret");
    findUserByEmail.mockResolvedValue({ success: false, error: { code: "SERVER_ERROR", message: "x" } });
    const { POST } = await import("@/app/api/auth/verify/route");
    const res = await POST(
      jsonRequest("https://dashboard.example.com/api/auth/verify", { token })
    );
    // Past the signature check: the (stubbed) user lookup is what answers.
    expect(findUserByEmail).toHaveBeenCalledWith("user@example.com");
    expect(res.status).not.toBe(401);
  });
});
