/**
 * meeting-state.test.ts — L2 brick test (tsx; exit code is the signal).
 *
 * Drives the REAL store over the REAL infra fakes:
 *   • bootstrap()  over createFakeApiClient (a golden transcript carrying absolute_start_time) →
 *                  segments seeded from REST + status taken from the response.
 *   • connectLive() builds the REAL @vexa/dash-ws createWsClient over createFakeWsTransport, wired to
 *                  the store's reducers — so the whole dispatch path (frame.type → callback → merge)
 *                  is exercised, not a stub.
 *   • meeting.status active                  → status "active".
 *   • a transcript bundle (confirmed+pending) → confirmed appended, pending shown (two-map merge).
 *   • a 2nd pending for the same speaker     → REPLACES the prior pending (no duplicate draft).
 *   • meeting.status completed               → status "completed" + connection "closed" (socket torn down).
 *
 * Cross-brick imports are relative to the sibling bricks' source (the bricks aren't symlinked into
 * node_modules); @vexa/dash-contracts resolves via the workspace symlink. tsx runs the TS directly.
 */
import { createMeetingState } from "./index.ts";
import { createWsClient } from "../../dash-ws/src/index.ts";
import { createFakeWsTransport, type FakeWsTransport } from "../../dash-ws/src/fakes.ts";
import { createFakeApiClient } from "../../dash-api-client/src/fakes.ts";
import type { TranscriptionResponse } from "@vexa/dash-contracts";

let failures = 0;
function check(label: string, cond: boolean): void {
  if (cond) {
    console.log(`  ✓ ${label}`);
  } else {
    console.error(`  ✗ ${label}`);
    failures++;
  }
}

// ── A golden transcript shaped like the REAL backend: REST segments carry absolute_start_time. ──────
const goldenTranscript: TranscriptionResponse = {
  id: 42,
  platform: "google_meet",
  native_meeting_id: "abc-defg-hij",
  constructed_meeting_url: "https://meet.google.com/abc-defg-hij",
  status: "active",
  start_time: "2026-06-20T09:00:00Z",
  end_time: null,
  segments: [
    {
      start: 1.0,
      end: 2.5,
      text: "This is Anna.",
      language: "en",
      speaker: "spk-Anna",
      completed: true,
      absolute_start_time: "2026-06-20T09:00:01Z",
      absolute_end_time: "2026-06-20T09:00:02Z",
      segment_id: "spk-Anna:1",
    },
    {
      start: 2.6,
      end: 5.2,
      text: "And this is Ben, thanks for joining.",
      language: "en",
      speaker: "spk-Ben",
      completed: true,
      absolute_start_time: "2026-06-20T09:00:03Z",
      absolute_end_time: "2026-06-20T09:00:05Z",
      segment_id: "spk-Ben:1",
    },
  ],
};

