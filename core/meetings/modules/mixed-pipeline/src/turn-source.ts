/**
 * TurnSource — WHERE a turn's edges come from.
 *
 * The mixed lane receives ONE audio stream carrying the whole meeting, so until now it INFERRED
 * the turn structure from the waveform: pyannote-segmentation guesses where a speaker starts and
 * stops. That inference has a measured cost — on clean single-speaker audio the streaming
 * segmenter declared 131.5s of 270s as speech and 79 of an oracle's 251 words fell outside every
 * declared region, i.e. the guess DELETED speech (the gap-reclaim finding).
 *
 * But an RTP mixer already labels the mix it produces: every packet carries the CSRC list of the
 * sources that went into it, and the UA exposes those with a per-stream-stable id. That is a turn
 * edge OBSERVED rather than inferred, and it comes with something pyannote can never supply — a
 * stable identity per speaker for the life of the meeting.
 *
 * This file is the seam that lets both be the same thing to the transcriber:
 *
 *   PyannoteTurnSource — today's streaming segmenter, wrapped UNCHANGED. Every existing test
 *                        scores this path, and it stays the fallback lane forever.
 *   CsrcTurnSource     — the transport as the spine. A turn opens when a source becomes audible
 *                        and closes when it stops (plus hysteresis, because DTX and jitter make
 *                        a speaker's packet train intermittent). Turns carry `trackId`.
 *
 * THE OVERLAP RULE, and it is the load-bearing one. The audio is MIXED: when two sources are
 * audible at once their voices are in the same samples and no cut can separate them. A source
 * spine that simply merged the two into one turn would hand a confident single name to a span
 * containing two people — fabrication dressed as attribution. So concurrency opens a CONTESTED
 * turn instead: its own span, no trackId, published unattributed. It is visible in the stats and
 * measurable in a replay, which is the whole difference between a known limit and a silent lie.
 */

/** What the transport said, structurally (mixed-capture-core's CsrcTransition, minus its identity
 *  — this module must not depend on the capture package to describe an edge). */
export interface TransportEvent {
  /** The RTP contributing-source id: stable per source for the life of the stream. */
  csrc: number;
  active: boolean;
  /** Epoch ms — the SAME clock feedAudio's tsMs carries. Nothing lines up without that. */
  tMs: number;
  audioLevel?: number;
}

/** Why a turn ended. The transcriber uses it to decide the STT context pad: a segmenter's
 *  speech-end lands a little early and needs one, a transport deactivation already carries the
 *  sensor's own inactivity window and must not be padded again. */
export type TurnCloseReason =
  | 'speaker-change'      // pyannote: speaker→speaker
  | 'overlap-edge'        // pyannote: overlap on/offset
  | 'silence'             // pyannote: speaker→silence
  | 'transport-inactive'  // csrc: the source stopped contributing (hysteresis elapsed)
  | 'contest-edge'        // csrc: concurrency began or ended
  | 'dispose';            // teardown / source switch

export interface TurnOpenedEvent {
  t0: number;
  /** The transport's id for the speaker of this turn. Absent ⇒ the span is not owned by one source. */
  trackId?: string;
  /** Two or more sources were audible across this span — the mix cannot be split, so no name. */
  contested?: boolean;
}

/** The open turn's live edge advanced: audio up to `tMs` belongs to the open turn and no further.
 *  This is what "STT windows cut at transport turn edges" means in practice — without it the open
 *  turn reads to the newest audio frame, which on the transport spine may already be someone else. */
export interface TurnGrownEvent { tMs: number; trackId?: string }

export interface TurnClosedEvent {
  t0: number;
  t1: number;
  trackId?: string;
  contested?: boolean;
  /** Every track that was audible at any point inside this span. For an owned turn that is the one
   *  track; for a contested one it is all of them — which is what lets a consumer tell an orphan
   *  with a single unambiguous neighbour from genuine crosstalk. */
  tracks?: string[];
  reason: TurnCloseReason;
}

export interface TurnSourceCallbacks {
  turnOpened(ev: TurnOpenedEvent): void;
  turnGrown?(ev: TurnGrownEvent): void;
  turnClosed(ev: TurnClosedEvent): void;
}

