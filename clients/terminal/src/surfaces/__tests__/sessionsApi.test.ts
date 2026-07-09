/** Isolation harness — agent-sessions data-access. Scoped (no subject, P20) + fail-loud (P18). */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { listSessions, sessionHistory } from "../sessionsApi";
import { ApiError } from "../apiClient";

let fetchMock: ReturnType<typeof vi.fn>;
const lastUrl = () => String(fetchMock.mock.calls.at(-1)![0]);

beforeEach(() => {
  fetchMock = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ sessions: [{ session: "s1", title: "Hi" }], turns: [] }) }) as unknown as Response);
  globalThis.fetch = fetchMock as unknown as typeof fetch;
});
afterEach(() => vi.restoreAllMocks());

describe("sessionsApi — scoped (no subject) + fail-loud", () => {
  it("listSessions GETs /api/sessions with no subject", async () => {
    expect((await listSessions())[0].session).toBe("s1");
    expect(lastUrl()).toBe("/api/sessions");
    expect(lastUrl()).not.toContain("subject");
  });
  it("sessionHistory GETs /api/sessions/{id}/history (id encoded), no subject", async () => {
    await sessionHistory("s 1/2");
    expect(lastUrl()).toBe("/api/sessions/s%201%2F2/history");
    expect(lastUrl()).not.toContain("subject");
  });
  it("FAIL-LOUD: a backend error throws ApiError (never a silent empty list)", async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 422, json: async () => ({ detail: "x" }) } as unknown as Response);
    await expect(listSessions()).rejects.toBeInstanceOf(ApiError);
  });
});
