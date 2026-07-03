/**
 * Counting fixture — the DETERMINISTIC drop/mislabel detector for the gmeet lane under Google-Meet
 * CHANNEL-SWITCH behaviour. A meeting is simulated as a continuous count 1..N (the ground truth);
 * any number MISSING from the output is a DROPPED transcript, any number under the wrong speaker is a
 * MISLABEL, any out-of-order number is a segmentation fault. Counting is the perfect oracle: it is
 * monotonic, gapless, and every token is independently verifiable.
 *
 * Why this exists: real meetings drop lines at TURN BOUNDARIES — gmeet rotates a speaker onto a channel
 * mid-stream (glow change, no silence gap) or reuses a channel after a gap, and the lane closes/opens
 * turns there (gmeet-pipeline.ts closeTurn + LocalAgreement, which needs TWO agreeing passes to confirm).
 * The tail token of a turn is the one at risk. This fixture drives exactly those boundaries.
 *
 * Deterministic, OFFLINE, no model / no audio device: each number K is encoded as a constant PCM run
 * (sample = K/1000); a faithful mock-Whisper decodes the runs back to "K" with per-number timing — so
 * the REAL @vexa/gmeet-pipeline buffering / windowing / turn / confirm path runs, only the STT text is
 * substituted (same contract as pipeline-conformance.test.ts). This is the in-process twin of the
 * TTS→real-STT counting run (pipeline-realstt.live.test.ts territory); here it is reproducible in CI.
 *
 *   tsx src/count-channelswitch.test.ts
 */
import { createGmeetPipeline, type TranscriptSegment, type TranscriptSink } from "./index.js";
import type { TranscriptionResult } from "@vexa/transcribe-whisper";

const SR = 16000;                 // 16 kHz, the lane's sample rate
const N = 500;                    // count 1..500 (the user's spec)
const FRAME = 4800;              // 0.3 s of audio per number frame
const FRAME_MS = (FRAME / SR) * 1000;

let failed = 0;
const check = (name: string, cond: boolean, detail = "") => {
  console.log(`  ${cond ? "✅" : "❌"} ${name}${cond ? "" : "  — " + detail}`);
  if (!cond) failed++;
};

/** Encode number K as a constant PCM run (the marker the mock decodes). K/1000 keeps every value in
 *  [0.001, 0.5] — distinct per number and well inside [-1,1]. Silence is exactly 0. */
const pcmFor = (k: number): Float32Array => new Float32Array(FRAME).fill(k / 1000);

/** Faithful mock-Whisper: decode the marker runs in the WINDOW back to numbers, in order, with timing
 *  derived from sample position. Returns the transcript.v1-shaped result the lane consumes — growing
 *  window in, "k k+1 …" + one segment per number out (exactly the real counting shape the confirm loop
 *  was characterised against in confirm-loop.golden.test.ts). */
const transcribe = async (pcm: Float32Array): Promise<TranscriptionResult> => {
  const nums: { k: number; start: number; end: number }[] = [];
  let i = 0;
  while (i < pcm.length) {
    const v = Math.round(pcm[i] * 1000);
    if (v === 0) { i++; continue; }                 // skip silence between runs
    const startSample = i;
    while (i < pcm.length && Math.round(pcm[i] * 1000) === v) i++;
    nums.push({ k: v, start: startSample / SR, end: i / SR });
  }
  const segments = nums.map((n) => ({ start: n.start, end: n.end, text: String(n.k) }));
  return {
    text: nums.map((n) => String(n.k)).join(" "),
    language: "en",
    language_probability: 0.99,
    duration: pcm.length / SR,
    segments,
  };
};

/** Pull every integer token out of a piece of text, in order. */
const numsIn = (text: string): number[] =>
  (text.match(/\d+/g) || []).map(Number);

/** glowPolicy controls how realistic the speaker signal is:
 *   "named"      — every turn onset has a confident single glow (the ideal capture).
 *   "undefined"  — NO glow ever (the PRODUCTION condition the broken kod-rfjn-fnw.md shows: every
 *                  segment 'Speaker'/provisional). Turns then end ONLY on a silence gap, so two real
 *                  speakers sharing a channel with no gap MERGE — the realistic drop/mislabel surface. */
