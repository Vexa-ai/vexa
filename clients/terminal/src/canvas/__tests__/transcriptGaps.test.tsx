// Transcript gaps (prod-cutover R4): audio the meeting never transcribed shows as a quiet marker
// where the words would have been — asked for explicitly, never mixed into the segments.
import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { LiveTranscriptEngine } from "../LiveTranscriptEngine";
import { fetchDurableTranscript } from "../../surfaces/liveMeetings";

function render(ui: React.ReactElement) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => { root.render(ui); });
  return { container, unmount: () => act(() => root.unmount()) };
}

const T0 = 1_781_946_000_000;
const segments = [
  { id: "a", speaker: "Anna", text: "First words.", tsMs: T0 + 10_000, completed: true },
  { id: "b", speaker: "Anna", text: "Words after the gap.", tsMs: T0 + 120_000, completed: true },
];
const gap = { startMs: T0 + 75_000, endMs: T0 + 111_000, label: "gap 01:15–01:51 (not transcribed)" };

afterEach(() => vi.unstubAllGlobals());

describe("transcript gaps", () => {
  for (const layout of ["stack", "rows"] as const) {
    it(`draws the marker between the words around it (${layout})`, () => {
      const { container, unmount } = render(<LiveTranscriptEngine segments={segments} gaps={[gap]} layout={layout} />);
      const marker = container.querySelector("[data-transcript-gap]");
      expect(marker?.textContent).toBe("gap 01:15–01:51 (not transcribed)");
      const text = container.textContent ?? "";
      expect(text.indexOf("First words.")).toBeLessThan(text.indexOf("gap 01:15"));
      expect(text.indexOf("gap 01:15")).toBeLessThan(text.indexOf("Words after the gap."));
      unmount();
    });
  }

  it("draws nothing extra when there are no gaps", () => {
    const { container, unmount } = render(<LiveTranscriptEngine segments={segments} />);
    expect(container.querySelector("[data-transcript-gap]")).toBeNull();
    unmount();
  });

  it("a meeting that lost all of its audio shows the gap, not 'nothing said'", () => {
    const { container, unmount } = render(<LiveTranscriptEngine segments={[]} gaps={[gap]} emptyLabel="Nothing said yet" />);
    expect(container.textContent).toContain("gap 01:15–01:51 (not transcribed)");
    expect(container.textContent).not.toContain("Nothing said yet");
    unmount();
  });

  it("the recorded transcript asks meeting-api for its gaps and carries them", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({
      segments: [{ start: 1781946010, end: 1781946012, text: "First words.", speaker: "Anna" }],
      gaps: [{ start: 1781946075, end: 1781946111, absolute_start_time: "2026-06-20T09:01:15.000Z",
        absolute_end_time: "2026-06-20T09:01:51.000Z", label: "gap 01:15–01:51 (not transcribed)" }],
    }) });
    vi.stubGlobal("fetch", fetchMock);
    const t = await fetchDurableTranscript("42");
    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/transcripts/by-id/42?include_gaps=true");
    expect(t.lines.map((l) => l.text)).toEqual(["First words."]);
    expect(t.gaps).toEqual([{ startMs: Date.parse("2026-06-20T09:01:15.000Z"), endMs: Date.parse("2026-06-20T09:01:51.000Z"),
      label: "gap 01:15–01:51 (not transcribed)" }]);
  });
});
