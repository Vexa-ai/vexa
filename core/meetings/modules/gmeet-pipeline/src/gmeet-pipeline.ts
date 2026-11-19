/**
 * gmeet-pipeline — Google Meet CHANNEL-routed strategy (overlap-safe).
 *
 *   capture.v1 (audio frames: CHANNEL index + glow NAME bound at the source)
 *        ──►  transcript.v1 (named segments + live drafts)
 *
 * The combination of main's overlap engine + glow naming. Google Meet delivers
 * each active speaker on a SEPARATE channel, so audio is routed by CHANNEL: two
 * speakers talking at once land on separate per-channel streams and are
 * transcribed INDEPENDENTLY — no muddling, onsets intact. The glow names each
 * channel-TURN, bound at the turn's ONSET (a single tile lit, before any overlap)
 * and HELD through the overlap (where per-frame glow would be ambiguous → UNKNOWN).
 *
 * Each (channel, turn) is its OWN SpeakerStreamManager stream with a name FIXED at
 * onset (`ch-<n>:<turn>`). A turn ends on a silence gap OR a confident glow-name
 * CHANGE (overlap rotates a channel mid-stream with no gap), opening a fresh turn —
 * so a stream's name never relabels mid-flight — no async flush/relabel race.
 *
 * CONTRACT BOUNDARY: identity is CARRIED (the glow bound it at capture), never
 * derived. No diarizer, no post-hoc window-match.
 */
import { SpeakerStreamManager, type CoverageStats, type MissingRange, type ProgressStats, type SpeakerStreamManagerConfig, type SttPressureView } from './speaker-streams.js';
import { log } from './log.js';
import { isHallucination } from './hallucination-filter.js';
import type { TranscriptionResult } from '@vexa/transcribe-whisper';
import type { TranscriptSegment, TranscriptSink } from './contracts/transcript-v1.js';

export interface GmeetPipelineOptions {
  /** One Whisper round-trip (stt.v1). language is baked into the closure by the host. */
  transcribe: (pcm: Float32Array, prompt?: string, hint?: { language?: string }) => Promise<TranscriptionResult>;
  /** Pin a speaker's language once two consecutive answers of 3+ words agree on it (and the STT, when
   *  it reports one, is ≥0.7 sure): later requests for that speaker carry it as a hint instead of
   *  re-detecting per window — re-detection on a quiet stretch is where invented languages come from.
   *  The host decides whether a hint is honoured (auto mode). Default true. */
  pinLanguage?: boolean;
  /** Where transcript.v1 segments + drafts land (consumer = collector/rendering). */
  sink: TranscriptSink;
  /** Named attribution source. Null omits browser provenance for native identities. */
  speakerSource?: 'glow-bound' | null;
  /** Host-provided channel identity; defaults to the browser channel key. */
  channelKey?: (channel: number) => string;
  /** Label for a turn whose onset had no single confident glow. Default 'Speaker'. */
  unknownLabel?: string;
  /** SpeakerStreamManager tuning (turn gating / confirmation). */
  config?: SpeakerStreamManagerConfig;
  /** The STT client's back-pressure (its shared gate). Default: the `sttPressure` the transcribe
   *  function carries, if any. While it backs off the lane sends no duplicates or pipelined drafts. */
  sttPressure?: SttPressureView;
  /** Silence gap (ms) on a channel that ends its turn (→ re-bind on the next onset). Default 1000. */
  onsetGapMs?: number;
  /** Surface a transcribe FAILURE (P18: fail loud + attributable). The pipeline still
   *  degrades gracefully (empty turn) so it doesn't wedge, but it reports the fault here
   *  so the host can make it observable (a /ws health frame, telemetry, lifecycle) instead
   *  of a silent "no transcript". Receives the thrown value (e.g. a TranscriptionError). */
  onError?: (fault: unknown) => void;
  /** A closed turn is freed once it owes nothing (its last request answered); this bounds how long
   *  it may wait for a slow STT. Past it the turn is removed and its untranscribed audio is counted
   *  as dropped, loudly. Default 600000 (10 min — beyond the client's worst case of four timed-out
   *  attempts on a deferred final). */
  closeSettleCapMs?: number;
  /** Interval of the per-meeting `[COVERAGE]` log line (ms). 0 disables the periodic line; the
   *  line is always written once at dispose. Default 60000. */
  coverageLogIntervalMs?: number;
  /** How far transcription is behind capture, reported every `progressIntervalMs` while it changes
   *  and once at dispose — so a UI can tell a quiet speaker ("no words") from a slow STT ("behind"). */
  onProgress?: (p: TranscriptionProgress) => void;
  /** Interval of `onProgress` (ms). Default 5000. */
  progressIntervalMs?: number;
  /** The final pass: on dispose, every window still owed a transcript (one that failed and is waiting
   *  for its retry, or still in flight) is retried until it lands or this much time has passed (ms).
   *  Only then is the lane complete; what is left is logged as [AUDIO-UNTRANSCRIBED] with its time
   *  range and counted as dropped. Default 120000. */
  finalPassMs?: number;
  /** SHOW FAST, THEN RECONFIRM. When > 0, every window the lane confirms goes out at once as a DRAFT
   *  (completed:false), and the speaker's drafts are grouped: when the speaker pauses for
   *  `reconfirmIdleMs`, or the group spans this many seconds, the group's audio is transcribed again
   *  in one request, with the speaker's earlier text as context, and ONE confirmed segment replaces
   *  the drafts (under the first draft's id; the other draft ids are withdrawn). The request goes
   *  through the same transcribe (so the bot's shared STT gate counts it) and waits while that gate
   *  backs off; past `reconfirmHoldMs`, or if it fails or comes back empty, the drafts are confirmed
   *  as they are — reconfirmation may lag under overload, it never loses words. 0 disables (the
   *  lane's confirmed windows are published as they are). Default 0. */
  reconfirmSec?: number;
  /** Pause (ms) after a speaker's last draft that closes the group. Default 1500. */
  reconfirmIdleMs?: number;
  /** How long a group may wait for STT back-pressure to clear before its drafts are confirmed as
   *  they are (ms). Default 15000. */
  reconfirmHoldMs?: number;
}