async function main(): Promise<void> {
  const apiClient = createFakeApiClient({ transcript: goldenTranscript });

  // The wsClientFactory wraps the REAL dash-ws client over a fake transport we keep a handle to,
  // so the test can drive the wire by hand.
  let transport: FakeWsTransport | null = null;
  const wsClientFactory = (wiring: Parameters<Parameters<typeof createMeetingState>[0]["wsClientFactory"]>[0]) => {
    transport = createFakeWsTransport();
    return createWsClient({
      transport,
      wsUrl: "wss://gateway.test/ws",
      authToken: "tok-123",
      meeting: wiring.meeting,
      onStatus: wiring.onStatus,
      onTranscript: wiring.onTranscript,
      onChat: wiring.onChat,
      onError: wiring.onError,
    });
  };

  const store = createMeetingState({
    apiClient,
    wsClientFactory,
    meeting: { platform: "google_meet", native_id: "abc-defg-hij", id: 42 },
  });

  // Track emitted snapshots to confirm subscribe() fires.
  let emitCount = 0;
  const unsub = store.subscribe(() => {
    emitCount++;
  });

  // ── bootstrap() ──────────────────────────────────────────────────────────────────────────────────
  console.log("bootstrap (REST seed):");
  check("connection starts idle", store.getState().connection === "idle");
  await store.bootstrap();
  const seeded = store.getState();
  check("two segments seeded from REST", seeded.segments.length === 2);
  check(
    "segments sorted by absolute_start_time (Anna before Ben)",
    seeded.segments[0]?.segment_id === "spk-Anna:1" && seeded.segments[1]?.segment_id === "spk-Ben:1",
  );
  check("status seeded from REST response", seeded.status === "active");
  check("subscribe() fired on bootstrap", emitCount >= 1);

  // ── connectLive() ────────────────────────────────────────────────────────────────────────────────
  console.log("connectLive:");
  store.connectLive();
  // DF2 — `connecting`, NOT `live`: "live" is earned by an observed frame, never asserted on start().
  check("connection is connecting (not yet live — DF2)", store.getState().connection === "connecting");
  check("transport connected with api_key", (transport!.connectedUrl || "").includes("api_key=tok-123"));
  // Open the socket → the client subscribes.
  transport!.fireOpen();
  check(
    "subscribe frame sent for the meeting",
    transport!.sent.some((s) => s.includes('"subscribe"') && s.includes("abc-defg-hij")),
  );

  // ── meeting.status active ────────────────────────────────────────────────────────────────────────
  console.log("status active:");
  transport!.emit({ type: "meeting.status", payload: { status: "active" } });
  check("status → active", store.getState().status === "active");
  // DF2 — the FIRST observed frame flips connecting → live (evidence-based).
  check("connection → live on the first frame", store.getState().connection === "live");

  // ── transcript bundle (confirmed + pending) ──────────────────────────────────────────────────────
  console.log("transcript bundle (confirmed + pending):");
  transport!.emit({
    type: "transcript",
    speaker: "spk-Carol",
    confirmed: [
      {
        text: "Let's get started.",
        speaker: "spk-Carol",
        segment_id: "spk-Carol:1",
        absolute_start_time: "2026-06-20T09:00:06Z",
        absolute_end_time: "2026-06-20T09:00:07Z",
        completed: true,
      },
    ],
    pending: [
      {
        text: "First item on the",
        speaker: "spk-Carol",
        segment_id: "spk-Carol:pending",
        absolute_start_time: "2026-06-20T09:00:08Z",
        absolute_end_time: "2026-06-20T09:00:08Z",
        completed: false,
      },
    ],
  });
  let segs = store.getState().segments;
  check("confirmed appended (now 3 confirmed + 1 pending = 4)", segs.length === 4);
  check(
    "Carol's confirmed segment present",
    segs.some((s) => s.segment_id === "spk-Carol:1" && s.text === "Let's get started."),
  );
  check(
    "Carol's pending draft present",
    segs.some((s) => s.segment_id === "spk-Carol:pending" && s.text === "First item on the"),
  );

  // ── a second pending for the SAME speaker → replaces (not duplicates) ─────────────────────────────
  console.log("second pending for same speaker (replace):");
  transport!.emit({
    type: "transcript",
    speaker: "spk-Carol",
    confirmed: [],
    pending: [
      {
        text: "First item on the agenda is the roadmap.",
        speaker: "spk-Carol",
        segment_id: "spk-Carol:pending",
        absolute_start_time: "2026-06-20T09:00:08Z",
        absolute_end_time: "2026-06-20T09:00:09Z",
        completed: false,
      },
    ],
  });
  segs = store.getState().segments;
  const carolPendings = segs.filter((s) => !s.completed && s.speaker === "spk-Carol");
  check("still exactly one pending draft for Carol (replaced, not duplicated)", carolPendings.length === 1);
  check(
    "pending draft text is the NEW (longer) text",
    carolPendings[0]?.text === "First item on the agenda is the roadmap.",
  );
  check("total segments still 4 (3 confirmed + 1 pending)", segs.length === 4);

  // ── REGRESSION: a LIVE bot segment carries an EPOCH `start` + NULL absolute_start_time ─────────────
  // The renderer requires absolute_start_time; the WS path must DERIVE it from the epoch `start`, else
  // every live segment is filtered at ingest — frames arrive but nothing renders live (REST-only).
  console.log("live WS segment (epoch start, null absolute_start_time):");
  const beforeLive = store.getState().segments.length;
  transport!.emit({
    type: "transcript",
    speaker: "Dmitriy Grankin",
    confirmed: [
      {
        segment_id: "ch-0:36:1782162716032",
        start: 1782162716.032,
        end: 1782162718.336,
        text: "live segment with epoch start and no absolute_start_time",
        speaker: "Dmitriy Grankin",
        completed: true,
        absolute_start_time: null,
        absolute_end_time: null,
      },
    ],
    pending: [],
  });
  const liveSegs = store.getState().segments;
  check(
    "live WS segment INGESTED despite null absolute_start_time (derived from epoch start)",
    liveSegs.length === beforeLive + 1,
  );
  const liveSeg = liveSegs.find((s) => s.segment_id === "ch-0:36:1782162716032");
  check(
    "live segment rendered with its text",
    liveSeg?.text === "live segment with epoch start and no absolute_start_time",
  );
  check(
    "absolute_start_time derived from epoch start (ISO, year 2026)",
    String(liveSeg?.absolute_start_time || "").startsWith("2026-"),
  );

  // ── meeting.status completed → status completed + connection closed ───────────────────────────────
  console.log("status completed (terminal):");
  transport!.emit({ type: "meeting.status", payload: { status: "completed" } });
  check("status → completed", store.getState().status === "completed");
  check("connection → closed", store.getState().connection === "closed");
  check("transport closed on terminal status", transport!.closed === true);

  unsub();
  // After unsubscribe, no further emits reach our counter — sanity that unsubscribe works.
  const beforeUnsubEmit = emitCount;
  store.connectLive(); // no-op (already closed) — must NOT emit to the unsubscribed cb
  check("unsubscribe stops further callbacks", emitCount === beforeUnsubEmit);
  check("connectLive() after close is a no-op (stays closed)", store.getState().connection === "closed");

  console.log("");
  if (failures > 0) {
    console.error(`dash-meeting-state: ${failures} check(s) FAILED`);
    process.exit(1);
  }
  console.log("dash-meeting-state: all checks pass (FSM + two-map live transcript assembly)");
}

main().catch((err) => {
  console.error("dash-meeting-state: test threw:", err);
  process.exit(1);
});
