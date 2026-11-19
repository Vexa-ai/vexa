import { afterEach, expect, it, vi } from "vitest";
import { fetchDurableTranscript } from "../liveMeetings";
afterEach(() => vi.unstubAllGlobals());
it("preserves subsecond absolute boundaries and legacy relative offsets", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ segments: [
    { start: 1791460000.125, end: 1791460003.375, text: "Timed passage" },
    { start: 2.75, text: "Legacy passage" },
  ] }) }));
  const { lines } = await fetchDurableTranscript("1");
  expect(lines[0]).toMatchObject({ tsMs: 1791460000125, endMs: 1791460003375 });
  expect(lines[1]).toMatchObject({ offsetSeconds: 2.75, tsMs: undefined });
});
