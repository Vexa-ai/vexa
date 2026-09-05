/**
 * CSRC observation — what the RTP transport says about who is audible in the mix.
 *
 * CLEAN-ROOM NOTE: implemented from the WebRTC specification alone —
 * `RTCRtpReceiver.getContributingSources()` returning `RTCRtpContributingSource`
 * (W3C webrtc-pc, and its MDN reference pages). No other implementation of this idea was
 * opened, fetched or consulted while writing it.
 *
 * WHY. The mixed lane receives ONE audio stream carrying the whole meeting, so nothing in the
 * waveform says who is speaking; today that is inferred twice (a segmenter guesses turn
 * boundaries, a binder guesses a name per turn from flickery DOM hints). But the transport
 * already carries the answer: an RTP mixer stamps each packet of the mix with the CSRC list of
 * the sources it mixed, and the UA exposes the recent ones — each with a per-stream-stable
 * numeric id, the time its most recent contribution arrived, and its audio level.
 *
 * WHAT THIS IS AND IS NOT. This brick is a SENSOR. It observes and emits transitions; it decides
 * nothing, names nobody, and reaches no pipeline. Whether the transport becomes the turn spine is
 * a later decision, made on the evidence this produces.
 *
 * Deliberate properties, each against the obvious alternative:
 *   • **Transitions only.** A 100 ms poll over a steady speaker would otherwise emit ten
 *     identical records a second for an hour; the tape would be enormous and say nothing a pair
 *     of edges doesn't. Steady state emits NOTHING.
 *   • **Synthetic deactivation, not "absent from the poll".** The UA keeps listing a source for
 *     seconds after it stops (webrtc-pc: sources contributing "recently"), so presence in the
 *     latest poll is not liveness — the entry's own timestamp is. A source is active while it was
 *     seen within `inactiveMs`, and its deactivation is SYNTHESIZED when that lapses. A sensor
 *     that only reported what the UA listed would report everyone as speaking forever.
 *   • **Never throws into capture.** The whole poll body is wrapped, a receiver whose
 *     `getContributingSources` throws is counted and SKIPPED while the others keep working, and
 *     the first error emits one typed observation (one, not one per 100 ms — a broken receiver
 *     would otherwise bury the pod's logs at 10 lines/second).
 *   • **Platform-agnostic.** No platform vocabulary anywhere in this file: RTP mixing is a
 *     transport fact, identical on every client that mixes server-side.
 *
 * The page→Node record this feeds (`<session>.csrc.jsonl`, one JSON object per line) is:
 *   {"type":"csrc","t":1754899200123,"csrc":3735928559,"active":true,
 *    "audioLevel":0.42,"rtpTimestamp":123456789,"lane":"mixed"}
 * `t` is epoch ms — the SAME clock the audio frames and the naming hints carry, which is the only
 * reason a stored tape can line CSRC edges up against audio at all.
 *
 * Consumed by:
 *  - the bot: bundled into browser-utils.global.js; the capture bridge starts it in the mixed lane.
 *  - anything else that already installed the remote-audio hook (the extension) — it reuses that
 *    hook's peer-connection registry rather than patching RTCPeerConnection a second time.
 */
import { observedPeerConnections } from './webrtc-audio-hook.js';

/** One entry of `RTCRtpReceiver.getContributingSources()`, structurally (webrtc-pc
 *  RTCRtpContributingSource). Structural rather than the DOM lib's type so a test can hand in a
 *  receiver stub, and so a UA that omits an optional member is simply a UA with less to say. */
export interface ContributingSourceLike {
  /** The CSRC identifier — stable per contributing source for the life of the RTP stream. */
  source: number;
  /** When the most recent contribution from this source arrived. Domain-guarded — see toEpochMs. */
  timestamp?: number;
  /** 0..1 where present (the UA converts the RFC 6465 level for us). Carried, never interpreted. */
  audioLevel?: number;
  /** The RTP timestamp of that contribution — the media clock, kept for offline alignment. */
  rtpTimestamp?: number;
}

