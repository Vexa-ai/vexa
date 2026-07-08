/** Durable catch-up bounded-retry loop (useLiveMeetingState in useMeeting.ts, ADR-0027).
 *
 *  After a stop, the durable `data.processed` completes only once the db-writer drains through the
 *  worker's view_end marker (≤ ~2 ticks). On the terminal/ended transition the hook refetches by
 *  ROW id, up to DURABLE_REFETCH_ATTEMPTS times, DURABLE_REFETCH_DELAY_MS apart, ONLY while the
 *  durable view holds FEWER notes than the pane already saw live, cancel-guarded on unmount.
 *  These tests pin: (a) the retry is bounded (never hammers), (b) it STOPS the instant durable
 *  catches up with the live count, (c) unmount cancels the pending retry.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, cleanup } from "@testing-library/react";
import { createElement } from "react";

// The retry loop lives inside useLiveMeetingState, fed entirely by these module deps — mock them so we
// can drive the finalizer-flush timing deterministically with fake timers.
const fetchDurableTranscript = vi.fn();
const useLiveMeetings = vi.fn();
const useMeetingLive = vi.fn();

vi.mock("../../surfaces/liveMeetings", () => ({
  useLiveMeetings: () => useLiveMeetings(),
  fetchDurableTranscript: (id: string) => fetchDurableTranscript(id),
  mergeNotesById: (a: unknown[], b: unknown[]) => [...(a ?? []), ...(b ?? [])],
}));
vi.mock("../../surfaces/meetingLive", () => ({
  useMeetingLive: () => useMeetingLive(),
}));
vi.mock("../actions", () => ({ useCanvasActionState: () => ({}) }));

import { DURABLE_REFETCH_ATTEMPTS, DURABLE_REFETCH_DELAY_MS, MeetingSourceProvider } from "../useMeeting";

const EMPTY_DURABLE = { lines: [], notes: [] };

// A finalized (terminal) meeting row that HAD live notes — the exact gate the retry needs.
function terminalMeetingWithLiveNotes() {
  useLiveMeetings.mockReturnValue([
    { id: "42", native_id: "aaa-bbb-ccc", session_uid: "aaa-bbb-ccc", platform: "google_meet", status: "stopped" },
  ]);
  useMeetingLive.mockReturnValue({ transcript: [], notes: [{ id: "n1", text: "seen live" }], cards: [], errors: [], issues: [], note: "", ended: true, connected: false, reconnects: 0 });
}

const wrapper = ({ children }: { children: React.ReactNode }) =>
  createElement(MeetingSourceProvider, { meetingId: "42", children });

beforeEach(() => {
  vi.useFakeTimers();
  fetchDurableTranscript.mockReset();
  useLiveMeetings.mockReset();
  useMeetingLive.mockReset();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("useLiveMeetingState durable stop-refetch", () => {
  it("retries the durable fetch at most DURABLE_REFETCH_ATTEMPTS times, then stops", async () => {
    terminalMeetingWithLiveNotes();
    fetchDurableTranscript.mockResolvedValue(EMPTY_DURABLE); // durable stays behind live → keep retrying, bounded

    renderHook(() => null, { wrapper });

    // Initial load(0), then up to DURABLE_REFETCH_ATTEMPTS scheduled retries. Flush each tick + fetch.
    for (let i = 0; i < DURABLE_REFETCH_ATTEMPTS + 3; i++) {
      await vi.advanceTimersByTimeAsync(DURABLE_REFETCH_DELAY_MS);
    }
    // load(0) + all attempts; the last retry's callback does NOT schedule another.
    expect(fetchDurableTranscript).toHaveBeenCalledTimes(DURABLE_REFETCH_ATTEMPTS + 1);
    expect(fetchDurableTranscript).toHaveBeenLastCalledWith("42");
  });

  it("stops retrying the instant the durable row comes back non-empty", async () => {
    terminalMeetingWithLiveNotes();
    fetchDurableTranscript
      .mockResolvedValueOnce(EMPTY_DURABLE)                                   // load(0): still empty → retry
      .mockResolvedValueOnce({ lines: [], notes: [{ id: "n1", text: "flushed" }] }) // retry 1: notes! → stop
      .mockResolvedValue(EMPTY_DURABLE);

    renderHook(() => null, { wrapper });
    for (let i = 0; i < 8; i++) await vi.advanceTimersByTimeAsync(DURABLE_REFETCH_DELAY_MS);

    expect(fetchDurableTranscript).toHaveBeenCalledTimes(2); // no further retries after notes arrived
  });

  it("cancels the pending retry on unmount (no post-unmount refetch)", async () => {
    terminalMeetingWithLiveNotes();
    fetchDurableTranscript.mockResolvedValue(EMPTY_DURABLE);

    const { unmount } = renderHook(() => null, { wrapper });
    await vi.advanceTimersByTimeAsync(0);   // resolve load(0), schedule retry 1
    expect(fetchDurableTranscript).toHaveBeenCalledTimes(1);
    unmount();                               // cancelled = true; clearTimeout(retry)
    await vi.advanceTimersByTimeAsync(DURABLE_REFETCH_DELAY_MS * 6);
    expect(fetchDurableTranscript).toHaveBeenCalledTimes(1); // the cancelled retry never fired
  });
});