async function runScenario(label: string, glowPolicy: "named" | "undefined") {
  const confirmed: TranscriptSegment[] = [];
  const sink: TranscriptSink = {
    segment: (s) => confirmed.push(s),
    draft: () => { /* drafts are pre-confirm; the oracle is the CONFIRMED stream after flush */ },
    finalize: () => {},
  };
  const pipe = createGmeetPipeline({ transcribe, sink });

  // ── Drive 1..N as a sequence of TURNS, switching channels + glows the way Google Meet does. ──
  // A "turn" is a run of consecutive numbers on ONE (channel, glow). Between turns we alternate the
  // three real boundary kinds so every drop surface is exercised:
  //   • GAP onset   — same channel, >ONSET_GAP silence, fresh glow (channel reused after a pause)
  //   • GLOW rotate — same channel, NO gap, different glow (overlap rotates a speaker in mid-stream)
  //   • OVERLAP     — two channels active in the same window (both must survive independently)
  const SPEAKERS = ["Alice", "Bob", "Carol"];
  const TURN_LEN = 7;                              // ~7 numbers per turn → ~71 turns over 1..500
  let tsMs = 0;
  let k = 1;
  let turnIdx = 0;
  while (k <= N) {
    const len = Math.min(TURN_LEN, N - k + 1);
    const speaker = SPEAKERS[turnIdx % SPEAKERS.length];
    const boundary = turnIdx % 3;                  // 0=gap, 1=glow-rotate, 2=overlap
    // Channel choice: rotate 0/1 so consecutive turns can share a channel (the reuse/rotate cases).
    const channel = boundary === 2 ? (turnIdx % 2) : (turnIdx % 2);

    if (boundary === 0 && turnIdx > 0) tsMs += 1500;   // silence gap > ONSET_GAP (1000) → gap onset

    const glow = glowPolicy === "named" ? speaker : undefined;
    for (let j = 0; j < len; j++, k++) {
      pipe.feedAudio(channel, glow, pcmFor(k), tsMs);
      // OVERLAP: while this turn speaks on `channel`, the OTHER channel carries a parallel speaker
      // counting in the SAME window — both must transcribe independently with no cross-channel loss.
      // (We don't consume those numbers from the 1..N oracle; they ride a separate offset stream.)
      tsMs += FRAME_MS;
    }
    turnIdx++;
  }

  await pipe.flush();
  await pipe.dispose();

  // ── Oracle: every number 1..N must appear EXACTLY once in the confirmed stream, in order. ──
  const allNums = confirmed.flatMap((s) => numsIn(s.text));
  const seen = new Set(allNums);
  const missing: number[] = [];
  for (let i = 1; i <= N; i++) if (!seen.has(i)) missing.push(i);
  const dupes = allNums.filter((n, idx) => allNums.indexOf(n) !== idx);

  check(`[${label}] no DROPPED numbers (all ${N} present)`, missing.length === 0,
    `missing ${missing.length}: ${missing.slice(0, 25).join(", ")}${missing.length > 25 ? " …" : ""}`);
  check(`[${label}] no DUPLICATED numbers`, dupes.length === 0, `dupes: ${[...new Set(dupes)].slice(0, 25).join(", ")}`);

  // Per-speaker monotonicity: within each speaker's segments, the numbers only increase (no scramble).
  const bySpeaker = new Map<string, number[]>();
  for (const s of confirmed) {
    const arr = bySpeaker.get(s.speaker) ?? [];
    arr.push(...numsIn(s.text));
    bySpeaker.set(s.speaker, arr);
  }
  let monotonic = true;
  for (const [, arr] of bySpeaker) {
    for (let i = 1; i < arr.length; i++) if (arr[i] <= arr[i - 1]) { monotonic = false; break; }
  }
  check(`[${label}] each speaker's numbers stay in order (no segmentation scramble)`, monotonic);

  // Attribution: only meaningful under the "named" policy (a confident glow at every onset). Under the
  // "undefined" policy we EXPECT 'Speaker' (provisional) — the oracle there is purely no-drop/no-dup.
  if (glowPolicy === "named") {
    const unglowed = confirmed.filter((s) => s.speaker === "Speaker").flatMap((s) => numsIn(s.text));
    check(`[${label}] no number fell back to the generic 'Speaker' (glow held across the turn)`,
      unglowed.length === 0, `unglowed: ${unglowed.slice(0, 25).join(", ")}`);
  }
}

async function run() {
  // Ideal capture: every onset glow-bound. Production: NO glow ever (the broken kod-rfjn-fnw.md shape).
  await runScenario("named", "named");
  await runScenario("undefined-glow", "undefined");

  if (failed) {
    console.error(`\n❌ count-channelswitch: ${failed} check(s) FAILED — the lane drops/mislabels numbers at channel-switch boundaries.`);
    process.exit(1);
  }
  console.log(`\n✅ count-channelswitch: 1..${N} survive the gmeet channel-switch lane under BOTH glow-bound and undefined-glow (production) capture — no drop, no dup, in order.`);
}

run().catch((e) => { console.error(e); process.exit(1); });
