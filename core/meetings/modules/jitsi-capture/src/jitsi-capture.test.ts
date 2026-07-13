/**
 * jitsi-capture L2 — the PURE state logic, no browser. Drives the real
 * createJitsiSpeakers / createJitsiChat against a FAKE `APP.store` (the redux
 * primary source), and pins the send path + the exported selector arrays (the
 * DOM-fallback surface). The DOM observers themselves are fallback-only and
 * live-validated. Run: npm test  or  npx tsx src/jitsi-capture.test.ts
 */
import {
  createJitsiSpeakers,
  createJitsiChat,
  sendJitsiChatMessage,
  jitsiDominantTileSelectors,
  jitsiTileNameSelectors,
  jitsiChatContainerSelectors,
  jitsiChatMessageSelectors,
  jitsiChatSenderSelectors,
  jitsiChatTextSelectors,
} from "./index.js";

let failed = 0;
const check = (name: string, cond: boolean, detail = "") => {
  console.log(`  ${cond ? "✅" : "❌"} ${name}${cond ? "" : "  — " + detail}`);
  if (!cond) failed++;
};
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// ── Fake APP.store — the shape createJitsiSpeakers/Chat defensively read ──────
type Participant = { id: string; name: string };
const fakeState: any = {
  "features/base/participants": {
    local: { id: "self1", name: "Vexa" } as Participant,
    remote: new Map<string, Participant>([
      ["p1", { id: "p1", name: "Alice" }],
      ["p2", { id: "p2", name: "Bob" }],
    ]),
    dominantSpeaker: undefined as string | undefined,
  },
  "features/chat": { messages: [] as any[] },
};
(globalThis as any).APP = {
  store: { getState: () => fakeState },
  conference: { _room: { sendTextMessage: (t: string) => sent.push(t) } },
};
const sent: string[] = [];

