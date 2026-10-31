// #841: the dashboard's Delivery History reads the core's per-user delivery ledger
// (GET /user/webhook/deliveries) instead of a store nothing writes. T1–T5 of the Design.
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getAuthenticatedUserId = vi.fn();
const cookieValues = new Map<string, string>();

vi.mock("@/lib/auth-utils", () => ({
  getAuthenticatedUserId: () => getAuthenticatedUserId(),
  getAuthenticatedUser: async () => null,
}));
vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) =>
      cookieValues.has(name) ? { name, value: cookieValues.get(name)! } : undefined,
  }),
}));

const ENV_KEYS = ["VEXA_API_URL", "VEXA_ADMIN_API_URL", "VEXA_ADMIN_API_KEY"];
const savedEnv: Record<string, string | undefined> = {};

const SECRET = "whsec_topsecret_value";
const TEST_URL = `https://hooks.example.com/vexa/inbox?token=${SECRET}`;
const now = () => new Date().toISOString();

const ledgerRow = (over: Record<string, unknown>) => ({
  event_type: "meeting.status_change",
  event_id: `evt_${String(over.outcome)}_${String(over.meeting_id ?? 1)}`,
  target_host: "hooks.example.com",
  outcome: "delivered",
  status_code: 200,
  attempt: 0,
  created_at: now(),
  meeting_id: 1,
  ...over,
});

const testRow = () => ({
  id: "test-1",
  event: "test",
  meeting_id: "test-meeting-123",
  meeting_name: "Test Webhook",
  status: "delivered",
  attempts: 1,
  max_attempts: 1,
  response_status: 200,
  response_time_ms: 12,
  endpoint_url: TEST_URL,
  created_at: now(),
  last_attempt_at: now(),
});

/** fetch stub: the ledger answers `ledger`, the admin user record answers `user`. */
function stubFetch(opts: {
  ledger?: unknown;
  ledgerStatus?: number;
  ledgerThrows?: boolean;
  user?: unknown;
  meetings?: unknown;
}) {
  const spy = vi.fn(async (url: string) => {
    if (url.includes("/user/webhook/deliveries")) {
      if (opts.ledgerThrows) throw new Error("ECONNREFUSED");
      return new Response(JSON.stringify(opts.ledger ?? { deliveries: [] }), {
        status: opts.ledgerStatus ?? 200,
      });
    }
    if (url.endsWith("/meetings")) return new Response(JSON.stringify(opts.meetings ?? { meetings: [] }));
    if (url.includes("/admin/users/")) {
      return new Response(JSON.stringify(opts.user ?? { data: { webhook_deliveries: [] } }));
    }
    return new Response("not found", { status: 404 });
  });
  vi.stubGlobal("fetch", spy);
  return spy;
}

async function listDeliveries(query = "time_range=7d") {
  const { GET } = await import("@/app/api/webhooks/deliveries/route");
  const res = await GET(new NextRequest(`https://dashboard.example.com/api/webhooks/deliveries?${query}`));
  return { res, body: await res.json() };
}

