/**
 * tape-replay — replay a REAL captured-signal.v1 mixed-lane tape (audio frames + platform
 * "who is lit" hints) through the REAL ChunkedTranscriber, offline: injected segmenter, stub
 * STT, no model, no network, no server. Built for the first real Teams tape (meeting 24,
 * 2 speakers, 2013 records / 569 hints) to measure the two defects it exposed:
 *
 *   DUPLICATION  — how many DURABLE rows the collector would hold (upsert on
 *                  (meeting_id, segment_id)) versus how many distinct utterances exist. A
 *                  draft published under its own id becomes a second row for the same text.
 *   ATTRIBUTION  — which name each turn ends up under, scored against ground-truth windows
 *                  the humans in the meeting attested to.
 *
 * WHERE THE TURNS COME FROM is now the thing under test, so it is a flag:
 *
 *   --turns <psv>            replay the LIVE run's own windows (the original mode). Segmentation
 *                            is not re-derived, so the only variable is the naming/publish path —
 *                            which is what makes it the stable regression baseline.
 *   --turn-source pyannote   run the REAL streaming segmenter over the tape's audio. Slower, and
 *                            it re-introduces pyannote's variance, but it is the only honest way
 *                            to score the FALLBACK lane on a tape that has no recorded windows.
 *   --turn-source csrc       the transport spine, driven from the tape's csrc sidecar.
 *   --turn-source auto       what production runs: pyannote until the transport speaks.
 *
 * The csrc sidecar is found beside the tape by convention (`<tape>.csrc.jsonl`, or the sibling of
 * a `*.captured-signal.jsonl`) unless --csrc names it. Captions (`--captions`) feed the track
 * namer as a second naming source AND, with --cc-oracle, score the run against the platform's own
 * attribution.
 *
 *   npx tsx src/tape-replay.ts --tape <captured-signal.jsonl> --turns <turns.psv> [--gt <gt.json>]
 *   npx tsx src/tape-replay.ts --tape <captured-signal.jsonl> --turn-source csrc --stt-url …
 */
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { ChunkedTranscriber, VirtualClock, type BoundarySource } from './index.js';
import { TranscriptionClient } from '@vexa/transcribe-whisper';
import type { BoundaryEvent } from './pyannote-segmenter.js';
import type { TransportEvent } from './turn-source.js';

const arg = (name: string): string | undefined => {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 ? process.argv[i + 1] : undefined;
};
const TAPE = arg('tape');
const TURNS = arg('turns');
const TURN_SOURCE = (arg('turn-source') ?? (TURNS ? 'recorded' : 'pyannote')) as 'recorded' | 'pyannote' | 'csrc' | 'auto';
if (!TAPE || (TURN_SOURCE === 'recorded' && !TURNS)) {
  console.error('usage: tsx src/tape-replay.ts --tape <jsonl> [--turns <psv> | --turn-source pyannote|csrc|auto] [--csrc <jsonl>] [--captions <jsonl>] [--gt <json>] [--cc-oracle]');
  process.exit(2);
}

/** The csrc sidecar lives beside the tape by convention. Naming it explicitly is for the case where
 *  a fixture was assembled by hand; guessing a path that does not exist is reported, never silent —
 *  a csrc run against an absent sidecar would otherwise produce an empty transcript and look like a
 *  pipeline defect. */
function siblingSidecar(tape: string, part: string): string | null {
  const candidates = [
    tape.replace(/\.captured-signal\.jsonl$/, `.${part}.jsonl`),
    tape.replace(/\.jsonl$/, `.${part}.jsonl`),
    tape.replace(/[^/]+$/, `${part}.jsonl`),
  ];
  for (const c of candidates) if (c !== tape && existsSync(c)) return c;
  return null;
}

interface Frame { ts: number; pcm: string }
interface Hint { t: number; name: string; isEnd: boolean }

// ── Load the tape. A tape may carry MORE THAN ONE header (the capture restarted mid-session);
// every non-header line is a record, and records are either audio frames or hints. ──
function loadTape(path: string): { frames: Frame[]; hints: Hint[]; language: string | null } {
  const frames: Frame[] = [];
  const hints: Hint[] = [];
  let language: string | null = null;
  for (const line of readFileSync(path, 'utf8').split('\n')) {
    if (!line) continue;
    let r: any;
    try { r = JSON.parse(line); } catch { continue; }
    // The header records what the LIVE bot was configured with. A replay that hard-codes a
    // language transcribes a different meeting than the one on the tape: forcing 'en' onto a
    // Russian conversation makes Whisper TRANSLATE it, and every word-level comparison against the
    // original then measures the harness rather than the lane.
    if (r.type === 'captured_signal_header') { if (typeof r.language === 'string' && r.language) language = r.language; continue; }
    if (r.type === 'hint') { if (r.name) hints.push({ t: r.t, name: r.name, isEnd: !!r.isEnd }); continue; }
    if (typeof r.pcm === 'string' && typeof r.ts === 'number') frames.push({ ts: r.ts, pcm: r.pcm });
  }
  return { frames, hints, language };
}

