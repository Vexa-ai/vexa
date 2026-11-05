import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const cookieValues = new Map<string, string>();

vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) =>
      cookieValues.has(name) ? { name, value: cookieValues.get(name)! } : undefined,
  }),
}));

type Call = { url: string; method: string; headers: Record<string, string>; body: unknown };

let stored: {
  webhook_url: string | null;
  webhook_secret: string | null;
  webhook_events: Record<string, boolean> | null;
};
let calls: Call[];

/** The gateway's /user/webhook tier: stores what PUT sends, masks the secret on GET. */
function fakeGateway(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const url = String(input);
  const method = init?.method || "GET";
  const headers = (init?.headers || {}) as Record<string, string>;
  const body = init?.body ? JSON.parse(String(init.body)) : undefined;
  calls.push({ url, method, headers, body });

  if (url !== "http://gateway.test/user/webhook") {
    return Promise.resolve(new Response("not found", { status: 404 }));
  }
  if (headers["X-API-Key"] !== "user-token") {
    return Promise.resolve(new Response("unauthorized", { status: 401 }));
  }
  if (method === "PUT") {
    stored.webhook_url = body.webhook_url;
    if (body.webhook_secret) stored.webhook_secret = body.webhook_secret;
    if (body.webhook_events) stored.webhook_events = body.webhook_events;
    return Promise.resolve(new Response(JSON.stringify({ id: 42, data: {} })));
  }
  const secret = stored.webhook_secret;
  return Promise.resolve(
    new Response(
      JSON.stringify({
        webhook_url: stored.webhook_url,
        webhook_secret_set: Boolean(secret),
        webhook_secret: secret ? "********" + secret.slice(-4) : null,
        webhook_events: stored.webhook_events,
      })
    )
  );
}

function putRequest(body: unknown) {
  return new NextRequest("https://dashboard.example.com/api/webhooks/config", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

const savedEnv: Record<string, string | undefined> = {};

beforeEach(() => {
  for (const key of ["VEXA_API_URL", "VEXA_ADMIN_API_URL", "VEXA_ADMIN_API_KEY"]) {
    savedEnv[key] = process.env[key];
  }
  process.env.VEXA_API_URL = "http://gateway.test";
  process.env.VEXA_ADMIN_API_URL = "http://admin.test";
  process.env.VEXA_ADMIN_API_KEY = "admin-key";
  cookieValues.clear();
  cookieValues.set("vexa-token", "user-token");
  stored = { webhook_url: null, webhook_secret: null, webhook_events: null };
  calls = [];
  vi.stubGlobal("fetch", vi.fn(fakeGateway));
});

afterEach(() => {
  for (const [key, value] of Object.entries(savedEnv)) {
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("PUT /api/webhooks/config", () => {
  it("stores the URL, secret and events through the user's /user/webhook", async () => {
    const { PUT } = await import("@/app/api/webhooks/config/route");
    const events = { "meeting.completed": true, "meeting.status_change": false };
    const res = await PUT(
      putRequest({ endpoint_url: "https://hooks.example.com/v", signing_secret: "test-secret", events })
    );

    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({
      endpoint_url: "https://hooks.example.com/v",
      signing_secret_masked: "****cret",
      events,
    });
    expect(stored).toEqual({
      webhook_url: "https://hooks.example.com/v",
      webhook_secret: "test-secret",
      webhook_events: events,
    });
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.headers["X-API-Key"]).toBe("user-token");
    expect(calls.some((c) => c.url.includes("/admin/"))).toBe(false);
  });

  it("keeps the stored secret when none is typed", async () => {
    stored = {
      webhook_url: "https://old.example.com",
      webhook_secret: "kept-secret-abcd",
      webhook_events: { "meeting.completed": true },
    };
    const { PUT } = await import("@/app/api/webhooks/config/route");
    const res = await PUT(putRequest({ endpoint_url: "https://new.example.com" }));

    expect(res.status).toBe(200);
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.body).not.toHaveProperty("webhook_secret");
    expect(stored.webhook_secret).toBe("kept-secret-abcd");
    expect(stored.webhook_url).toBe("https://new.example.com");
    expect((await res.json()).signing_secret_masked).toBe("********abcd");
  });

  it("generates a secret for a new endpoint saved without one", async () => {
    const { PUT } = await import("@/app/api/webhooks/config/route");
    const res = await PUT(putRequest({ endpoint_url: "https://hooks.example.com/v" }));

    expect(res.status).toBe(200);
    expect(stored.webhook_secret).toMatch(/^whsec_/);
    expect((await res.json()).signing_secret_masked).toBe("****" + stored.webhook_secret!.slice(-4));
  });

  it("reports a gateway refusal instead of claiming the save", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
        init?.method === "PUT" ? new Response("bad", { status: 422 }) : fakeGateway(input, init)
      )
    );
    const { PUT } = await import("@/app/api/webhooks/config/route");
    const res = await PUT(putRequest({ endpoint_url: "https://hooks.example.com/v" }));
    expect(res.status).toBe(502);
  });

  it("is 401 without a session", async () => {
    cookieValues.clear();
    const { PUT } = await import("@/app/api/webhooks/config/route");
    const res = await PUT(putRequest({ endpoint_url: "https://hooks.example.com/v" }));
    expect(res.status).toBe(401);
    expect(calls).toHaveLength(0);
  });
});

describe("GET /api/webhooks/config", () => {
  it("reads the saved config back with the secret masked", async () => {
    stored = {
      webhook_url: "https://hooks.example.com/v",
      webhook_secret: "test-secret",
      webhook_events: { "meeting.completed": true },
    };
    const { GET } = await import("@/app/api/webhooks/config/route");
    const res = await GET();

    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({
      endpoint_url: "https://hooks.example.com/v",
      signing_secret_masked: "********cret",
      events: { "meeting.completed": true },
    });
  });
});

describe("POST /api/webhooks/rotate-secret", () => {
  it("stores a new secret and keeps the URL and events", async () => {
    stored = {
      webhook_url: "https://hooks.example.com/v",
      webhook_secret: "old-secret",
      webhook_events: { "meeting.completed": true },
    };
    const { POST } = await import("@/app/api/webhooks/rotate-secret/route");
    const res = await POST();

    expect(res.status).toBe(200);
    const { signing_secret } = await res.json();
    expect(signing_secret).toMatch(/^whsec_/);
    expect(stored).toEqual({
      webhook_url: "https://hooks.example.com/v",
      webhook_secret: signing_secret,
      webhook_events: { "meeting.completed": true },
    });
    expect(calls.some((c) => c.url.includes("/admin/"))).toBe(false);
  });
});