beforeEach(() => {
  for (const key of ENV_KEYS) savedEnv[key] = process.env[key];
  process.env.VEXA_API_URL = "http://gateway.test";
  process.env.VEXA_ADMIN_API_URL = "http://admin.test";
  process.env.VEXA_ADMIN_API_KEY = "admin-key";
  cookieValues.set("vexa-token", "user-token");
  getAuthenticatedUserId.mockResolvedValue("42");
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

describe("T1 mapping: ledger rows → Delivery History rows", () => {
  it("maps every outcome to its status bucket, attempts = attempt + 1, endpoint_url = host", async () => {
    stubFetch({
      ledger: {
        deliveries: [
          ledgerRow({ outcome: "delivered", status_code: 200, attempt: 0, meeting_id: 11 }),
          ledgerRow({ outcome: "queued", status_code: 503, attempt: 2, meeting_id: 12 }),
          ledgerRow({ outcome: "suppressed", status_code: null, meeting_id: 13 }),
          ledgerRow({ outcome: "blocked", status_code: null, meeting_id: 14 }),
          ledgerRow({ outcome: "failed", status_code: 500, attempt: 1, meeting_id: 15 }),
        ],
      },
    });
    const { res, body } = await listDeliveries();
    expect(res.status).toBe(200);
    expect(body.ledger_unavailable).toBe(false);
    const byOutcome = Object.fromEntries(body.deliveries.map((d: any) => [d.outcome, d]));
    expect(byOutcome.delivered).toMatchObject({
      id: "evt_delivered_11",
      event: "meeting.status_change",
      meeting_id: "11",
      status: "delivered",
      attempts: 1,
      response_status: 200,
      endpoint_url: "hooks.example.com",
      source: "ledger",
    });
    expect(byOutcome.queued).toMatchObject({ status: "retrying", attempts: 3, response_status: 503 });
    expect(byOutcome.suppressed).toMatchObject({ status: "suppressed", response_status: null });
    expect(byOutcome.blocked).toMatchObject({ status: "failed", response_status: null });
    expect(byOutcome.failed).toMatchObject({ status: "failed", attempts: 2, response_status: 500 });
    expect(body.stats).toEqual({ total: 5, delivered: 1, retrying: 1, failed: 2 });
  });

  it("?status=failed returns the blocked row and ?status=suppressed the suppressed one", async () => {
    const rows = [
      ledgerRow({ outcome: "blocked", status_code: null, meeting_id: 21 }),
      ledgerRow({ outcome: "suppressed", status_code: null, meeting_id: 22 }),
      ledgerRow({ outcome: "delivered", meeting_id: 23 }),
    ];
    stubFetch({ ledger: { deliveries: rows } });
    const failed = (await listDeliveries("status=failed")).body.deliveries;
    expect(failed.map((d: any) => d.outcome)).toEqual(["blocked"]);
    stubFetch({ ledger: { deliveries: rows } });
    const suppressed = (await listDeliveries("status=suppressed")).body.deliveries;
    expect(suppressed.map((d: any) => d.outcome)).toEqual(["suppressed"]);
  });
});

describe("T2 negative control: the rows come from the ledger read and nowhere else", () => {
  it("the old /meetings source alone yields zero non-test rows", async () => {
    // What the 0.12 core actually returns: meetings whose data has no webhook_delivery* fields.
    const meetings = { meetings: [{ id: 1, data: { name: "Standup" }, updated_at: now() }] };
    const spy = stubFetch({ meetings, ledger: { deliveries: [] } });
    const { body } = await listDeliveries();
    expect(body.deliveries.filter((d: any) => d.source !== "test")).toEqual([]);
    expect(spy.mock.calls.some((c) => String(c[0]).endsWith("/meetings"))).toBe(false);
  });

  it("the same scenario with a two-row ledger yields two source:ledger rows", async () => {
    const meetings = { meetings: [{ id: 1, data: { name: "Standup" }, updated_at: now() }] };
    const spy = stubFetch({
      meetings,
      ledger: { deliveries: [ledgerRow({ meeting_id: 1 }), ledgerRow({ meeting_id: 1, event_type: "meeting.completed", event_id: "evt_b" })] },
    });
    const { body } = await listDeliveries();
    const ledgerRows = body.deliveries.filter((d: any) => d.source === "ledger");
    expect(ledgerRows).toHaveLength(2);
    expect(ledgerRows.map((d: any) => d.id).sort()).toEqual(["evt_b", "evt_delivered_1"]);
    const ledgerCall = spy.mock.calls.find((c) => String(c[0]).includes("/user/webhook/deliveries"))!;
    expect(String(ledgerCall[0])).toBe("http://gateway.test/user/webhook/deliveries?limit=100");
    expect((ledgerCall[1] as RequestInit).headers).toMatchObject({ "X-API-Key": "user-token" });
  });
});

describe("T3 P14: no URL path, query or secret in a non-test row", () => {
  it("ledger rows carry the bare host; the Test row keeps its own URL", async () => {
    stubFetch({
      ledger: { deliveries: [ledgerRow({ meeting_id: 31 })] },
      user: { data: { webhook_deliveries: [testRow()] } },
    });
    const { body } = await listDeliveries();
    const nonTest = body.deliveries.filter((d: any) => d.source !== "test");
    expect(nonTest).toHaveLength(1);
    for (const row of nonTest) {
      const text = JSON.stringify(row);
      expect(text).not.toContain(SECRET);
      expect(text).not.toContain("/vexa/inbox");
      expect(text).not.toContain("?");
      expect(row.endpoint_url).toBe("hooks.example.com");
    }
    const test = body.deliveries.find((d: any) => d.source === "test");
    expect(test).toMatchObject({ id: "test-1", endpoint_url: TEST_URL });
  });
});

describe("T4 ledger down: Test rows still come back, flagged", () => {
  it.each([
    ["gateway answers 503", { ledgerStatus: 503 }],
    ["fetch throws", { ledgerThrows: true }],
  ])("%s → 200 with the Test rows and ledger_unavailable: true", async (_name, failure) => {
    stubFetch({ ...failure, user: { data: { webhook_deliveries: [testRow()] } } });
    const { res, body } = await listDeliveries();
    expect(res.status).toBe(200);
    expect(body.ledger_unavailable).toBe(true);
    expect(body.deliveries.map((d: any) => d.source)).toEqual(["test"]);
    expect(body.stats.total).toBe(1);
  });

  it("a healthy ledger reports ledger_unavailable: false", async () => {
    stubFetch({ ledger: { deliveries: [] } });
    expect((await listDeliveries()).body.ledger_unavailable).toBe(false);
  });
});

describe("T5 per-meeting: /api/webhooks/deliveries/:meetingId reads the ledger", () => {
  const params = (meetingId: string) => ({ params: Promise.resolve({ meetingId }) });

  it("calls /user/webhook/deliveries, never /admin/webhooks/deliveries, and filters by meeting_id", async () => {
    const spy = stubFetch({
      ledger: {
        deliveries: [
          ledgerRow({ meeting_id: 5, outcome: "delivered", status_code: 200 }),
          ledgerRow({ meeting_id: 5, outcome: "failed", status_code: 500, attempt: 1, event_id: "evt_f" }),
          ledgerRow({ meeting_id: 6 }),
        ],
      },
    });
    const { GET } = await import("@/app/api/webhooks/deliveries/[meetingId]/route");
    const res = await GET(new NextRequest("https://dashboard.example.com/api/webhooks/deliveries/5"), params("5"));
    expect(res.status).toBe(200);
    const urls = spy.mock.calls.map((c) => String(c[0]));
    expect(urls).toEqual(["http://gateway.test/user/webhook/deliveries?limit=100"]);
    expect(urls.some((u) => u.includes("/admin/webhooks/deliveries"))).toBe(false);
    expect((spy.mock.calls[0][1] as RequestInit).headers).toMatchObject({ "X-API-Key": "user-token" });

    const { attempts } = await res.json();
    expect(attempts).toHaveLength(2);
    expect(attempts[0]).toMatchObject({
      attempt: 1,
      endpoint_url: "hooks.example.com",
      response_status: 200,
      response_time_ms: null,
      success: true,
    });
    expect(attempts[1]).toMatchObject({ attempt: 2, response_status: 500, success: false });
    expect(JSON.stringify(attempts)).not.toContain("?");
  });

  it("a meeting with no deliveries returns attempts: []", async () => {
    stubFetch({ ledger: { deliveries: [ledgerRow({ meeting_id: 6 })] } });
    const { GET } = await import("@/app/api/webhooks/deliveries/[meetingId]/route");
    const res = await GET(new NextRequest("https://dashboard.example.com/api/webhooks/deliveries/5"), params("5"));
    expect(await res.json()).toMatchObject({ attempts: [], ledger_unavailable: false });
  });
});

describe("T6 isolation: the ledger is read for the session user only", () => {
  it("fetches with the session token and no userId value reaches any fetch URL", async () => {
    const spy = stubFetch({ ledger: { deliveries: [ledgerRow({ meeting_id: 61 })] } });
    const { body } = await listDeliveries("userId=7&time_range=7d");
    expect(body.deliveries.map((d: any) => d.source)).toEqual(["ledger"]);
    const urls = spy.mock.calls.map((c) => String(c[0]));
    expect(urls).toContain("http://gateway.test/user/webhook/deliveries?limit=100");
    expect(urls).toContain("http://admin.test/admin/users/42");
    for (const url of urls) {
      expect(url).not.toMatch(/userId|user_id|\/users\/7\b/);
    }
    const ledgerCall = spy.mock.calls.find((c) => String(c[0]).includes("/user/webhook/deliveries"))!;
    expect((ledgerCall[1] as RequestInit).headers).toEqual({ "X-API-Key": "user-token" });
  });

  it("a session that does not resolve to a user reads the ledger only, never /meetings", async () => {
    getAuthenticatedUserId.mockResolvedValue(null);
    const spy = stubFetch({ ledger: { deliveries: [] } });
    const { res } = await listDeliveries("userId=..%2Fusers");
    expect(res.status).toBe(200);
    expect(spy.mock.calls.map((c) => String(c[0]))).toEqual([
      "http://gateway.test/user/webhook/deliveries?limit=100",
    ]);
  });

  it("refuses a request without a session before any fetch", async () => {
    cookieValues.clear();
    const spy = stubFetch({});
    const { res } = await listDeliveries();
    expect(res.status).toBe(401);
    expect(spy).not.toHaveBeenCalled();
  });
});