export interface TurnSourceHealth {
  kind: string;
  /** Edges this source produced (opens + closes). */
  edges: number;
  /** Transport transitions consumed (csrc only). */
  transitions?: number;
  /** Turns whose span carried two or more concurrent sources (csrc only). */
  contested?: number;
  /** Distinct track ids seen (csrc only). */
  tracks?: number;
}

export interface TurnSource {
  readonly kind: 'pyannote' | 'csrc';
  /** One mixed-audio frame. Sources that derive edges from the waveform consume it; the transport
   *  source uses it only as a CLOCK (and as the energy signal behind its liveness watchdog). */
  onAudio?(pcm: Float32Array, tsMs: number): void;
  /** One transport transition. Ignored by sources that do not read the transport. */
  onTransportEvent?(ev: TransportEvent): void;
  /** Close whatever is open, at `tMs`. Called on teardown and when authority moves elsewhere. */
  flush(tMs: number): void;
  /** Resolves once this source has no work in flight. Production never calls it — the lane feeds
   *  frames fire-and-forget on purpose, so a slow model cannot back up the capture path. A replay
   *  driver DOES call it, because "let the lane settle" has to include the model: the segmenter's
   *  inference takes tens of milliseconds and a driver that ran ahead of it would deliver
   *  boundaries at different points in the audio than a live run did, which is a divergence in the
   *  harness masquerading as one in the lane. */
  settled?(): Promise<void>;
  reset(): void;
  health(): TurnSourceHealth;
}

// ── pyannote ────────────────────────────────────────────────────────────────────────────────────

/** The cut source the segmenter presents (unchanged — re-declared here so turn-source.ts does not
 *  import the transcriber and the transcriber can import this). */
export interface BoundarySource {
  appendFrame(pcm: Float32Array, tsMs: number): Promise<unknown>;
  reset(): void;
}

/** The segmentation model's boundary, structurally (pyannote-segmenter's BoundaryEvent). */
export interface SegmenterBoundary {
  tMs: number;
  kind: 'silence→speaker' | 'speaker→speaker' | 'speaker→silence' | 'overlap-onset' | 'overlap-offset';
  confidence: number;
}

/**
 * Today's lane, wrapped. Deliberately STATELESS: it forwards each boundary exactly as
 * ChunkedTranscriber.handleBoundary did, so every existing test scores the same behaviour through
 * a new seam rather than a new algorithm. `t0` on close is reported as the boundary itself — this
 * source does not track which turn is open; the transcriber does, and always did.
 */
export class PyannoteTurnSource implements TurnSource {
  readonly kind = 'pyannote' as const;
  private edges = 0;
  /** Inference promises handed out and not yet resolved. Counted, never awaited by the lane. */
  private inFlight = new Set<Promise<unknown>>();

  private constructor(
    private readonly cb: TurnSourceCallbacks,
    private segmenter: BoundarySource | null,
    private readonly log: (m: string) => void,
  ) {}

  static async create(
    cb: TurnSourceCallbacks,
    makeSegmenter: (onBoundary: (ev: SegmenterBoundary) => void) => Promise<BoundarySource>,
    log: (m: string) => void = () => { /* silent */ },
  ): Promise<PyannoteTurnSource> {
    const src = new PyannoteTurnSource(cb, null, log);
    src.segmenter = await makeSegmenter((ev) => src.handleBoundary(ev));
    return src;
  }

  private handleBoundary(ev: SegmenterBoundary): void {
    switch (ev.kind) {
      case 'silence→speaker':
        this.edges++;
        this.cb.turnOpened({ t0: ev.tMs });
        break;
      case 'speaker→speaker':
      case 'overlap-onset':
      case 'overlap-offset':
        this.edges += 2;
        this.cb.turnClosed({ t0: ev.tMs, t1: ev.tMs, reason: ev.kind === 'speaker→speaker' ? 'speaker-change' : 'overlap-edge' });
        this.cb.turnOpened({ t0: ev.tMs });
        break;
      case 'speaker→silence':
        this.edges++;
        this.cb.turnClosed({ t0: ev.tMs, t1: ev.tMs, reason: 'silence' });
        break;
    }
  }

