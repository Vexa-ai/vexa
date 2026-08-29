import type { TransportEvent } from './turn-source.js';

export interface TeamsCsrcVirtualFrame {
  /** Stable RTP contributing-source id for the life of the Teams transport stream. */
  csrc: number;
  /** The original mixed PCM. During overlap the same immutable frame is routed to every active lane. */
  pcm: Float32Array;
  /** Epoch milliseconds in the same clock domain as the CSRC event. */
  tsMs: number;
  /** True when this frame came from the bounded onset lookback rather than the live active set. */
  backfilled: boolean;
}

export interface TeamsCsrcChannelizerOptions {
  /** Recover audio already captured before a delayed active:true observation. Default: 600 ms. */
  lookbackMs?: number;
  /**
   * A new CSRC nested inside an established active source must survive this long before it owns a
   * virtual lane. If it dies first while the established source continues, it is transport
   * flicker and receives no PCM. Its held onset is recovered from the ring on promotion.
   * Default: 1500 ms.
   */
  flickerHoldMs?: number;
  onFrame: (frame: TeamsCsrcVirtualFrame) => void;
}

export interface TeamsCsrcChannelizerHealth {
  active: number;
  tracks: number;
  transitions: number;
  inputFrames: number;
  emittedFrames: number;
  backfilledFrames: number;
  maxConcurrency: number;
  provisional: number;
  suppressedFlickers: number;
  promotedAfterHold: number;
}

interface MixedFrame {
  id: number;
  pcm: Float32Array;
  tsMs: number;
}

/**
 * Route one Teams server-mixed PCM stream into persistent virtual CSRC lanes.
 *
 * This class deliberately does not transcribe, name, segment, or infer speakers. It carries the
 * transport's active set onto the audio frames. A downstream per-channel pipeline supplies the
 * GMeet continuity/buffering semantics. During overlap the waveform cannot be separated: every
 * active lane receives the same immutable mixed frame and relies on its own prompt history.
 *
 * CSRC events and PCM cross independent page-to-Node callbacks. An activation can therefore arrive
 * after the corresponding PCM. A bounded ring recovers that onset once. `lastEmittedFrame` prevents
 * reactivation lookback from emitting any frame twice on one lane.
 */
export class TeamsCsrcChannelizer {
  private readonly lookbackMs: number;
  private readonly flickerHoldMs: number;
  private readonly onFrame: TeamsCsrcChannelizerOptions['onFrame'];
  private readonly active = new Set<number>();
  private readonly provisional = new Map<number, number>();
  private readonly seenTracks = new Set<number>();
  private readonly lastEmittedFrame = new Map<number, number>();
  private readonly ring: MixedFrame[] = [];
  private nextFrameId = 0;
  private transitions = 0;
  private inputFrames = 0;
  private emittedFrames = 0;
  private backfilledFrames = 0;
  private maxConcurrency = 0;
  private suppressedFlickers = 0;
  private promotedAfterHold = 0;

  constructor(opts: TeamsCsrcChannelizerOptions) {
    this.lookbackMs = Math.max(0, opts.lookbackMs ?? 600);
    this.flickerHoldMs = Math.max(0, opts.flickerHoldMs ?? 1500);
    this.onFrame = opts.onFrame;
  }

  feedAudio(pcm: Float32Array, tsMs: number): void {
    if (!Number.isFinite(tsMs)) return;
    // A PCM frame is the routing commit boundary. Transport callbacks that share a timestamp can
    // arrive in any order; waiting until this frame lets every queued true/false edge settle before
    // a lane can backfill audio. Commit before inserting the new frame so earlier ring frames are
    // marked backfilled while this frame remains the first live frame.
    if (this.active.size === 0 && this.provisional.size > 0) {
      for (const [csrc, sinceMs] of [...this.provisional]) this.promote(csrc, sinceMs, false);
    }
    this.promoteSurvivors(tsMs);
    const frame: MixedFrame = { id: this.nextFrameId++, pcm, tsMs };
    this.inputFrames++;
    this.ring.push(frame);
    // A provisional lane needs its entire hold interval plus the pre-event onset lookback.
    this.evictBefore(tsMs - this.lookbackMs - this.flickerHoldMs);
    for (const csrc of this.active) this.emit(csrc, frame, false);
  }

  recordTransportEvent(ev: TransportEvent): void {
    if (!Number.isFinite(ev.csrc) || !Number.isFinite(ev.tMs)) return;
    this.transitions++;
    this.seenTracks.add(ev.csrc);

    if (!ev.active) {
      if (this.provisional.delete(ev.csrc)) {
        // It never became an owner, and the source that caused it to be held is still active.
        // No buffered audio was emitted under this CSRC, so the false edge closes cleanly.
        this.suppressedFlickers++;
        return;
      }
      this.active.delete(ev.csrc);
      return;
    }

    if (this.active.has(ev.csrc) || this.provisional.has(ev.csrc)) return;
    // Never backfill from a transport callback. A same-timestamp false edge may still follow, and
    // emitting here would make callback order decide ownership. The next PCM frame commits it.
    this.provisional.set(ev.csrc, ev.tMs);
  }

  health(): TeamsCsrcChannelizerHealth {
    return {
      active: this.active.size,
      tracks: this.seenTracks.size,
      transitions: this.transitions,
      inputFrames: this.inputFrames,
      emittedFrames: this.emittedFrames,
      backfilledFrames: this.backfilledFrames,
      maxConcurrency: this.maxConcurrency,
      provisional: this.provisional.size,
      suppressedFlickers: this.suppressedFlickers,
      promotedAfterHold: this.promotedAfterHold,
    };
  }

  private promoteSurvivors(tsMs: number): void {
    for (const [csrc, sinceMs] of [...this.provisional]) {
      if (tsMs - sinceMs >= this.flickerHoldMs) {
        this.promote(csrc, sinceMs, this.flickerHoldMs > 0);
      }
    }
  }

  private promote(csrc: number, sinceMs: number, afterHold: boolean): void {
    this.provisional.delete(csrc);
    if (this.active.has(csrc)) return;
    this.active.add(csrc);
    if (afterHold) this.promotedAfterHold++;
    this.maxConcurrency = Math.max(this.maxConcurrency, this.active.size);
    const onsetFloor = sinceMs - this.lookbackMs;
    for (const frame of this.ring) {
      if (frame.tsMs >= onsetFloor) this.emit(csrc, frame, true);
    }
  }

  private emit(csrc: number, frame: MixedFrame, backfilled: boolean): void {
    if (frame.id <= (this.lastEmittedFrame.get(csrc) ?? -1)) return;
    this.lastEmittedFrame.set(csrc, frame.id);
    this.emittedFrames++;
    if (backfilled) this.backfilledFrames++;
    this.onFrame({ csrc, pcm: frame.pcm, tsMs: frame.tsMs, backfilled });
  }

  private evictBefore(floorMs: number): void {
    let drop = 0;
    while (drop < this.ring.length && this.ring[drop].tsMs < floorMs) drop++;
    if (drop > 0) this.ring.splice(0, drop);
  }
}