/** What this needs from an RTCRtpReceiver: its track's kind, and the CSRC accessor. */
export interface CsrcReceiverLike {
  track?: { kind?: string } | null;
  getContributingSources?: () => ContributingSourceLike[];
}

/** A transition — a source became audible, or stopped being audible. The ONLY thing emitted. */
export interface CsrcTransition {
  type: 'csrc';
  csrc: number;
  active: boolean;
  /** Epoch ms. On activation, when the transport saw the contribution; on deactivation, when the
   *  absence was concluded (the lapse is `inactiveMs` earlier by construction). */
  tMs: number;
  audioLevel?: number;
  rtpTimestamp?: number;
}

/** The one typed observation this sensor emits: it could not read the transport. Emitted ONCE per
 *  cause per poller — the count in `health()` is what says how often it kept happening. */
export interface CsrcPollErrorObservation {
  kind: 'csrc-poll-error';
  signal: 'csrc';
  /** Which read failed: enumerating receivers, or one receiver's CSRC accessor. */
  where: 'receivers' | 'contributing-sources' | 'poll';
  error: string;
  tMs: number;
}

export interface CsrcPollHealth {
  /** Polls attempted. Zero after a while ⇒ the timer never armed. */
  polls: number;
  /** Audio receivers seen on the last poll. Zero for a page with no peer connections — the
   *  ordinary case on any platform that does not mix server-side, and not an error. */
  receivers: number;
  /** Sources currently held active. */
  active: number;
  /** Transitions emitted since start. */
  transitions: number;
  /** Reads that threw. The meeting is unaffected by every one of them. */
  errors: number;
}

export interface CsrcPollOptions {
  /** Where transitions go. Called for edges only; a throw here is swallowed. */
  onTransition: (t: CsrcTransition) => void;
  /** Typed diagnostics (first error per cause). Never a transition, never a name. */
  onObservation?: (o: CsrcPollErrorObservation) => void;
  /** Receiver enumeration. Defaults to the remote-audio hook's peer-connection registry. */
  receivers?: () => CsrcReceiverLike[];
  /** Poll cadence in ms. Default 100 — the granularity the transport itself updates at. */
  pollMs?: number;
  /** How long a source stays active after its last observed contribution. Default 400 ms — a
   *  packet-gap window; a caller with a measured speech-pause window passes its own. A value that
   *  is not finite or is shorter than one poll is unusable (NaN makes both liveness comparisons
   *  false and flips every source on every poll) and is replaced by the default, said via `log`. */
  inactiveMs?: number;
  /** Epoch-ms clock (injectable for tests). */
  now?: () => number;
  /** performance.timeOrigin, for entries stamped on the performance clock. */
  timeOrigin?: () => number;
  log?: (m: string) => void;
}

export interface CsrcPoll {
  /** One tick. The interval drives it; tests drive it directly for a deterministic clock. */
  poll(): void;
  health(): CsrcPollHealth;
  /** Stop polling. Flushes a deactivation for every still-active source FIRST, so the meeting's
   *  last turn closes in the tape rather than dangling open — the same reason the caption reader
   *  flushes its in-flight entry at teardown. Idempotent; never throws. */
  destroy(): void;
}

/** The transport updates roughly per packet; 100 ms is one packet-train, not an arbitrary tick. */
export const CSRC_POLL_MS = 100;
/** Chosen to span a packet gap (jitter, DTX, a brief pause) without holding a finished turn open. */
export const CSRC_INACTIVE_MS = 400;
/** Beyond this, a timestamp is not the clock we think it is. Mirrors the bridge's hint guard. */
const MAX_CLOCK_SKEW_MS = 10 * 60 * 1000;

