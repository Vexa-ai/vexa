/** Bug-3 stop-refetch bounded-retry loop (useLiveMeetingState in useMeeting.ts).
 *
 *  The reviewer's non-blocking gap: processedNotesHydration.test.ts pins fetchDurableTranscript's URL
 *  but nothing exercised the BOUNDED-RETRY logic — the bug-prone part. After a meeting finalizes, the
 *  durable `data.processed` can lag, so on the terminal transition the hook refetches by ROW id, up to
 *  DURABLE_REFETCH_ATTEMPTS(5) times, DURABLE_REFETCH_DELAY_MS(600) apart, ONLY while
 *  (terminal && durable-empty && liveNotesRef>0), and the loop is cancel-guarded on unmount. These
 *  tests pin: (a) it retries at most 5× (bounded — never hammers), (b) it STOPS the instant durable
 *  comes back non-empty, (c) unmount cancels the pending retry (no post-unmount setState/fetch).
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

import { MeetingSourceProvider } from "../useMeeting";

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
  it("retries the durable fetch at most DURABLE_REFETCH_ATTEMPTS(5) times, 600ms apart, then stops", async () => {
    terminalMeetingWithLiveNotes();
    fetchDurableTranscript.mockResolvedValue(EMPTY_DURABLE); // durable stays empty → keep retrying, bounded

    renderHook(() => null, { wrapper });

    // Initial load(0), then up to 5 scheduled retries. Flush each 600ms tick + its awaited fetch.
    for (let i = 0; i < 8; i++) {
      await vi.advanceTimersByTimeAsync(600);
    }
    // load(0) + attempts 1..5 = 6 fetches total; the 5th retry's callback does NOT schedule a 6th.
    expect(fetchDurableTranscript).toHaveBeenCalledTimes(6);
    expect(fetchDurableTranscript).toHaveBeenLastCalledWith("42");
  });

  it("stops retrying the instant the durable row comes back non-empty", async () => {
    terminalMeetingWithLiveNotes();
    fetchDurableTranscript
      .mockResolvedValueOnce(EMPTY_DURABLE)                                   // load(0): still empty → retry
      .mockResolvedValueOnce({ lines: [], notes: [{ id: "n1", text: "flushed" }] }) // retry 1: notes! → stop
      .mockResolvedValue(EMPTY_DURABLE);

    renderHook(() => null, { wrapper });
    for (let i = 0; i < 8; i++) await vi.advanceTimersByTimeAsync(600);

    expect(fetchDurableTranscript).toHaveBeenCalledTimes(2); // no further retries after notes arrived
  });

  it("cancels the pending retry on unmount (no post-unmount refetch)", async () => {
    terminalMeetingWithLiveNotes();
    fetchDurableTranscript.mockResolvedValue(EMPTY_DURABLE);

    const { unmount } = renderHook(() => null, { wrapper });
    await vi.advanceTimersByTimeAsync(0);   // resolve load(0), schedule retry 1
    expect(fetchDurableTranscript).toHaveBeenCalledTimes(1);
    unmount();                               // cancelled = true; clearTimeout(retry)
    await vi.advanceTimersByTimeAsync(600 * 6);
    expect(fetchDurableTranscript).toHaveBeenCalledTimes(1); // the cancelled retry never fired
  });
});