  onAudio(pcm: Float32Array, tsMs: number): void {
    const p = this.segmenter?.appendFrame(pcm, tsMs);
    if (!p) return;
    // Tracked, NOT awaited: the lane must never block capture on the model. The set exists purely
    // so a replay driver can wait for what the wall clock would have given the model anyway.
    this.inFlight.add(p);
    void p.catch((e: any) => this.log(`segmenter error: ${e?.message}`)).finally(() => this.inFlight.delete(p));
  }

  async settled(): Promise<void> {
    while (this.inFlight.size > 0) await Promise.allSettled([...this.inFlight]);
  }

  flush(): void { /* the transcriber's closing pass owns the final close, exactly as before */ }
  reset(): void { try { this.segmenter?.reset(); } catch { /* best effort */ } }
  health(): TurnSourceHealth { return { kind: this.kind, edges: this.edges }; }
}

// ── csrc (the transport spine) ──────────────────────────────────────────────────────────────────

const envNumber = (name: string, fallback: number): number => {
  const raw = typeof process !== 'undefined' ? process.env?.[name] : undefined;
  const n = raw !== undefined && raw !== '' ? Number(raw) : NaN;
  return Number.isFinite(n) && n > 0 ? n : fallback;
};

/** How long a source may go quiet before its turn is closed. The sensor already waits
 *  CSRC_INACTIVE_MS (800 ms, measured on live Teams) before declaring a deactivation, so this is
 *  the SECOND grace: it spans a breath, a DTX gap, a dropped packet train. Too small shatters a
 *  sentence into turns; too large merges a real handoff. Combined worst-case close latency is the
 *  sum of both graces (~1.4 s at the defaults). */
export const CSRC_HYSTERESIS_MS = envNumber('VEXA_CSRC_HYSTERESIS_MS', 600);
/** Energetic audio this long with NOT ONE transition ⇒ the transport stopped talking to us while
 *  the meeting continued. Measured in audio time, on frames that carry energy, so an honestly
 *  silent room never trips it. */
export const CSRC_DEATH_MS = envNumber('VEXA_CSRC_DEATH_MS', 45_000);
/** Below this RMS a frame is not evidence of anyone speaking (the transcriber's own DROP_RMS). */
const DEATH_RMS = 0.006;

export interface CsrcTurnSourceOptions {
  hysteresisMs?: number;
  /** Fired ONCE when the transport goes silent under continuing speech. The host demotes to the
   *  fallback source; this source never demotes itself (it does not know what else exists). */
  onDead?: (info: { reason: 'transport-silent'; tMs: number; quietMs: number }) => void;
  log?: (m: string) => void;
}

interface OpenTurn { t0: number; trackId?: string; contested: boolean; tracks: Set<string> }

/**
 * The transport as the turn spine.
 *
 * One turn is exposed at a time — the transcriber's ring is a single serialized pump and, more
 * fundamentally, the audio is one mix. The state machine is therefore over the SET of audible
 * sources, and only three shapes exist:
 *
 *   {}      → nothing open. Silence is never submitted to STT.
 *   {a}     → a turn carrying trackId=a. This is the case that buys us everything.
 *   {a,b,…} → a CONTESTED turn: its own span, no trackId, unattributed on publish.
 *
 * Every category change cuts. Membership changes WITHIN the contested shape do not — re-cutting
 * on each arrival would shred a crosstalk passage into unusable slivers, and every one of them
 * would be unattributed anyway.
 */
export class CsrcTurnSource implements TurnSource {
  readonly kind = 'csrc' as const;

  /** The transport spine has no asynchronous work at all — every edge is derived synchronously from
   *  an event that already happened. It is why THIS spine replays byte-identically under either
   *  clock, and the pyannote spine (a real model, inferring on its own schedule) does not. */
  async settled(): Promise<void> { /* nothing is ever in flight */ }


  private readonly hysteresisMs: number;
  private readonly log: (m: string) => void;
  private readonly onDead?: CsrcTurnSourceOptions['onDead'];

  /** Sources currently held audible. */
  private active = new Set<number>();
  /** csrc → the instant its deactivation was reported; it stays audible until +hysteresis. */
  private pendingClose = new Map<number, number>();
  private open: OpenTurn | null = null;

  /** The newest instant we have integrated to — max of audio time and transition time. */
  private clock = 0;
  /** Audio time of the newest transition; the liveness watchdog measures from here. */
  private lastTransitionMs = 0;
  private quietEnergyMs = 0;
  private deadFired = false;

