/** Isolation harness — the fail-loud HTTP transport (P18). The whole point: an error must THROW, not
 *  be swallowed into empty data. A 200 returns the parsed body (legit-empty included). */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { getJson, ApiError } from "../apiClient";

afterEach(() => vi.restoreAllMocks());

function mock(resp: Partial<Response> & { jsonBody?: unknown }) {
  globalThis.fetch = vi.fn(async () => ({ ok: resp.ok ?? true, status: resp.status ?? 200, json: async () => resp.jsonBody ?? {} }) as unknown as Response) as unknown as typeof fetch;
}

describe("apiClient.getJson — fail-loud", () => {
  it("returns the parsed body on 200 (legit-empty is not an error)", async () => {
    mock({ ok: true, status: 200, jsonBody: { sessions: [] } });
    expect(await getJson("/api/sessions")).toEqual({ sessions: [] });
  });
  it("THROWS ApiError with status + detail on a non-ok response (never swallows)", async () => {
    mock({ ok: false, status: 422, jsonBody: { detail: "Field required" } });
    await expect(getJson("/api/sessions")).rejects.toMatchObject({ name: "ApiError", status: 422, detail: "Field required" });
  });
  it("THROWS ApiError(status 0) on a network/DNS failure", async () => {
    globalThis.fetch = vi.fn(async () => { throw new TypeError("Failed to fetch"); }) as unknown as typeof fetch;
    const err = await getJson("/api/sessions").catch((e) => e as ApiError);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(0);
  });
});
