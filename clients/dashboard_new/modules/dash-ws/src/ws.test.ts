/**
 * L2 — drives the REAL createWsClient over the fake transport. Exit 1 on any failure.
 *
 *   1. on open → a `subscribe` frame is sent carrying the meeting; the connect url has ?api_key=…
 *   2. emit `meeting.status` status:"active"        → onStatus("active")
 *   3. emit `meeting.status` status:"needs_help"    → onStatus("needs_human_help")  (ADR-0023 normalize)
 *   4. emit a `transcript` bundle (confirmed:[{text:"hi"}], pending:[]) → onTranscript gets the confirmed seg
 *   5. emit `transcription_segment`                 → onTranscript({segments:[frame]})
 *   6. emit `chat_message`                          → onChat(frame)
 *   7. emit `error`                                 → onError(code)
 *   8. emit `subscribed` / `pong`                   → no-ops (no callback fires)
 */
import { createWsClient, normalizeStatus, type TranscriptUpdate } from "./index.js";
import { createFakeWsTransport } from "./fakes.js";
import type { ChatMessageFrame } from "@vexa/dash-contracts";

let failed = 0;
const ok = (label: string, cond: boolean, detail = "") => {
  console.log(`  ${cond ? "✓" : "✗"} ${label}${cond ? "" : detail ? " — " + detail : ""}`);
  if (!cond) failed++;
};

// ── wire the client over the fake transport ───────────────────────────────────────────────────────
const transport = createFakeWsTransport();

let lastStatus: string | null = null;
let lastTranscript: TranscriptUpdate | null = null;
let lastChat: ChatMessageFrame | null = null;
let lastError: string | null = null;
let statusCalls = 0;
let transcriptCalls = 0;
let chatCalls = 0;
let errorCalls = 0;

const client = createWsClient({
  transport,
  wsUrl: "wss://api.example/ws",
  authToken: "secret-token",
  meeting: { platform: "google_meet", native_id: "abc-123" },
  onStatus: (s) => {
    lastStatus = String(s);
    statusCalls++;
  },
  onTranscript: (u) => {
    lastTranscript = u;
    transcriptCalls++;
  },
  onChat: (c) => {
    lastChat = c;
    chatCalls++;
  },
  onError: (e) => {
    lastError = e;
    errorCalls++;
  },
});

client.start();

// ── 0) pure helper ────────────────────────────────────────────────────────────────────────────────
console.log("normalizeStatus:");
ok("needs_help → needs_human_help", normalizeStatus("needs_help") === "needs_human_help");
ok("active passes through", normalizeStatus("active") === "active");

// ── 1) connect url + open → subscribe frame ────────────────────────────────────────────────────────
console.log("open → subscribe:");
ok(
  "connect url carries ?api_key=secret-token",
  transport.connectedUrl === "wss://api.example/ws?api_key=secret-token",
  String(transport.connectedUrl),
);

transport.fireOpen();
ok("a frame was sent on open", transport.sent.length >= 1);
const subscribe = transport.sent.length ? JSON.parse(transport.sent[0]) : {};
ok("first sent frame is action:subscribe", subscribe.action === "subscribe");
ok(
  "subscribe carries the meeting ref",
  Array.isArray(subscribe.meetings) &&
    subscribe.meetings[0]?.platform === "google_meet" &&
    subscribe.meetings[0]?.native_id === "abc-123",
  JSON.stringify(subscribe.meetings),
);

// ── 2) golden meeting.status active ─────────────────────────────────────────────────────────────────
console.log("meeting.status:");
transport.emit({
  type: "meeting.status",
  meeting: { id: 1, platform: "google_meet", native_id: "abc-123" },
  payload: { status: "active" },
});
ok('status:"active" → onStatus("active")', lastStatus === "active", String(lastStatus));

// ── 3) meeting.status needs_help → normalized ───────────────────────────────────────────────────────
transport.emit({ type: "meeting.status", payload: { status: "needs_help" } });
ok(
  'status:"needs_help" → onStatus("needs_human_help")',
  lastStatus === "needs_human_help",
  String(lastStatus),
);

// ── 4) transcript bundle ────────────────────────────────────────────────────────────────────────────
console.log("transcript bundle:");
transport.emit({
  type: "transcript",
  speaker: "Alice",
  confirmed: [{ text: "hi", speaker: "Alice", absolute_start_time: "2026-06-22T00:00:00Z" }],
  pending: [],
});
ok("onTranscript fired", lastTranscript !== null);
const confirmed = lastTranscript?.confirmed ?? [];
ok("bundle confirmed[0].text == 'hi'", confirmed[0]?.text === "hi", JSON.stringify(confirmed));
ok("bundle speaker == 'Alice'", lastTranscript?.speaker === "Alice");
ok("bundle pending is empty", (lastTranscript?.pending ?? []).length === 0);

// ── 5) single transcription_segment frame ───────────────────────────────────────────────────────────
console.log("transcription_segment:");
transport.emit({ type: "transcription_segment", text: "second line", speaker: "Bob" });
const segs = lastTranscript?.segments ?? [];
ok("wrapped into segments:[frame]", segs.length === 1, JSON.stringify(lastTranscript));
ok("segment text preserved", segs[0]?.text === "second line");

// ── 6) chat_message ─────────────────────────────────────────────────────────────────────────────────
console.log("chat_message:");
transport.emit({ type: "chat_message", sender: "Bot", text: "welcome" });
ok("onChat fired with the frame", lastChat?.text === "welcome" && lastChat?.sender === "Bot");

// ── 7) error ────────────────────────────────────────────────────────────────────────────────────────
console.log("error:");
transport.emit({ type: "error", error: "missing_api_key" });
ok('onError("missing_api_key")', lastError === "missing_api_key", String(lastError));

// ── 8) subscribed / pong are no-ops ─────────────────────────────────────────────────────────────────
console.log("control frames are no-ops:");
const before = { statusCalls, transcriptCalls, chatCalls, errorCalls };
transport.emit({ type: "subscribed", meetings: [{ platform: "google_meet", native_id: "abc-123" }] });
transport.emit({ type: "pong" });
transport.emit({ type: "totally_unknown_frame" });
ok(
  "no callback fired for subscribed/pong/unknown",
  statusCalls === before.statusCalls &&
    transcriptCalls === before.transcriptCalls &&
    chatCalls === before.chatCalls &&
    errorCalls === before.errorCalls,
);

// ── 9) stop() closes the transport ──────────────────────────────────────────────────────────────────
console.log("stop:");
client.stop();
ok("stop() closes the transport", transport.closed === true);

// ── verdict ─────────────────────────────────────────────────────────────────────────────────────────
console.log(
  failed ? `\ndash-ws: ${failed} check(s) FAILED` : `\ndash-ws: all checks pass (unified ws.v1 dispatch)`,
);
process.exit(failed ? 1 : 0);