  /** Closed runs, bounded — the timeline `ownerAt` reads. Trimmed to the recent past because the
   *  ring the lane can still cut from is bounded too. */
  private history: Array<{ track: string; t0: number; t1: number }> = [];
  private edges = 0;
  private transitions = 0;
  private contestedTurns = 0;
  private readonly seenTracks = new Set<number>();

  constructor(private readonly cb: TurnSourceCallbacks, opts: CsrcTurnSourceOptions = {}) {
    this.hysteresisMs = opts.hysteresisMs ?? CSRC_HYSTERESIS_MS;
    this.onDead = opts.onDead;
    this.log = opts.log ?? (() => { /* silent */ });
  }

  /** True once the transport has said ANYTHING. Until then this source has no opinion and the
   *  host must not make it authoritative — an unarmed spine emits no turns, which would be a hole
   *  in the transcript rather than a fallback. */
  get armed(): boolean { return this.transitions > 0; }

  onTransportEvent(ev: TransportEvent): void {
    if (!Number.isFinite(ev.csrc) || !Number.isFinite(ev.tMs)) return;
    this.transitions++;
    this.seenTracks.add(ev.csrc);
    // Resolve everything that expired BEFORE this edge, so turns close in time order.
    this.settle(ev.tMs);
    this.lastTransitionMs = Math.max(this.lastTransitionMs, ev.tMs);
    this.quietEnergyMs = 0;
    if (ev.active) {
      // A reactivation inside the hysteresis window is the same turn continuing — cancel the close.
      this.pendingClose.delete(ev.csrc);
      // A DIFFERENT source starting is positive evidence that whoever was trailing off has
      // finished. The hysteresis exists to bridge ONE speaker's own packet gaps (DTX, jitter); if
      // it is allowed to run past the moment the next speaker starts, every clean handoff is
      // reported as two people audible at once — which publishes as an unattributable contested
      // span and shreds the turn either side of it. On the adversarial m34 fixture, whose speakers
      // never actually overlap, that alone manufactured 8 contested turns out of 21. So retire the
      // others' pending closes AT THE INSTANT THEY STOPPED, before this activation is admitted.
      for (const [csrc, t1] of [...this.pendingClose]) {
        if (csrc === ev.csrc) continue;
        this.pendingClose.delete(csrc);
        this.active.delete(csrc);
        this.reconcile(Math.min(t1, ev.tMs));
      }
      if (!this.active.has(ev.csrc)) {
        this.active.add(ev.csrc);
        this.reconcile(ev.tMs);
      }
      return;
    }
    if (this.active.has(ev.csrc)) this.pendingClose.set(ev.csrc, ev.tMs);
  }

  onAudio(pcm: Float32Array, tsMs: number): void {
    const durMs = (pcm.length / 16000) * 1000;
    const edge = tsMs + durMs;
    this.settle(edge);
    if (this.lastTransitionMs === 0) this.lastTransitionMs = tsMs;
    // Liveness: only ENERGETIC audio counts toward the watchdog. A quiet room produces no
    // transitions and is not a dead transport.
    if (!this.deadFired && rms(pcm) >= DEATH_RMS) {
      this.quietEnergyMs += durMs;
      if (this.quietEnergyMs >= CSRC_DEATH_MS) {
        this.deadFired = true;
        this.log(`transport silent for ${Math.round(this.quietEnergyMs)}ms of energetic audio`);
        this.onDead?.({ reason: 'transport-silent', tMs: edge, quietMs: Math.round(this.quietEnergyMs) });
      }
    }
    if (!this.open) return;
    // The open turn may read to here and no further: a source whose deactivation is pending owns
    // audio only up to the instant it stopped contributing.
    let limit = edge;
    for (const t1 of this.pendingClose.values()) limit = Math.min(limit, t1);
    if (limit > this.open.t0) this.cb.turnGrown?.({ tMs: limit, trackId: this.open.trackId });
  }