/** The lane's progress report (snake_case: it goes on the wire as is). */
export interface TranscriptionProgress {
  /** Seconds of captured audio not yet transcribed or otherwise resolved. */
  backlog_s: number;
  /** Seconds since the last voiced frame; null before any. */
  silence_s: number | null;
  captured_s: number;
  transcribed_s: number;
}

export interface GmeetPipeline {
  /** One capture.v1 frame: CHANNEL index + glow NAME (undefined ⇒ no single glow now). */
  feedAudio(channel: number, glowName: string | undefined, pcm: Float32Array, tsMs: number): void;
  flush(): Promise<void>;
  dispose(): Promise<void>;
  /** Seconds of audio captured / submitted / transcribed / skipped / dropped so far. */
  coverage(): CoverageStats;
  /** Seconds behind / seconds silent, now. */
  progress(): ProgressStats;
  /** Captured audio that will never be transcribed, by capture time (empty when nothing was lost). */
  missingRanges(): MissingRange[];
  /** STOP BUDGET: the host will not wait any longer — every window still owed is recorded as
   *  missing now (the lane's own close accounting), so the terminal report can carry it. */
  abandon(): void;
}

/** One log line for a meeting's coverage: what was captured, what reached STT, where it ended up. */
export function formatCoverage(c: CoverageStats): string {
  const pct = c.capturedSec > 0 ? Math.round((c.transcribedSec / c.capturedSec) * 100) : 0;
  return `[COVERAGE] captured=${c.capturedSec}s submitted=${c.submittedSec}s transcribed=${c.transcribedSec}s ` +
    `(${pct}% of captured) no_speech=${c.noSpeechSec}s short=${c.shortSec}s dropped=${c.droppedSec}s`;
}