/** The transport sidecar: one transition per line, plus its own mini-header (skipped). */
function loadCsrc(path: string): TransportEvent[] {
  const out: TransportEvent[] = [];
  for (const line of readFileSync(path, 'utf8').split('\n')) {
    if (!line) continue;
    let r: any;
    try { r = JSON.parse(line); } catch { continue; }
    if (r.type !== 'csrc') continue;
    if (typeof r.csrc !== 'number' || typeof r.t !== 'number') continue;
    out.push({ csrc: r.csrc, active: !!r.active, tMs: r.t, ...(typeof r.audioLevel === 'number' ? { audioLevel: r.audioLevel } : {}) });
  }
  return out;
}

/** Roster display names, from the observations sidecar the capture path writes. A name here says
 *  who was IN the meeting, never who was speaking. */
function loadRosterNames(path: string): { name: string; tMs: number }[] {
  const out: { name: string; tMs: number }[] = [];
  for (const line of readFileSync(path, 'utf8').split('\n')) {
    if (!line) continue;
    let r: any;
    try { r = JSON.parse(line); } catch { continue; }
    if (r.type !== 'observation') continue;
    const o = r.observation;
    if (!o || o.type !== 'roster-name' || typeof o.name !== 'string' || !o.name) continue;
    out.push({ name: o.name, tMs: typeof r.t === 'number' ? r.t : 0 });
  }
  return out;
}

/** The producer's roster-coverage statements, from the observations sidecar. */
function loadRosterCoverage(path: string): { named: number; participants: number; tMs: number }[] {
  const out: { named: number; participants: number; tMs: number }[] = [];
  for (const line of readFileSync(path, 'utf8').split('\n')) {
    if (!line) continue;
    let r: any;
    try { r = JSON.parse(line); } catch { continue; }
    if (r.type !== 'observation') continue;
    const o = r.observation;
    if (!o || o.type !== 'roster-coverage') continue;
    if (typeof o.named !== 'number' || typeof o.participants !== 'number') continue;
    out.push({ named: o.named, participants: o.participants, tMs: typeof r.t === 'number' ? r.t : 0 });
  }
  return out;
}

/** The caption sidecar: the platform's own ASR, with its own attribution. Used as name evidence,
 *  and (with --cc-oracle) as the thing we are scored against. */
function loadCaptions(path: string): { t: number; name: string; text: string; stable: boolean }[] {
  const out: { t: number; name: string; text: string; stable: boolean }[] = [];
  for (const line of readFileSync(path, 'utf8').split('\n')) {
    if (!line) continue;
    let r: any;
    try { r = JSON.parse(line); } catch { continue; }
    if (r.type !== 'caption' || !r.name || typeof r.t !== 'number') continue;
    out.push({ t: r.t, name: r.name, text: String(r.text ?? ''), stable: r.stable !== false });
  }
  return out;
}

function loadTurns(path: string): { turn: number; t0: number; t1: number }[] {
  return readFileSync(path, 'utf8').split('\n').filter(Boolean).map((l) => {
    const [turn, t0, t1] = l.split('|');
    return { turn: Number(turn), t0: Number(t0), t1: Number(t1) };
  }).filter((t) => Number.isFinite(t.t0) && Number.isFinite(t.t1)).sort((a, b) => a.t0 - b.t0);
}

/** A label that names NOBODY. Three shapes mean the same thing and all three must score as
 *  unattributed rather than as a wrong name: the provisional segmentation id, the display label the
 *  pyannote lane publishes it as, and the transport spine's honest "a distinct person we could not
 *  name". Counting "Speaker A" as a name would flatter every score in this file. */
const isUnattributed = (s: string): boolean =>
  s === 'Speaker' || /^seg_\d+$/.test(s) || /^Speaker [A-Z]+$/.test(s);

const pcmOf = (f: Frame): Float32Array => {
  const b = Buffer.from(f.pcm, 'base64');
  return new Float32Array(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength));
};