/**
 * Resolve a contributing source's `timestamp` to epoch ms.
 *
 * The spec's DOMHighResTimeStamp is relative to the document's time origin, so the epoch value is
 * `timeOrigin + timestamp`. But a stub — or a UA that reports the value already shifted — hands
 * over something that IS epoch, and blindly adding the origin to it would land the record ~55
 * years in the future, where it can never be lined up against audio. So each candidate is tested
 * for plausibility against the current epoch clock and the first plausible one wins; if neither
 * is, the observation is stamped NOW, which is at worst one poll late and always in the right
 * domain. Exported for its test: this is the only arithmetic in the file that can silently lie.
 */
export function toEpochMs(timestamp: number | undefined, nowMs: number, timeOriginMs: number): number {
  if (typeof timestamp !== 'number' || !Number.isFinite(timestamp)) return nowMs;
  if (Math.abs(timestamp - nowMs) <= MAX_CLOCK_SKEW_MS) return timestamp;          // already epoch
  const shifted = timeOriginMs + timestamp;
  if (Math.abs(shifted - nowMs) <= MAX_CLOCK_SKEW_MS) return shifted;              // performance clock
  return nowMs;
}

/** The default receiver source: every audio receiver of every peer connection the remote-audio
 *  hook already intercepts. Reusing that registry is the whole coupling — patching
 *  RTCPeerConnection a second time would race the first patch and mirror tracks twice. */
function hookedReceivers(): CsrcReceiverLike[] {
  const out: CsrcReceiverLike[] = [];
  for (const pc of observedPeerConnections()) {
    let list: unknown[] = [];
    try { list = pc.getReceivers?.() ?? []; } catch { continue; }   // one dead connection, not all
    for (const r of list) out.push(r as CsrcReceiverLike);
  }
  return out;
}

const isAudioReceiver = (r: CsrcReceiverLike): boolean =>
  !!r && r.track?.kind === 'audio' && typeof r.getContributingSources === 'function';

/**
 * Start observing the transport's contributing sources. Returns immediately; the first transition
 * arrives within `pollMs` of the first source becoming audible.
 *
 * A page with no peer connections yields zero receivers, zero transitions and zero errors — the
 * common case, and deliberately silent.
 */