  /** Who the transport says was audible at one instant. The turn is the unit the lane CUTS on, but
   *  it is not the unit attribution has to stop at: a turn carries Whisper's own word timestamps,
   *  and each of those can be asked this question independently. That is the difference between a
   *  whole sentence going out as "Speaker" and only the contested syllables doing so. */
  ownerAt(tMs: number): { trackId?: string; contested: boolean } {
    const on: string[] = [];
    for (const h of this.history) if (h.t0 <= tMs && tMs < h.t1) on.push(h.track);
    if (this.open && this.open.t0 <= tMs) for (const c of this.active) { const id = String(c); if (!on.includes(id)) on.push(id); }
    if (on.length === 1) return { trackId: on[0], contested: false };
    return { contested: on.length > 1 };
  }

  /** Every track audible at one instant — the candidate set a tie-break may choose from. */
  tracksAudibleAt(tMs: number): string[] {
    const on: string[] = [];
    for (const h of this.history) if (h.t0 <= tMs && tMs < h.t1 && !on.includes(h.track)) on.push(h.track);
    if (this.open && this.open.t0 <= tMs) for (const c of this.active) { const id = String(c); if (!on.includes(id)) on.push(id); }
    return on;
  }

  /** Retire deactivations whose hysteresis has elapsed, each at the instant the source actually
   *  stopped — not at the moment we noticed, or a turn's end would drift by the grace. */
  private settle(tNow: number): void {
    this.clock = Math.max(this.clock, tNow);
    for (;;) {
      let due: { csrc: number; t1: number } | null = null;
      for (const [csrc, t1] of this.pendingClose) {
        if (t1 + this.hysteresisMs > tNow) continue;
        if (!due || t1 < due.t1) due = { csrc, t1 };
      }
      if (!due) return;
      this.pendingClose.delete(due.csrc);
      this.active.delete(due.csrc);
      this.reconcile(due.t1);
    }
  }

  /** Bring the exposed turn in line with the audible set, cutting at `tEdge`. */
  private reconcile(tEdge: number): void {
    const desiredContested = this.active.size >= 2;
    const desiredTrack = this.active.size === 1 ? String([...this.active][0]) : undefined;

    if (this.open) {
      const same = desiredContested
        ? this.open.contested
        : (!this.open.contested && this.open.trackId === desiredTrack && desiredTrack !== undefined);
      if (same) { for (const c of this.active) this.open.tracks.add(String(c)); return; }
      this.closeOpen(tEdge, this.open.contested || desiredContested ? 'contest-edge'
        : (this.active.size === 0 ? 'transport-inactive' : 'speaker-change'));
    }
    if (this.active.size === 0) return;
    this.open = { t0: tEdge, trackId: desiredTrack, contested: desiredContested, tracks: new Set([...this.active].map(String)) };
    if (desiredContested) this.contestedTurns++;
    this.edges++;
    this.cb.turnOpened({ t0: tEdge, ...(desiredTrack ? { trackId: desiredTrack } : {}), ...(desiredContested ? { contested: true } : {}) });
  }

  private closeOpen(t1: number, reason: TurnCloseReason): void {
    const o = this.open;
    if (!o) return;
    for (const t of o.tracks) this.history.push({ track: t, t0: o.t0, t1: Math.max(t1, o.t0) });
    if (this.history.length > 4000) this.history.splice(0, this.history.length - 4000);
    this.open = null;
    this.edges++;
    this.cb.turnClosed({
      t0: o.t0, t1: Math.max(t1, o.t0), reason,
      ...(o.trackId ? { trackId: o.trackId } : {}),
      ...(o.contested ? { contested: true } : {}),
      tracks: [...o.tracks],
    });
  }

  flush(tMs: number): void {
    // Close every pending deactivation AT its own instant first, then whatever is still open.
    const t = Math.max(tMs, this.clock);
    for (const [csrc, t1] of [...this.pendingClose]) { this.pendingClose.delete(csrc); this.active.delete(csrc); this.reconcile(t1); }
    this.active.clear();
    this.closeOpen(t, 'dispose');
  }

  reset(): void {
    this.active.clear();
    this.pendingClose.clear();
    this.open = null;
    this.quietEnergyMs = 0;
    this.deadFired = false;
  }

  health(): TurnSourceHealth {
    return {
      kind: this.kind, edges: this.edges, transitions: this.transitions,
      contested: this.contestedTurns, tracks: this.seenTracks.size,
    };
  }
}

function rms(s: Float32Array): number {
  if (s.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < s.length; i++) sum += s[i] * s[i];
  return Math.sqrt(sum / s.length);
}