async function main(): Promise<void> {
  const { frames, hints, language: tapeLanguage } = loadTape(TAPE!);
  // `--language auto` (or a tape whose header says nothing) leaves it to Whisper, which is what a
  // bot with no configured language does live.
  const langArg = arg('language') ?? tapeLanguage ?? 'auto';
  const language = langArg === 'auto' ? undefined : langArg;
  const turns = TURNS ? loadTurns(TURNS) : [];
  const csrcPath = TURN_SOURCE === 'csrc' || TURN_SOURCE === 'auto'
    ? (arg('csrc') ?? siblingSidecar(TAPE!, 'csrc'))
    : arg('csrc') ?? null;
  const transport = csrcPath ? loadCsrc(csrcPath) : [];
  if ((TURN_SOURCE === 'csrc' || TURN_SOURCE === 'auto') && transport.length === 0) {
    // Loud, not silent: a csrc run with no sidecar produces an empty transcript, which reads
    // exactly like a broken pipeline.
    console.error(`WARNING: --turn-source ${TURN_SOURCE} but no transport transitions were found`
      + `${csrcPath ? ` in ${csrcPath}` : ' (no csrc sidecar beside the tape)'}`
      + (TURN_SOURCE === 'csrc' ? ' — this run will produce nothing.' : ' — this run will stay on pyannote.'));
  }
  // The roster sensor postdates the older fixtures, so a tape recorded before it carries no roster
  // observations at all. `--roster "A,B"` supplies them as a STAND-IN so the elimination rule can be
  // exercised against a real tape — it is stated in the run's own output, because a name that came
  // from the operator rather than from the meeting must never be mistaken for one the bot observed.
  const observationsPath = arg('observations') ?? siblingSidecar(TAPE!, 'observations');
  const rosterFromTape = observationsPath ? loadRosterNames(observationsPath) : [];
  const coverageFromTape = observationsPath ? loadRosterCoverage(observationsPath) : [];
  const rosterSupplied = (arg('roster') ?? '').split(',').map((x) => x.trim()).filter(Boolean);
  // `--no-captions` withholds the caption lane as NAME EVIDENCE while leaving it available as the
  // scoring oracle. That separation is the whole point of the flag: it answers "could this meeting
  // have been named without ever touching the participants' captions?" — a question about whether
  // the bot needs to change visible meeting state at all — without giving up the reference the
  // answer is checked against.
  const captionsPath = arg('captions') ?? siblingSidecar(TAPE!, 'captions');
  const captions = captionsPath ? loadCaptions(captionsPath) : [];
  const captionsAsEvidence = !process.argv.includes('--no-captions');
  console.log(`tape: ${frames.length} audio frame(s), ${hints.length} hint(s), ${transport.length} transport transition(s), ${captions.length} caption(s)`);
  if (rosterFromTape.length) console.log(`roster: ${rosterFromTape.length} observed sighting(s) from the tape`);
  if (coverageFromTape.length) console.log(`roster coverage: ${JSON.stringify(coverageFromTape.map((c) => `${c.named}/${c.participants}`))}`);
  if (rosterSupplied.length) console.log(`roster: ${JSON.stringify(rosterSupplied)} SUPPLIED BY --roster (stand-in for the roster sensor, which postdates this tape)`);
  console.log(`language: ${language ?? 'auto (whisper decides, as the live bot did)'}`);
  if (!captionsAsEvidence) console.log(`captions: WITHHELD as name evidence (--no-captions); still used as the scoring oracle`);
  console.log(`turn source: ${TURN_SOURCE}${TURN_SOURCE === 'recorded' ? ` (${turns.length} live window(s))` : ''}`);

  let emit!: (ev: BoundaryEvent) => void;
  /** Every publish call, in order — the naive "one row per publish" view. */
  const writes: { id: string; speaker: string; text: string; completed: boolean }[] = [];
  /** The collector's durable view: UPSERT on segment id (its unique key with meeting_id), and
   *  DELETE on a retract. */
  const durable = new Map<string, { speaker: string; text: string; startMs: number; endMs: number }>();
  const renames: { from: string; to: string; n: number }[] = [];
  const spineEvents: { from: string; to: string; reason: string; tMs: number }[] = [];
  let retracted = 0;

  // The bot's pending reconciliation, mirrored from services/bot/src/pipeline.ts. The mixed lane
  // republishes its whole surviving pending tail each pass, so an id that DROPS OUT of the block
  // has been superseded — the bot diffs the set and retracts the departed ids, which the collector
  // turns into a DELETE. The harness drives the transcriber directly, so it has to stand in for
  // that leg too, or it measures a durable store the product does not actually have.
  let pendingIds = new Set<string>();
  const reconcilePending = (pending: { segmentId: string }[]): void => {
    const next = new Set(pending.map((c) => c.segmentId));
    for (const id of pendingIds) if (!next.has(id)) { durable.delete(id); retracted++; }
    pendingIds = next;
  };

  // STT. --stt-url (+ VEXA_STT_TOKEN in the environment, never on the command line) drives the
  // REAL production TranscriptionClient — the same class services/bot/src/pipeline.ts builds in
  // createTranscribe, so the words below come from the same Whisper the live bot used. Without
  // --stt-url a deterministic stub stands in: one marker word per second of submitted audio,
  // growing by a STABLE PREFIX as each pass resubmits a longer span, or LocalAgreement would
  // never confirm and the draft/confirm transition under measurement would never happen.
  // The lane is asynchronous: a heartbeat enqueues work a promise chain drains later, so the clock
  // must let that chain settle before it moves again — otherwise the next heartbeat runs against a
  // state the live lane never passed through.
  // "Settle" must include the MODEL, not just the microtask queue: the segmenter's inference takes
  // tens of milliseconds, and a driver that advanced past it would hand the lane boundaries at
  // different audio positions than a live run did — a divergence manufactured by the harness and
  // then blamed on the lane. (Measured on m24: without this, 130 rows real vs 128 virtual.)
  let settleLane: (() => Promise<void>) | null = null;
  const drain = async (): Promise<void> => {
    for (let i = 0; i < 4; i++) await new Promise((r) => setImmediate(r));
    if (settleLane) { await settleLane(); await new Promise((r) => setImmediate(r)); }
  };
  const firstTs = Math.min(
    ...[frames.length ? frames[0].ts : Infinity, hints.length ? hints[0].t : Infinity,
        transport.length ? transport[0].tMs : Infinity].filter((x) => Number.isFinite(x)),
  );
  const clock = process.argv.includes('--virtual-time')
    ? new VirtualClock({ startMs: Number.isFinite(firstTs) ? firstTs : Date.now(), drain })
    : null;

  const sttUrl = arg('stt-url');
  const client = sttUrl
    ? new TranscriptionClient({ serviceUrl: sttUrl, apiToken: process.env.VEXA_STT_TOKEN, model: arg('stt-model') })
    : null;
  let sttCalls = 0, sttFailures = 0;
  const tc = await ChunkedTranscriber.create({
    ...(language ? { language } : {}),
    transcribe: async (pcm: Float32Array, prompt?: string) => {
      if (client) {
        sttCalls++;
        try {
          return await client.transcribe(pcm, language, prompt);
        } catch (e) {
          // A failed STT call is a REAL event, not a reason to substitute fake words — count it
          // and return nothing, exactly as an empty transcription would behave in the lane.
          sttFailures++;
          return { text: '', language: language ?? 'en', duration: pcm.length / 16000, segments: [] };
        }
      }
      // The stub's words carry the VOICE, derived from the window's amplitude. Emitting the same
      // marker text for every turn makes each turn's text a substring of the previous turn's
      // prompt, so the lane's own prompt-echo guard (correctly) drops it — and the replay then
      // measures the stub instead of the lane. Real speakers produce different words; so does this.
      // The token is derived from the window's OPENING SAMPLES, which is what makes it both stable
      // and unique: a turn resubmits from the same start as it grows (so the prefix is stable and
      // LocalAgreement can confirm), while a different turn starts at different audio (so its words
      // differ). Emitting the same marker for every turn made each turn's text a substring of the
      // previous turn's prompt, and the lane's prompt-echo guard correctly dropped it — five of
      // fourteen turns published nothing, which reads as a lane defect and is a stub artefact.
      let sum = 0;
      for (let i = 0; i < pcm.length; i++) sum += pcm[i] * pcm[i];
      const voice = Math.round(Math.sqrt(sum / Math.max(1, pcm.length)) * 100);
      let sig = 0;
      for (let i = 0; i < Math.min(64, pcm.length); i++) sig = (sig * 31 + Math.round(pcm[i] * 1000)) | 0;
      const tag = Math.abs(sig % 9973);
      const secs = Math.max(1, Math.floor(pcm.length / 16000));
      const text = Array.from({ length: secs }, (_, i) => `v${voice}s${tag}w${i}`).join(' ');
      return {
        text, language: language ?? 'en', language_probability: 0.99, duration: pcm.length / 16000,
        segments: [{ text, start: 0, end: pcm.length / 16000, no_speech_prob: 0.01, avg_logprob: -0.2, compression_ratio: 1.1 } as any],
      };
    },
    publish: (speaker, confirmed, pending) => {
      for (const c of confirmed) { writes.push({ id: c.segmentId, speaker, text: c.text, completed: true }); durable.set(c.segmentId, { speaker, text: c.text, startMs: c.startMs, endMs: c.endMs }); }
      reconcilePending(pending ?? []);
      for (const p of pending ?? []) { writes.push({ id: p.segmentId, speaker, text: p.text, completed: false }); durable.set(p.segmentId, { speaker, text: p.text, startMs: p.startMs, endMs: p.endMs }); }
    },
    publishPending: (speaker, segs) => {
      reconcilePending(segs);
      for (const p of segs) { writes.push({ id: p.segmentId, speaker, text: p.text, completed: false }); durable.set(p.segmentId, { speaker, text: p.text, startMs: p.startMs, endMs: p.endMs }); }
    },
    clearPending: () => { reconcilePending([]); },
    rename: (oldS, newS, segs) => {
      renames.push({ from: oldS, to: newS, n: segs.length });
      for (const s of segs) { writes.push({ id: s.segmentId, speaker: newS, text: s.text, completed: true }); durable.set(s.segmentId, { speaker: newS, text: s.text, startMs: s.startMs, endMs: s.endMs }); }
    },
    // The turn spine under test. 'recorded' injects the live run's own boundaries (the stable
    // baseline); every other mode runs the REAL streaming segmenter over the tape's audio, because
    // a fallback scored against pre-recorded windows is not the fallback being scored.
    ...(TURN_SOURCE === 'recorded'
      ? {
        makeSegmenter: async (onBoundary: (ev: BoundaryEvent) => void): Promise<BoundarySource> => {
          emit = onBoundary;
          return { appendFrame: async () => { /* boundaries are injected from --turns */ }, reset: () => { /* nothing */ } };
        },
      }
      : {}),
    ...(TURN_SOURCE === 'csrc' || TURN_SOURCE === 'auto' ? { turnSource: TURN_SOURCE as 'csrc' | 'auto' } : {}),
    // The grace is wall-time in production but AUDIO time here, and a tape's PCM is VAD-gated
    // (m29 carries 201.6s of audio across a 913s meeting) — so a default 20s of audio can be
    // minutes of meeting. Overridable for that reason.
    ...(arg('grace-ms') ? { turnSourceGraceMs: Number(arg('grace-ms')) } : {}),
    // The bot is a participant in every meeting it records; without its own name the namer cannot
    // tell it from a person in the roster. Defaults to the name our bots actually join under.
    selfName: arg('self-name') ?? 'Vexa',
    ...(clock ? { clock: { now: () => clock.now(), setHeartbeat: (fn, ms) => clock.setHeartbeat(fn, ms) } } : {}),
    onObservation: (o) => { spineEvents.push(o); console.log(`  [spine] ${o.from} → ${o.to} (${o.reason}) @${o.tMs}`); },
    log: (m: string) => { if (process.argv.includes('--verbose')) console.log(`  ${m}`); },
  });

  settleLane = () => tc.settled();
  // ── Drive the tape in TIMESTAMP order across all three streams: audio, hints, and the
  // recorded turn boundaries. Each carries its own capture ts, so no wall-clock pacing is
  // needed and the replay stays deterministic. ──
  type Ev = { t: number; run: () => void };
  const evs: Ev[] = [];
  for (const f of frames) evs.push({ t: f.ts, run: () => tc.feedAudio(pcmOf(f), f.ts) });
  for (const h of hints) evs.push({ t: h.t, run: () => tc.recordHint(h.name, 'dom-outline', h.t, h.isEnd) });
  if (TURN_SOURCE === 'recorded') {
    for (const tn of turns) {
      evs.push({ t: tn.t0, run: () => emit({ tMs: tn.t0, kind: 'silence→speaker', confidence: 0.9 }) });
      evs.push({ t: tn.t1, run: () => emit({ tMs: tn.t1, kind: 'speaker→silence', confidence: 0.9 }) });
    }
  }
  // The transport's edges ride the SAME ordered timeline as the audio and the hints — that shared
  // epoch clock is the only reason a stored transition can be lined up against a stored frame.
  for (const ev of transport) evs.push({ t: ev.tMs, run: () => tc.recordTransportEvent(ev) });
  // Captions are name evidence for a track. Only settled entries: an entry still being refined can
  // still change its author, and evidence that can change is not evidence.
  if (captionsAsEvidence) for (const c of captions) if (c.stable) evs.push({ t: c.t, run: () => tc.recordCaption(c.name, c.t) });
  // Roster names ride the timeline where the sensor observed them; a supplied stand-in is delivered
  // repeatedly from the first frame, which is what a scan every couple of seconds looks like.
  for (const r of rosterFromTape) evs.push({ t: r.tMs, run: () => tc.recordRosterName(r.name, r.tMs) });
  for (const c of coverageFromTape) evs.push({ t: c.tMs, run: () => tc.recordRosterCoverage(c.named, c.participants, c.tMs) });
  if (rosterSupplied.length && frames.length) {
    for (let i = 0; i < 3; i++) {
      const at = frames[Math.min(i * 5, frames.length - 1)].ts;
      for (const n of rosterSupplied) evs.push({ t: at, run: () => tc.recordRosterName(n, at) });
    }
  }
  evs.sort((a, b) => a.t - b.t);
  // PACING MATTERS. The transcriber's submit pump is driven by a WALL-CLOCK 1s tick, so a turn
  // is transcribed in several growing passes live but in ONE pass if the tape is fired instantly.
  // Attribution defects that live in the CADENCE — a name locked on the first tick, when the
  // commit window is a fraction of a second — are invisible without it. `--realtime` replays the
  // tape at its captured rate (1x; ~7 min for m24); without it only structure/duplication is
  // meaningful.
  // THREE TIERS, one harness.
  //   (default)        instant, no clock — event logic only. Naming iterations live here.
  //   --virtual-time   the tape's own clock drives the lane's heartbeat and TTL, so cadence-class
  //                    behaviour is reproduced EXACTLY, in seconds instead of minutes.
  //   --realtime       1x against the wall. The final confirmation pass, not an iteration tool.
  const realtime = process.argv.includes('--realtime');
  const virtualTime = process.argv.includes('--virtual-time');
  const t0 = evs[0]?.t ?? 0;
  const wall0 = Date.now();
  for (let i = 0; i < evs.length; i++) {
    if (realtime) {
      const due = wall0 + (evs[i].t - t0);
      const wait = due - Date.now();
      if (wait > 0) await new Promise((r) => setTimeout(r, wait));
    } else if (clock) {
      // Move the lane's clock to THIS event's own time, firing every heartbeat due on the way.
      await clock.advanceTo(evs[i].t);
    }
    evs[i].run();
    if (clock) await drain();
    else if (!realtime && i % 200 === 0) await new Promise((r) => setImmediate(r));   // let the async pump drain
  }
  if (clock) {
    // Let the tail play out: the lane's TTL finalize and its roll are heartbeat-driven, and a tape
    // that simply stops would leave the last turn's pending tail unexamined.
    await clock.advanceTo(evs.length ? evs[evs.length - 1].t + 30_000 : clock.now());
    console.log(`virtual time: ${clock.heartbeatsFired()} heartbeat(s) fired across the tape`);
  }
  await tc.dispose();

  // ── DUPLICATION ──
  const texts = new Set([...durable.values()].map((v) => v.text));
  console.log(`\nDUPLICATION`);
  console.log(`  publish calls (naive one-row-per-publish): ${writes.length}`);
  console.log(`  draft ids RETRACTED by the bot's pending reconcile : ${retracted}`);
  console.log(`  durable rows after UPSERT on segment_id  : ${durable.size}`);
  console.log(`  distinct texts among durable rows        : ${texts.size}`);
  const dupIds = [...durable.keys()].filter((id) => /:p\d+$/.test(id));
  console.log(`  durable rows carrying a DRAFT-ONLY id (:pN): ${dupIds.length}`);
  const perTurn = new Map<string, Set<string>>();
  for (const id of durable.keys()) {
    const m = /^turn:(\d+):(p?\d+)$/.exec(id);
    if (!m) continue;
    if (!perTurn.has(m[1])) perTurn.set(m[1], new Set());
    perTurn.get(m[1])!.add(m[2]);
  }
  const shadowed = [...perTurn.entries()].filter(([, s]) => [...s].some((x) => x.startsWith('p'))).length;
  console.log(`  turns holding BOTH a draft id and a confirmed id: ${shadowed}`);

  // ── ATTRIBUTION ──
  const byTime: { id: string; t: number; end: number; speaker: string; text: string }[] = [];
  for (const [id, v] of durable) byTime.push({ id, t: v.startMs, end: v.endMs, speaker: v.speaker, text: v.text });
  byTime.sort((a, b) => a.t - b.t);
  const tally = new Map<string, number>();
  for (const r of byTime) tally.set(r.speaker, (tally.get(r.speaker) ?? 0) + 1);
  console.log(`\nATTRIBUTION`);
  console.log(`  label distribution: ${JSON.stringify(Object.fromEntries(tally))}`);
  console.log(`  renames issued: ${renames.length}`);
  if (client) console.log(`  REAL STT: ${sttCalls} call(s), ${sttFailures} failure(s)`);
  const st = tc.stats();
  console.log(`  spine at end: ${st.spine}; contested turns: ${st.contested}; turns: ${st.turns}`);
  console.log(`  turn sources: ${JSON.stringify(st.sources)}`);
  console.log(`  tracks: ${st.tracks.named}/${st.tracks.tracks} named — ${JSON.stringify(st.tracks.how)}`);
  console.log(`  track evidence: ${JSON.stringify(st.tracks.evidence)}; roster: ${JSON.stringify(st.tracks.roster)}`);
  console.log(`  roster refused: ${JSON.stringify(st.tracks.rosterPolluted)}; coverage: ${JSON.stringify(st.tracks.rosterCoverage)}`);
  console.log(`  orphan spans: ${st.orphansContained}/${st.orphans} contained by a single track (the rest were genuinely ambiguous)`);
  if (spineEvents.length) console.log(`  spine changes: ${spineEvents.map((e) => `${e.from}→${e.to}(${e.reason})`).join(', ')}`);
  const namedRows = byTime.filter((r) => !isUnattributed(r.speaker)).length;
  console.log(`  rows carrying a HUMAN name: ${namedRows}/${byTime.length} (${byTime.length ? ((namedRows / byTime.length) * 100).toFixed(1) : '0'}%)`);

  // ── The readable transcript: one line per FINAL (post-retract) segment. ──
  const out = arg('out');
  if (out) {
    const t0Ms = byTime.length ? byTime[0].t : 0;
    const clock = (ms: number): string => {
      const s2 = Math.max(0, Math.round((ms - t0Ms) / 1000));
      return `${String(Math.floor(s2 / 60)).padStart(2, '0')}:${String(s2 % 60).padStart(2, '0')}`;
    };
    const lines = byTime.map((r) => `[${clock(r.t)}] ${/^seg_\d+$/.test(r.speaker) ? 'Speaker' : r.speaker}: ${r.text}`);
    writeFileSync(out, lines.join('\n') + '\n');
    console.log(`\nwrote ${lines.length} transcript line(s) → ${out}`);
  }

  // The same rows, machine-readable, so a durable store can be loaded from the SAME run that
  // produced the text render above. STT output varies between runs, so the two artefacts have
  // to come from one run or they describe different transcripts. `Speaker` is written out here
  // exactly as the render shows it — a provisional seg_N is not a display name.
  // Every publish call in order, drafts included — the ONLY way to ask what the retract leg
  // deleted. `durable` shows what survived; this shows what was ever shown to a viewer, so a
  // word that appeared as a draft and never came back as confirmed is measurable.
  const outWrites = arg('out-writes');
  if (outWrites) {
    writeFileSync(outWrites, writes.map((w) => JSON.stringify(w)).join('\n') + '\n');
    console.log(`wrote ${writes.length} publish call(s) → ${outWrites}`);
  }

  const outJson = arg('out-json');
  if (outJson) {
    const rows = byTime.map((r) => ({
      segment_id: r.id,
      start_epoch_s: r.t / 1000,
      end_epoch_s: r.end / 1000,
      speaker: /^seg_\d+$/.test(r.speaker) ? 'Speaker' : r.speaker,
      text: r.text,
    }));
    writeFileSync(outJson, JSON.stringify(rows, null, 2) + '\n');
    console.log(`wrote ${rows.length} JSON row(s) → ${outJson}`);
  }

  // ── THE CAPTION ORACLE ──
  // Teams' own ASR names every caption's author, so a Teams tape carries its own attribution
  // reference — no third party, no hand-labelling. It is NOT a neutral oracle (it over-represents
  // whoever it hears best) and it is not word-accurate, so it is reported as agreement against a
  // stated tolerance rather than as accuracy. Its lag against the audio is applied before matching.
  if (process.argv.includes('--cc-oracle')) {
    const lag = Number(arg('cc-lag-ms') ?? 1000);
    const tol = Number(arg('cc-tol-ms') ?? 1000);
    console.log(`\nCAPTION ORACLE (${captions.length} caption(s), lag ${lag}ms, tolerance ±${tol}ms)`);
    if (captions.length === 0) console.log('  no captions in this fixture — nothing to score against');
    else {
      // 1) WORDS. A bag-of-words recall against the oracle's words: a WER proxy, since true WER
      //    needs an alignment across two different segmentations that we cannot do reliably.
      const norm = (s: string): string[] => (s.toLowerCase().match(/[a-z0-9'Ѐ-ӿ]+/g) ?? []);
      const refCount = new Map<string, number>();
      // Captions REFINE in place: the same utterance appears repeatedly, growing. Counting every
      // entry would inflate the reference enormously, so each author's longest final form wins by
      // dropping any caption text that is a prefix of a later one from the same author.
      const finals: { name: string; text: string }[] = [];
      for (let i = 0; i < captions.length; i++) {
        const c = captions[i];
        const superseded = captions.slice(i + 1, i + 12)
          .some((n) => n.name === c.name && n.text.startsWith(c.text.slice(0, Math.max(0, c.text.length - 1))));
        if (!superseded) finals.push({ name: c.name, text: c.text });
      }
      for (const f of finals) for (const w of norm(f.text)) refCount.set(w, (refCount.get(w) ?? 0) + 1);
      const hypCount = new Map<string, number>();
      for (const r of byTime) for (const w of norm(r.text)) hypCount.set(w, (hypCount.get(w) ?? 0) + 1);
      let overlap = 0;
      for (const [w, n] of refCount) overlap += Math.min(n, hypCount.get(w) ?? 0);
      const refTotal = [...refCount.values()].reduce((a, b) => a + b, 0);
      const hypTotal = [...hypCount.values()].reduce((a, b) => a + b, 0);
      const recall = refTotal ? overlap / refTotal : 0;
      const precision = hypTotal ? overlap / hypTotal : 0;
      const f1 = recall + precision > 0 ? (2 * recall * precision) / (recall + precision) : 0;
      console.log(`  WORDS  oracle=${refTotal} ours=${hypTotal}  recall=${recall.toFixed(3)}  precision=${precision.toFixed(3)}  F1=${f1.toFixed(3)}`);

      // 2) ATTRIBUTION. Each of OUR rows is scored against the majority caption author whose
      //    lag-shifted timestamp lands inside the row's span. A row nobody captioned is not scored
      //    (it is not evidence of anything); a row we left unnamed is counted separately, because
      //    an honest gap and a wrong name are not the same failure and must never be added together.
      let agree = 0, disagree = 0, unnamed = 0, unscored = 0;
      const confusion = new Map<string, number>();
      for (const r of byTime) {
        const inSpan = captions.filter((c) => c.t - lag >= r.t - tol && c.t - lag <= r.end + tol);
        if (inSpan.length === 0) { unscored++; continue; }
        const votes = new Map<string, number>();
        for (const c of inSpan) votes.set(c.name, (votes.get(c.name) ?? 0) + 1);
        const truth = [...votes.entries()].sort((a, b) => b[1] - a[1])[0][0];
        if (isUnattributed(r.speaker)) { unnamed++; confusion.set(`unnamed←${truth}`, (confusion.get(`unnamed←${truth}`) ?? 0) + 1); continue; }
        if (r.speaker === truth) agree++;
        else { disagree++; confusion.set(`${r.speaker}←${truth}`, (confusion.get(`${r.speaker}←${truth}`) ?? 0) + 1); }
      }
      const scored = agree + disagree;
      console.log(`  ATTRIBUTION  scored=${scored + unnamed} (of ${byTime.length} rows; ${unscored} had no caption in span)`);
      console.log(`    named+agree      : ${agree}` + (scored ? ` (${((agree / scored) * 100).toFixed(1)}% of named)` : ''));
      console.log(`    named+WRONG      : ${disagree}`);
      console.log(`    unnamed          : ${unnamed}`);
      const delivered = scored + unnamed;
      console.log(`    correct name DELIVERED: ${agree}/${delivered}` + (delivered ? ` (${((agree / delivered) * 100).toFixed(1)}%)` : ''));
      if (confusion.size) console.log(`    breakdown (ours←oracle): ${JSON.stringify(Object.fromEntries([...confusion].sort((a, b) => b[1] - a[1])))}`);
    }
  }

  const gtPath = arg('gt');
  if (gtPath) {
    const gt = JSON.parse(readFileSync(gtPath, 'utf8')) as { label: string; t0: number; t1: number; who: string }[];
    let right = 0, wrong = 0, unknown = 0;
    for (const w of gt) {
      const inWin = byTime.filter((r) => r.t >= w.t0 && r.t < w.t1);
      const c = { right: 0, wrong: 0, unknown: 0 };
      for (const r of inWin) {
        if (r.speaker === w.who) c.right++;
        else if (isUnattributed(r.speaker)) c.unknown++;
        else c.wrong++;
      }
      right += c.right; wrong += c.wrong; unknown += c.unknown;
      console.log(`  ${w.label} (truth: ${w.who}) → right ${c.right} · wrong ${c.wrong} · unattributed ${c.unknown}`);
    }
    const scored = right + wrong;
    console.log(`  TOTAL over ground truth: right ${right} · wrong ${wrong} · unattributed ${unknown}` +
      (scored ? `  (accuracy over attributed: ${((right / scored) * 100).toFixed(1)}%)` : ''));
  }
}

void main().catch((e) => { console.error('tape-replay failed:', e); process.exit(1); });