export function createGmeetPipeline(opts: GmeetPipelineOptions): GmeetPipeline {
  const UNKNOWN = opts.unknownLabel ?? 'Speaker';
  const ONSET_GAP = opts.onsetGapMs ?? 1000;
  const sttPressure = opts.sttPressure ?? (opts.transcribe as { sttPressure?: SttPressureView }).sttPressure;
  const mgr = new SpeakerStreamManager(sttPressure && !opts.config?.sttPressure ? { ...opts.config, sttPressure } : opts.config);
  const inflight = new Set<Promise<void>>();
  // Per channel: the CURRENT turn's stream key, bound name, last-audio time, turn counter.
  const chan = new Map<number, { key: string; name: string; lastMs: number; turn: number }>();

  // Emit the SEALED transcript.v1 shape (snake_case, segment_id + completed, source
  // in the contract's enum) — the pipeline IS the transcript.v1 producer, so its
  // output conforms to meetings/contracts/transcript.v1 (pinned by the replay golden).
  const segOf = (speakerName: string, key: string, text: string, startMs: number, endMs: number, completed: boolean, lang?: string): TranscriptSegment => {
    const named = speakerName !== UNKNOWN;
    return {
      segment_id: `${key}:${Math.round(startMs)}`,
      speaker: speakerName, speaker_key: key, text,
      start: startMs / 1000, end: endMs / 1000, completed, words: [],
      language: lang ?? null,
      ...(!named ? {source: 'provisional-cluster-id' as const}
        : opts.speakerSource === null ? {} : {source: opts.speakerSource ?? 'glow-bound' as const}),
      confidence: named ? 1 : 0,
    };
  };

  // The window's language off the stt.v1 result: the per-call detection in auto mode, or the
  // invocation-forced code (baked into the transcribe closure, echoed back by the service).
  // 'unknown' is the client's no-detection sentinel, not an ISO code — NULL stays honest.
  const langOf = (l: string | undefined): string | undefined =>
    l && l !== 'unknown' ? l : undefined;

  // Each request carries its number back, so several requests for one speaker (a stalled one and the
  // window sent again beside it) are told apart and only the newest valid answer is used.
  // Per channel (successive turns share it): the pinned language, or the streak towards one.
  const langs = new Map<string, { pinned?: string; last?: string; streak: number }>();
  const channelOf = (key: string) => key.replace(/#\d+$/, '').replace(/:\d+$/, '');
  const observeLanguage = (key: string, r: TranscriptionResult | undefined) => {
    if (opts.pinLanguage === false || !r) return;
    const lang = langOf(r.language);
    const words = (r.text || '').trim().split(/\s+/).filter(Boolean).length;
    if (!lang || words < 3 || (r.language_probability !== undefined && r.language_probability > 0 && r.language_probability < 0.7)) return;
    const st = langs.get(channelOf(key)) ?? { streak: 0 };
    if (st.pinned) return;
    st.streak = st.last === lang ? st.streak + 1 : 1;
    st.last = lang;
    if (st.streak >= 2) { st.pinned = lang; log(`[gmeet-pipeline] language pinned for ${channelOf(key)}: ${lang}`); }
    langs.set(channelOf(key), st);
  };
  mgr.onSegmentReady = (speakerId, _name, audio, requestId) => {
    const p = (async () => {
      try {
        const pinned = langs.get(channelOf(speakerId))?.pinned;
        const r = await opts.transcribe(audio, mgr.getLastConfirmedText(speakerId) || undefined, pinned ? { language: pinned } : undefined);
        observeLanguage(speakerId, r);
        const segs = r?.segments;
        mgr.handleTranscriptionResult(speakerId, (r?.text || '').trim(), segs?.[segs.length - 1]?.end, segs, langOf(r?.language), requestId);
      } catch (e) {
        opts.onError?.(e);                          // P18: report the fault, don't swallow it…
        mgr.handleTranscriptionFault(speakerId, e, requestId);  // …a draft keeps its audio; a final counts its loss
      }
    })();
    inflight.add(p);
    void p.finally(() => inflight.delete(p));
  };

  // ── show fast, then reconfirm ───────────────────────────────────────────────────────────────────
  const RECONFIRM_MS = Math.max(0, (opts.reconfirmSec ?? 0) * 1000);
  const RECONFIRM_IDLE = opts.reconfirmIdleMs ?? 1500;
  const RECONFIRM_HOLD = opts.reconfirmHoldMs ?? 15_000;
  type Frag = { name: string; text: string; startMs: number; endMs: number; lang?: string };
  type Group = { key: string; frags: Frag[]; lastAt: number; settled?: boolean };
  const groups = new Map<string, Group>();
  /** Per stream key: the frames it was fed (capture time), kept only as long as a group may need them. */
  const audio = new Map<string, { ts: number; pcm: Float32Array }[]>();
  const pending = new Set<Group>();          // groups whose reconfirmation is in flight
  const heldIds = new Set<string>();         // draft ids awaiting reconfirmation
  let reconfirmCalls = 0, reconfirmed = 0, promoted = 0;
  const publishFrag = (key: string, f: Frag, completed: boolean) =>
    completed ? opts.sink.segment(segOf(f.name, key, f.text, f.startMs, f.endMs, true, f.lang))
      : opts.sink.draft ? opts.sink.draft({ ...segOf(f.name, key, f.text, f.startMs, f.endMs, false, f.lang), confidence: 0 })
        : opts.sink.segment(segOf(f.name, key, f.text, f.startMs, f.endMs, true, f.lang));
  /** Confirm a group's drafts as they are (back-pressure past the hold, a failed or empty answer). */
  const promote = (g: Group, why: string) => {
    if (g.settled) return;
    g.settled = true; promoted++;
    for (const f of g.frags) publishFrag(g.key, f, true);
    log(`[gmeet-pipeline] [RECONFIRM-SKIPPED] ${g.key}: ${g.frags.length} draft(s) confirmed as they are (${why})`);
  };
  const sliceAudio = (key: string, fromMs: number, toMs: number): Float32Array => {
    const fr = (audio.get(key) ?? []).filter((x) => x.ts + (x.pcm.length / 16) > fromMs && x.ts < toMs);
    const out = new Float32Array(fr.reduce((n, x) => n + x.pcm.length, 0));
    let o = 0; for (const x of fr) { out.set(x.pcm, o); o += x.pcm.length; }
    return out;
  };
  const reconfirm = async (g: Group): Promise<void> => {
    const first = g.frags[0], last = g.frags[g.frags.length - 1];
    const pcm = sliceAudio(g.key, first.startMs - 300, last.endMs + 300);
    // Trim what no later group needs (groups of one key run in order).
    const kept = (audio.get(g.key) ?? []).filter((x) => x.ts + x.pcm.length / 16 > last.endMs - 300);
    audio.set(g.key, kept);
    if (pcm.length < 16000 * 0.5) { promote(g, 'audio too short to reconfirm'); return; }
    const holdUntil = Date.now() + RECONFIRM_HOLD;
    while (sttPressure?.backingOff() && Date.now() < holdUntil && !g.settled) await new Promise((r) => setTimeout(r, 250));
    if (g.settled) return;
    if (sttPressure?.backingOff()) { promote(g, 'stt backing off'); return; }
    try {
      reconfirmCalls++;
      const pinned = langs.get(channelOf(g.key))?.pinned;
      const prompt = lastReconfirmed.get(channelOf(g.key));
      const r = await opts.transcribe(pcm, prompt || undefined, pinned ? { language: pinned } : undefined);
      if (g.settled) return;                    // abandoned while in flight: the drafts were confirmed
      const text = (r?.text ?? '').replace(/\s+/g, ' ').trim();
      if (!text || isHallucination(text, { voiced: true })) { promote(g, 'empty answer'); return; }
      g.settled = true; reconfirmed++;
      const name = chanName.get(g.key) ?? first.name;
      opts.sink.segment(segOf(name, g.key, text, first.startMs, last.endMs, true, langOf(r?.language) ?? first.lang));
      for (const f of g.frags.slice(1)) opts.sink.draft?.({ ...segOf(name, g.key, '', f.startMs, f.endMs, false, f.lang), confidence: 0 });
      lastReconfirmed.set(channelOf(g.key), text.slice(-200));
    } catch (e) {
      opts.onError?.(e);
      promote(g, 'stt fault');
    }
  };
  const closeGroup = (key: string) => {
    const g = groups.get(key);
    if (!g) return;
    groups.delete(key);
    if (!g.frags.length) return;
    pending.add(g);
    const p = reconfirm(g).finally(() => {
      pending.delete(g);
      for (const f of g.frags) heldIds.delete(segOf(f.name, g.key, '', f.startMs, f.startMs, false).segment_id);
    });
    inflight.add(p);
    void p.finally(() => inflight.delete(p));
  };
  const lastReconfirmed = new Map<string, string>();
  const chanName = new Map<string, string>();
  const sweep = RECONFIRM_MS > 0 ? setInterval(() => {
    const now = Date.now();
    for (const [key, g] of [...groups]) if (now - g.lastAt >= RECONFIRM_IDLE && mgr.isSettled(key)) closeGroup(key);
  }, 250) : undefined;
  (sweep as { unref?: () => void } | undefined)?.unref?.();

  mgr.onSegmentConfirmed = (speakerId, speakerName, text, startMs, endMs, _segmentId, lang) => {
    if (!text.trim()) return;
    if (RECONFIRM_MS <= 0) { opts.sink.segment(segOf(speakerName, speakerId, text, startMs, endMs, true, lang)); return; }
    const f: Frag = { name: speakerName, text, startMs, endMs, lang };
    heldIds.add(segOf(speakerName, speakerId, '', startMs, startMs, false).segment_id);
    publishFrag(speakerId, f, false);           // shown at once, as a draft
    const g = groups.get(speakerId) ?? { key: speakerId, frags: [], lastAt: Date.now() };
    g.frags.push(f); g.lastAt = Date.now();
    groups.set(speakerId, g);
    if (endMs - g.frags[0].startMs >= RECONFIRM_MS) closeGroup(speakerId);
  };
  mgr.onSegmentPending = (speakerId, speakerName, text, startMs, lang) => {
    // A cleared lane draft whose id now carries a draft awaiting reconfirmation stays: the
    // reconfirmed segment (or the promotion) replaces it by id.
    if (RECONFIRM_MS > 0 && !text.trim() && heldIds.has(segOf(speakerName, speakerId, '', startMs, startMs, false).segment_id)) return;
    opts.sink.draft?.({ ...segOf(speakerName, speakerId, text, startMs, startMs, false, lang), confidence: 0 });
  };

  const settle = async () => { while (inflight.size) await Promise.all([...inflight]); };
  // Close a finished turn: final-submit + emit (name is fixed on the key, so the late
  // transcribe can't be mislabeled), then free the stream after it has long settled.
  // The stream is freed only once it owes nothing: with STT answering in 5–30 s, a fixed delay
  // removed turns whose deferred final was still in flight, and their audio never reached the
  // transcript. The cap bounds a request that never answers (its loss is counted, loudly).
  const SETTLE_CAP = opts.closeSettleCapMs ?? 600_000;
  const closeTurn = (key: string) => {
    void mgr.flushSpeaker(key, true).catch(() => { /* nothing owed */ });
    const closedAt = Date.now();
    const tick = () => {
      if (mgr.isSettled(key) || Date.now() - closedAt > SETTLE_CAP) {
        closeGroup(key);
        mgr.removeSpeaker(key);
        const t2 = setTimeout(() => { if (![...pending].some((g) => g.key === key) && !groups.has(key)) audio.delete(key); }, RECONFIRM_HOLD + 120_000);
        (t2 as { unref?: () => void }).unref?.();
        return;
      }
      const t = setTimeout(tick, 1000);
      (t as { unref?: () => void }).unref?.();   // don't keep the process alive for cleanup
    };
    const t = setTimeout(tick, 1000);
    (t as { unref?: () => void }).unref?.();
  };
  const coverageEvery = opts.coverageLogIntervalMs ?? 60_000;
  let lastCoverageLine = '';
  const logCoverage = (force = false) => {
    const line = formatCoverage(mgr.coverage());
    if (force || line !== lastCoverageLine) log(line);
    lastCoverageLine = line;
  };
  const coverageTimer = coverageEvery > 0 ? setInterval(() => logCoverage(), coverageEvery) : undefined;
  (coverageTimer as { unref?: () => void } | undefined)?.unref?.();

  let lastProgress = '';
  const reportProgress = (force = false) => {
    if (!opts.onProgress) return;
    const p = mgr.progress(), c = mgr.coverage();
    const report: TranscriptionProgress = {
      backlog_s: p.backlogSec, silence_s: p.silentForSec < 0 ? null : p.silentForSec,
      captured_s: c.capturedSec, transcribed_s: c.transcribedSec,
    };
    // Unchanged reports are not repeated (silence alone ticking is not news while nothing is behind).
    const key = `${report.backlog_s}|${report.captured_s}|${report.transcribed_s}|${report.backlog_s > 0 ? report.silence_s : ''}`;
    if (!force && key === lastProgress) return;
    lastProgress = key;
    try { opts.onProgress(report); } catch { /* a progress consumer must never disturb the lane */ }
  };
  const progressEvery = opts.progressIntervalMs ?? 5000;
  const progressTimer = opts.onProgress && progressEvery > 0 ? setInterval(() => reportProgress(), progressEvery) : undefined;
  (progressTimer as { unref?: () => void } | undefined)?.unref?.();

  return {
    feedAudio: (channel, glowName, pcm, tsMs) => {
      let st = chan.get(channel);
      // A channel-turn ends on EITHER a silence gap OR a confident glow-name CHANGE.
      // The glow-change case is the one overlap breaks: a channel rotates to a new
      // speaker mid-stream with NO silence gap, so the gap alone would hold the stale
      // name (the Галина→Зоя mislabel). A different single glow IS the rotation signal.
      const gapOnset = !!st && tsMs - st.lastMs > ONSET_GAP;
      const glowRotation = !!st && !!glowName && st.name !== UNKNOWN && glowName !== st.name;
      if (!st || gapOnset || glowRotation) {
        // TURN ONSET / rotation: close the previous turn and open a fresh stream named
        // from the glow lit RIGHT NOW (fixed for the turn — held through overlap below).
        if (st) closeTurn(st.key);
        const turn = (st ? st.turn : 0) + 1;
        const base = opts.channelKey?.(channel) ?? `ch-${channel}`;
        const key = `${base}:${turn}`;
        st = { key, name: glowName || UNKNOWN, lastMs: tsMs, turn };
        chan.set(channel, st);
        // A channel's successive turns form one publication-order group: a later turn's words never
        // publish ahead of an earlier turn's still being transcribed (bounded by orderHoldMs).
        mgr.addSpeaker(key, st.name, base);
      } else if (st.name === UNKNOWN && glowName) {
        // Onset was during overlap (no single glow) → opened UNKNOWN; a confident single
        // glow has now appeared early in the turn → adopt it (upgrade unknown→name only).
        st.name = glowName;
        mgr.updateSpeakerName(st.key, glowName);
      }
      st.lastMs = tsMs;
      if (RECONFIRM_MS > 0) {
        chanName.set(st.key, st.name);
        const fr = audio.get(st.key) ?? [];
        fr.push({ ts: tsMs, pcm: pcm.slice() });
        // Bound: never more than 3 groups' worth of audio per stream.
        let total = fr.reduce((n, x) => n + x.pcm.length, 0);
        while (total > 16 * RECONFIRM_MS * 3 && fr.length > 1) total -= fr.shift()!.pcm.length;
        audio.set(st.key, fr);
      }
      mgr.feedAudio(st.key, pcm, tsMs);
    },
    flush: async () => { for (const st of chan.values()) await mgr.flushSpeaker(st.key, true); await settle(); },
    coverage: () => mgr.coverage(),
    progress: () => mgr.progress(),
    missingRanges: () => mgr.missingRanges(),
    abandon: () => {
      mgr.removeAll();
      // The host will not wait: drafts still open or being reconfirmed are confirmed as they are.
      for (const key of [...groups.keys()]) { const g = groups.get(key)!; groups.delete(key); promote(g, 'stop budget'); }
      for (const g of [...pending]) promote(g, 'stop budget');
    },
    dispose: async () => {
      for (const st of chan.values()) await mgr.flushSpeaker(st.key, true);
      await settle();
      // The final pass: nothing captured is given up while there is time to transcribe it.
      const deadline = Date.now() + (opts.finalPassMs ?? 120_000);
      let pass = 0;
      while (mgr.hasUnfinished() && Date.now() < deadline) {
        if (pass++ === 0) log(`[gmeet-pipeline] final pass: audio still owed a transcript — retrying until it lands`);
        mgr.retryNow();
        await new Promise((r) => setTimeout(r, 100));
        await settle();
      }
      mgr.removeAll();
      // Every open group is reconfirmed now (or confirmed as it is) before the lane completes.
      for (const key of [...groups.keys()]) closeGroup(key);
      await settle();
      for (const g of [...pending]) promote(g, 'dispose');
      if (sweep) clearInterval(sweep);
      if (RECONFIRM_MS > 0) log(`[gmeet-pipeline] [RECONFIRM] reconfirmed=${reconfirmed} confirmed-as-drafted=${promoted} requests=${reconfirmCalls}`);
      if (coverageTimer) clearInterval(coverageTimer);
      if (progressTimer) clearInterval(progressTimer);
      logCoverage(true);
      reportProgress(true);
      await opts.sink.finalize();
    },
  };
}
