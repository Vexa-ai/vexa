/** Isolation harness — Routines data-access. Asserts every call hits the ONE gateway edge under
 *  /api/routines* with NO `subject` (scope is server-derived — P20), AND that a backend error is
 *  FAIL-LOUD: it throws (propagates to the surface) instead of being swallowed into an empty list (P18). */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { listRoutines, deleteRoutine, setRoutineEnabled } from "../routinesApi";
import { ApiError } from "../apiClient";

let fetchMock: ReturnType<typeof vi.fn>;
const lastUrl = () => String(fetchMock.mock.calls.at(-1)![0]);
const lastInit = () => (fetchMock.mock.calls.at(-1)![1] ?? {}) as RequestInit;

beforeEach(() => {
  fetchMock = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ routines: [{ id: "r1", name: "daily", cron: "0 9 * * *", enabled: true }] }) }) as unknown as Response);
  globalThis.fetch = fetchMock as unknown as typeof fetch;
});
afterEach(() => vi.restoreAllMocks());

describe("routinesApi — scoped (no subject) + fail-loud", () => {
  it("listRoutines GETs /api/routines with no subject", async () => {
    expect((await listRoutines())[0].name).toBe("daily");
    expect(lastUrl()).toBe("/api/routines");
    expect(lastUrl()).not.toContain("subject");
  });
  it("deleteRoutine DELETEs /api/routines/{id}, no subject", async () => {
    await deleteRoutine("r1");
    expect(lastUrl()).toBe("/api/routines/r1");
    expect(lastInit().method).toBe("DELETE");
  });
  it("setRoutineEnabled PATCHes /api/routines/{name}/enabled, no subject", async () => {
    await setRoutineEnabled("daily", false);
    expect(lastUrl()).toBe("/api/routines/daily/enabled");
    expect(lastInit().method).toBe("PATCH");
    expect(lastUrl()).not.toContain("subject");
  });
  it("FAIL-LOUD: a backend error throws ApiError (never a silent empty list)", async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 502, json: async () => ({ detail: "upstream" }) } as unknown as Response);
    await expect(listRoutines()).rejects.toBeInstanceOf(ApiError);
  });
  it("FAIL-LOUD: a network failure throws (not [])", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("offline"));
    await expect(listRoutines()).rejects.toBeInstanceOf(ApiError);
  });
});
