/** Isolation harness — agent-sessions data-access. Scoped (no subject, P20) + fail-loud (P18). */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { addParticipant, listParticipants, listSessions, removeParticipant, sessionHistory, sessionMessages } from "../sessionsApi";
import { ApiError } from "../../data/apiClient";

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
  it("sessionHistory carries a refused save as a typed fault (ADR-0048 S5), and none when there is none", async () => {
    fetchMock.mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ turns: [],
      fault: { source: "session-store", kind: "session_conflict", detail: "The last reply was not saved.", remedy: "Send it again." } }) } as unknown as Response);
    const h = await sessionHistory("s-1");
    expect(h.fault?.kind).toBe("session_conflict");
    expect(h.fault?.source).toBe("session-store");
    expect((await sessionHistory("s-1")).fault).toBeNull();
  });
  it("FAIL-LOUD: a backend error throws ApiError (never a silent empty list)", async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 422, json: async () => ({ detail: "x" }) } as unknown as Response);
    await expect(listSessions()).rejects.toBeInstanceOf(ApiError);
  });
});

describe("sessionsApi — participants (ADR-0048)", () => {
  it("lists, adds by email, removes — each on the session's own path", async () => {
    await listParticipants("pchat-1");
    expect(lastUrl()).toBe("/api/sessions/pchat-1/participants");
    await addParticipant("pchat-1", { email: "bob@acme.test" });
    const [url, init] = fetchMock.mock.calls.at(-1)! as [string, RequestInit];
    expect(url).toBe("/api/sessions/pchat-1/participants");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ email: "bob@acme.test", role: "member" });
    await removeParticipant("s-1", "bob");
    expect(lastUrl()).toBe("/api/sessions/s-1/participants/bob");
    expect((fetchMock.mock.calls.at(-1)![1] as RequestInit).method).toBe("DELETE");
    await sessionMessages("s-1");
    expect(lastUrl()).toBe("/api/sessions/s-1/messages");
  });
  it("FAIL-LOUD: a refused add throws (the owner rule is the server's)", async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 403, json: async () => ({ detail: "not_session_owner" }) } as unknown as Response);
    await expect(addParticipant("s-1", { email: "x@y.z" })).rejects.toBeInstanceOf(ApiError);
  });
});