export function createCsrcPoll(opts: CsrcPollOptions): CsrcPoll {
  const pollMs = opts.pollMs ?? CSRC_POLL_MS;
  const requestedInactiveMs = opts.inactiveMs;
  const inactiveMs = typeof requestedInactiveMs === 'number' && Number.isFinite(requestedInactiveMs)
    && requestedInactiveMs >= pollMs ? requestedInactiveMs : CSRC_INACTIVE_MS;
  const now = opts.now ?? (() => Date.now());
  const timeOrigin = opts.timeOrigin
    ?? (() => Number((globalThis as unknown as { performance?: { timeOrigin?: number } }).performance?.timeOrigin ?? 0));
  const receivers = opts.receivers ?? hookedReceivers;
  const log = opts.log ?? (() => { /* silent */ });
  if (requestedInactiveMs !== undefined && inactiveMs !== requestedInactiveMs) {
    log(`inactiveMs=${String(requestedInactiveMs)} is not a usable window (finite, >= the ${pollMs}ms poll) — using ${CSRC_INACTIVE_MS}ms`);
  }

  /** csrc → the last moment (epoch ms) it was observed contributing, plus its last carried levels. */
  const live = new Map<number, { lastSeen: number; audioLevel?: number; rtpTimestamp?: number }>();
  const reported = new Set<CsrcPollErrorObservation['where']>();
  let polls = 0;
  let receiverCount = 0;
  let transitions = 0;
  let errors = 0;
  let stopped = false;

  const emit = (t: CsrcTransition): void => {
    transitions++;
    // A consumer that throws must not stop the sensor, and must not reach the capture path.
    try { opts.onTransition(t); } catch { /* observation must never break capture */ }
  };

  const fault = (where: CsrcPollErrorObservation['where'], e: unknown): void => {
    errors++;
    if (reported.has(where)) return;      // ONE per cause: a broken receiver polls 10×/second
    reported.add(where);
    const observation: CsrcPollErrorObservation = {
      kind: 'csrc-poll-error', signal: 'csrc', where,
      error: String((e as { message?: string })?.message ?? e).slice(0, 200),
      tMs: now(),
    };
    try { opts.onObservation?.(observation); } catch { /* diagnostics never break capture */ }
  };

  const poll = (): void => {
    if (stopped) return;
    try {
      polls++;
      const nowMs = now();
      let list: CsrcReceiverLike[] = [];
      try { list = (receivers() || []).filter(isAudioReceiver); } catch (e) { fault('receivers', e); }
      receiverCount = list.length;

      for (const r of list) {
        let sources: ContributingSourceLike[] = [];
        try { sources = r.getContributingSources?.() || []; }
        catch (e) { fault('contributing-sources', e); continue; }   // this receiver only
        for (const s of sources) {
          const csrc = Number(s?.source);
          if (!Number.isFinite(csrc)) continue;
          const seenAt = toEpochMs(s?.timestamp, nowMs, timeOrigin());
          // The UA lists sources that contributed RECENTLY, not sources contributing NOW: an entry
          // whose own timestamp has gone stale is a carcass, and treating it as presence would
          // hold every speaker active for the rest of the meeting.
          if (nowMs - seenAt > inactiveMs) continue;
          const prev = live.get(csrc);
          live.set(csrc, {
            lastSeen: prev ? Math.max(prev.lastSeen, seenAt) : seenAt,
            audioLevel: typeof s?.audioLevel === 'number' ? s.audioLevel : prev?.audioLevel,
            rtpTimestamp: typeof s?.rtpTimestamp === 'number' ? s.rtpTimestamp : prev?.rtpTimestamp,
          });
          if (!prev) {
            emit({
              type: 'csrc', csrc, active: true, tMs: seenAt,
              ...(typeof s?.audioLevel === 'number' ? { audioLevel: s.audioLevel } : {}),
              ...(typeof s?.rtpTimestamp === 'number' ? { rtpTimestamp: s.rtpTimestamp } : {}),
            });
          }
        }
      }

      // Synthetic deactivation — the edge the transport itself never sends.
      for (const [csrc, state] of Array.from(live)) {
        if (nowMs - state.lastSeen < inactiveMs) continue;
        live.delete(csrc);
        emit({
          type: 'csrc', csrc, active: false, tMs: nowMs,
          ...(typeof state.audioLevel === 'number' ? { audioLevel: state.audioLevel } : {}),
          ...(typeof state.rtpTimestamp === 'number' ? { rtpTimestamp: state.rtpTimestamp } : {}),
        });
      }
    } catch (e) {
      // Belt-and-braces: nothing above should reach here, and if it does the capture path still
      // must not feel it.
      fault('poll', e);
    }
  };

  const timer = (globalThis as unknown as { setInterval: (fn: () => void, ms: number) => unknown })
    .setInterval(poll, pollMs);
  log(`csrc poll started (${pollMs}ms, inactive after ${inactiveMs}ms)`);

  return {
    poll,
    health: () => ({ polls, receivers: receiverCount, active: live.size, transitions, errors }),
    destroy(): void {
      if (stopped) return;
      stopped = true;
      try {
        (globalThis as unknown as { clearInterval: (t: unknown) => void }).clearInterval(timer);
      } catch { /* best-effort */ }
      const tMs = now();
      for (const [csrc, state] of Array.from(live)) {
        emit({
          type: 'csrc', csrc, active: false, tMs,
          ...(typeof state.audioLevel === 'number' ? { audioLevel: state.audioLevel } : {}),
          ...(typeof state.rtpTimestamp === 'number' ? { rtpTimestamp: state.rtpTimestamp } : {}),
        });
      }
      live.clear();
      log(`csrc poll stopped (${polls} polls, ${transitions} transitions, ${errors} errors)`);
    },
  };
}