async function main() {
  // ── speakers: dominant transitions → debut/end hint pairs ────────────────────
  const events: Array<{ name: string; isEnd: boolean }> = [];
  const speakers = createJitsiSpeakers({
    selfName: "Vexa",
    pollMs: 10,
    heartbeatMs: 30,
    onSpeaking: (name, _id, isEnd) => events.push({ name, isEnd }),
  });

  fakeState["features/base/participants"].dominantSpeaker = "p1";
  await sleep(40);
  check("speaker start emitted for Alice", events.some((e) => e.name === "Alice" && !e.isEnd), JSON.stringify(events));

  // A STILL-dominant speaker keeps being re-asserted (the binder's heartbeat contract):
  // an open hint turn decays after a grace, so an unchanged speaker must re-emit.
  const assertsBefore = events.filter((e) => e.name === "Alice" && !e.isEnd).length;
  await sleep(100);
  const assertsAfter = events.filter((e) => e.name === "Alice" && !e.isEnd).length;
  check("still-dominant speaker heartbeats", assertsAfter > assertsBefore, `${assertsBefore} → ${assertsAfter}`);

  fakeState["features/base/participants"].dominantSpeaker = "p2";
  await sleep(40);
  check("Alice ended when Bob took over", events.some((e) => e.name === "Alice" && e.isEnd), JSON.stringify(events));
  check("Bob start emitted", events.some((e) => e.name === "Bob" && !e.isEnd), JSON.stringify(events));

  // The bot's own dominant-speaker state is never reported (and ends the previous).
  events.length = 0;
  fakeState["features/base/participants"].dominantSpeaker = "self1";
  await sleep(40);
  check("self (bot) never reported as a speaker", !events.some((e) => e.name === "Vexa"), JSON.stringify(events));
  check("previous speaker ended on self takeover", events.some((e) => e.name === "Bob" && e.isEnd), JSON.stringify(events));

  check("speakers mode = redux", speakers.getState().mode === "redux", speakers.getState().mode ?? "null");
  speakers.destroy();

  // ── chat: history primes silently; new messages emit once ────────────────────
  fakeState["features/chat"].messages = [
    { id: "m1", displayName: "Alice", message: "hello from before the bot joined", messageType: "remote", timestamp: 1 },
  ];
  const got: Array<{ sender: string; text: string }> = [];
  const chat = createJitsiChat({ pollMs: 10, onMessage: (m) => got.push(m) });
  await sleep(40);
  check("pre-join history is primed, not emitted", got.length === 0, JSON.stringify(got));

  fakeState["features/chat"].messages = [
    ...fakeState["features/chat"].messages,
    { id: "m2", displayName: "Bob", message: "agenda is in the doc", messageType: "remote", timestamp: 2 },
    { id: "m3", displayName: "", message: "anonymous ping", messageType: "remote", timestamp: 3 },
    { id: "m4", displayName: "Eve", message: "boom", messageType: "error", timestamp: 4 },
    { id: "m5", displayName: "Vexa", message: "sent by the bot itself", messageType: "local", timestamp: 5 },
  ];
  await sleep(40);
  check("new message emitted", got.some((m) => m.sender === "Bob" && m.text === "agenda is in the doc"), JSON.stringify(got));
  check("missing displayName → Unknown", got.some((m) => m.sender === "Unknown" && m.text === "anonymous ping"), JSON.stringify(got));
  check("error-type messages filtered", !got.some((m) => m.text === "boom"), JSON.stringify(got));
  check("the bot's own (local) messages never echo back", !got.some((m) => m.text === "sent by the bot itself"), JSON.stringify(got));

  const before = got.length;
  await sleep(30);
  check("no duplicate emissions on re-poll", got.length === before, `${got.length} vs ${before}`);

  // ── store replacement (reconnect / p2p↔JVB move / history cap): the array shrinks to a
  // retained tail — already-delivered messages must NOT re-emit, and new ones still do. ──
  fakeState["features/chat"].messages = [
    { id: "m2", displayName: "Bob", message: "agenda is in the doc", messageType: "remote", timestamp: 2 },
    { id: "m5", displayName: "Vexa", message: "sent by the bot itself", messageType: "local", timestamp: 5 },
  ];
  await sleep(30);
  check("store replacement re-emits nothing", got.length === before, `${got.length} vs ${before}`);
  fakeState["features/chat"].messages = [
    ...fakeState["features/chat"].messages,
    { id: "m6", displayName: "Carol", message: "fresh after the resync", messageType: "remote", timestamp: 6 },
  ];
  await sleep(30);
  check(
    "post-resync message emits once",
    got.filter((m) => m.text === "fresh after the resync").length === 1,
    JSON.stringify(got),
  );

  check("chat mode = redux", chat.getState().mode === "redux", chat.getState().mode ?? "null");
  chat.destroy();

  // ── send path ────────────────────────────────────────────────────────────────
  check("sendJitsiChatMessage uses the conference API", sendJitsiChatMessage("hi room") === true && sent[0] === "hi room", JSON.stringify(sent));
  delete (globalThis as any).APP.conference;
  check("send returns false when the API is absent", sendJitsiChatMessage("nope") === false);

  // ── DOM-fallback selector surface is exported + non-empty ────────────────────
  for (const [name, arr] of Object.entries({
    jitsiDominantTileSelectors, jitsiTileNameSelectors,
    jitsiChatContainerSelectors, jitsiChatMessageSelectors,
    jitsiChatSenderSelectors, jitsiChatTextSelectors,
  })) {
    check(`${name} exported non-empty`, Array.isArray(arr) && arr.length > 0);
  }

  if (failed) { console.error(`\n❌ jitsi-capture (L2): ${failed} check(s) FAILED.`); process.exit(1); }
  console.log("\n✅ jitsi-capture (L2): speakers + chat drive the fake APP.store correctly; send path + selector surface pinned.");
}

void main();
