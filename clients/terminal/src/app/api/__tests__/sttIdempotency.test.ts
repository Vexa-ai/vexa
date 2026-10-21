import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../proxyAuth", () => ({ resolveApiKey: async () => "user-key" }));

import { POST } from "../stt/route";

/** #1344: the hosted transcription API answers 400 to a transcription request without an
 *  `Idempotency-Key` of 1..255 characters. The dictation proxy forwards one request per clip and
 *  never retries, so each clip carries its own fresh key. */
describe("stt dictation proxy — Idempotency-Key", () => {
  const keys: Array<string | null> = [];
  const realFetch = globalThis.fetch;

  beforeEach(() => {
    keys.length = 0;
    process.env.TRANSCRIPTION_SERVICE_URL = "http://stt.test";
    globalThis.fetch = (async (_url: unknown, init?: RequestInit) => {
      keys.push(new Headers(init?.headers).get("Idempotency-Key"));
      return new Response(JSON.stringify({ text: "ok", segments: [] }), { status: 200 });
    }) as typeof fetch;
  });
  afterEach(() => {
    globalThis.fetch = realFetch;
    delete process.env.TRANSCRIPTION_SERVICE_URL;
  });

  const clip = () => new Request("http://terminal.test/api/stt", { method: "POST", body: new Uint8Array(4096) });

  it("every forwarded clip carries a fresh key of 1..255 chars", async () => {
    expect((await POST(clip())).status).toBe(200);
    expect((await POST(clip())).status).toBe(200);
    expect(keys).toHaveLength(2);
    for (const k of keys) {
      expect(k).toBeTruthy();
      expect(k!.length).toBeGreaterThanOrEqual(1);
      expect(k!.length).toBeLessThanOrEqual(255);
    }
    expect(keys[0]).not.toBe(keys[1]);
  });
});
