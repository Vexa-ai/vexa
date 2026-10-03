/**
 * @vexa/record-chunker — the shared browser MediaRecorder driver.
 *
 * Runs in browser context. Wraps a MediaRecorder over a combined audio
 * MediaStream, encodes each timeslice to base64, and hands it to an injected
 * `onChunk` callback (the recording.v1 chunk shape). On stop it emits one final
 * chunk with `isFinal: true`. NO master assembly here — the master is built
 * server-side (meeting-api `recording_finalizer.py`) from the chunk_seq sequence.
 *
 * Both lane recording taps use this once: `@vexa/gmeet-capture` (gmeet) and
 * `@vexa/mixed-capture-core` (mixed/teams). The combine-the-audio step differs
 * per lane (gmeet builds a combined stream from its media elements; mixed
 * already has one mixed stream) — that lives in each lane; the MediaRecorder
 * loop is identical and lives here.
 *
 * No fallbacks: if `onChunk` throws or returns false we splice the chunk anyway
 * and log (the server-side reconciler re-fetches via the chunk_seq contract);
 * if no supported mimeType exists we log and refuse to start.
 */

/** One recording chunk, ready for upload. Mirrors the recording.v1 wire shape. */
export interface RecordingChunk {
  base64: string;
  chunkSeq: number;
  isFinal: boolean;
  mimeType: string;
}

/** A lane recording tap — what `createGmeetRecordingTap` / `createMixedRecordingTap` return. */
export interface RecordingTap {
  start(): Promise<void>;
  stop(): Promise<void>;
}

/** Options a host passes to a lane recording tap. */
export interface RecordingTapOptions {
  /** MediaRecorder timeslice in ms (default 15000 — matches the WAV chunk size). */
  timesliceMs?: number;
  /** Receives each chunk. Return false / throw → the chunk is spliced anyway (reconciler re-fetches). */
  onChunk: (chunk: RecordingChunk) => Promise<boolean> | boolean;
  /** Fired ONCE on MediaRecorder.onstart — t=0 of the master (segment↔audio alignment). */
  onStarted?: () => void;
}

export interface MediaRecorderChunkerOptions extends RecordingTapOptions {
  /** Combined audio stream to record (lane-built). */
  stream: MediaStream;
}

const BUFFER_CAP = 10;

/** The 4-byte EBML magic every valid webm/Matroska stream starts with (`1a 45 df a3`). */
const EBML_MAGIC = [0x1a, 0x45, 0xdf, 0xa3];

/** True when `bytes` begins with the EBML header (i.e. it is a self-describing webm init segment,
 *  not a cluster-only continuation chunk). */
function isWebmHeader(bytes: Uint8Array): boolean {
  return bytes.length >= 4 && EBML_MAGIC.every((b, i) => bytes[i] === b);
}

const blog = (msg: string) => { try { (window as any).logBot?.(msg); } catch { /* */ } };

/**
 * Drives a MediaRecorder over `stream`, emitting base64 chunks via `onChunk`.
 * Lifecycle: start() → chunks per timeslice → stop() resolves AFTER the final
 * chunk callback completes.
 */
export class MediaRecorderChunker implements RecordingTap {
  private opts: MediaRecorderChunkerOptions;
  private recorder: MediaRecorder | null = null;
  private chunkSeq = 0;
  private pending: Blob[] = [];
  private resolveFinalChunk: (() => void) | null = null;
  private mimeType = "audio/webm";
  /**
   * The webm EBML init segment retained from the FIRST self-describing blob (chunk 0:
   * `1a 45 df a3` EBML + Segment + Tracks + first Cluster). Held so it can be re-attached to a
   * later surviving chunk when chunk 0's own delivery fails over the page→Node base64 bridge;
   * without it the assembler would build a headerless (mid-Matroska `43 b6 75 …`) master from the
   * cluster-only survivors, which no player accepts. */
  private initSegment: Uint8Array | null = null;
  /** False until a chunk carrying the EBML header has been ACK'd by `onChunk` (returned truthy). */
  private initSegmentDelivered = false;

  constructor(opts: MediaRecorderChunkerOptions) {
    this.opts = opts;
  }

  /** The underlying MediaRecorder (null until start()). */
  getMediaRecorder(): MediaRecorder | null {
    return this.recorder;
  }

  /** Force the recorder to emit a chunk NOW with everything buffered since the last timeslice —
   *  without stopping. The tail-loss fix: on eviction Zoom navigates the page away (destroying the
   *  recorder) before the next timeslice boundary, so the last partial (the final few seconds of
   *  speech) never becomes a chunk. Flushing at the earliest end-signal (the mix going silent) turns
   *  that partial into a real, immediately-uploaded chunk before the context dies. Idempotent + safe. */
  flush(): void {
    try { if (this.recorder && this.recorder.state === "recording") this.recorder.requestData(); }
    catch (err: any) { blog(`[record-chunker] flush() requestData failed: ${err?.message || err}`); }
  }

  async start(): Promise<void> {
    if (this.recorder) {
      blog("[record-chunker] start() called twice — ignoring");
      return;
    }

    // Pick the best supported mimeType. No fallback beyond the candidate list.
    const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/ogg"];
    let chosen = "";
    for (const mime of candidates) {
      try {
        if ((window as any).MediaRecorder?.isTypeSupported?.(mime)) { chosen = mime; break; }
      } catch { /* */ }
    }

    let recorder: MediaRecorder;
    try {
      recorder = chosen
        ? new MediaRecorder(this.opts.stream, { mimeType: chosen })
        : new MediaRecorder(this.opts.stream);
    } catch (err: any) {
      blog(`[record-chunker] Failed to construct MediaRecorder: ${err?.message || err}`);
      return;
    }

    this.recorder = recorder;
    this.mimeType = recorder.mimeType || chosen || "audio/webm";

    // t=0 of the master — listeners align segment timestamps to audio origin.
    recorder.onstart = () => { try { this.opts.onStarted?.(); } catch { /* */ } };

    recorder.ondataavailable = async (event: BlobEvent) => {
      if (!(event.data && event.data.size > 0)) {
        blog("[record-chunker] dataavailable fired with empty data (skipping)");
        return;
      }

      // Defensive buffer + cap — successful callbacks splice; the cap should
      // never trip in normal operation (the reconciler re-fetches if it does).
      this.pending.push(event.data);
      if (this.pending.length > BUFFER_CAP) {
        const dropped = this.pending.shift();
        blog(`[record-chunker] WARN buffer exceeded cap ${BUFFER_CAP}, dropped oldest (${dropped?.size ?? 0} bytes); reconciler will re-fetch`);
      }

      const seq = this.chunkSeq;
      this.chunkSeq = seq + 1;

      try {
        const arrBuffer = await event.data.arrayBuffer();
        let bytes = new Uint8Array(arrBuffer);

        // Retain the EBML init segment from the FIRST self-describing blob. webm/Matroska always
        // starts with `1a 45 df a3`; MediaRecorder puts EBML + Segment + Tracks in chunk 0.
        if (!this.initSegment && isWebmHeader(bytes)) this.initSegment = bytes;

        // If the init segment has NOT yet been delivered (chunk 0's own send failed over the
        // bridge) and THIS chunk is cluster-only, PREPEND the retained header so the master is
        // never assembled headerless. The byte-concat codec keeps this valid: a chunk that is
        // [EBML init][cluster] is exactly what a self-describing chunk 0 looks like.
        if (this.initSegment && !this.initSegmentDelivered && !isWebmHeader(bytes)) {
          const merged = new Uint8Array(this.initSegment.length + bytes.length);
          merged.set(this.initSegment, 0);
          merged.set(bytes, this.initSegment.length);
          bytes = merged;
          blog(`[record-chunker] chunk ${seq} re-attached EBML init segment (${this.initSegment.length}B) — chunk 0 delivery was lost`);
        }

        const carriesHeader = isWebmHeader(bytes);
        // OPTIMISTICALLY mark the init segment delivered the moment a header-bearing chunk is
        // DISPATCHED (not after its await resolves), so a chunk emitted while chunk 0 is still
        // in flight does not redundantly re-attach the header. On a confirmed failure below we
        // clear the flag again so the NEXT surviving chunk carries the retained init segment.
        if (carriesHeader) this.initSegmentDelivered = true;

        let binary = "";
        const encodeChunkSize = 0x8000;
        for (let i = 0; i < bytes.length; i += encodeChunkSize) {
          binary += String.fromCharCode(...bytes.subarray(i, i + encodeChunkSize));
        }
        const base64 = btoa(binary);
        blog(`[record-chunker] chunk ${seq} (${bytes.length} bytes)`);
        try {
          const ok = await this.opts.onChunk({ base64, chunkSeq: seq, isFinal: false, mimeType: this.mimeType });
          // A header-bearing chunk that the sink REJECTED is not delivered — clear the flag so the
          // retained init segment is re-attached to the next surviving (cluster-only) chunk.
          if (!ok && carriesHeader) this.initSegmentDelivered = false;
          if (!ok) blog(`[record-chunker] chunk ${seq} callback returned false — sink rejected; reconciler will re-fetch`);
        } catch (cbErr: any) {
          if (carriesHeader) this.initSegmentDelivered = false;
          blog(`[record-chunker] chunk ${seq} callback threw: ${cbErr?.message || cbErr}; reconciler will re-fetch`);
        } finally {
          const idx = this.pending.indexOf(event.data);
          if (idx >= 0) this.pending.splice(idx, 1);
        }
      } catch (err: any) {
        const idx = this.pending.indexOf(event.data);
        if (idx >= 0) this.pending.splice(idx, 1);
        blog(`[record-chunker] chunk ${seq} encode FAILED: ${err?.message || err}; spliced`);
      }
    };

    recorder.onstop = async () => {
      // Final chunk (empty body OK — server treats isFinal=true as the COMPLETED signal).
      try {
        const finalSeq = this.chunkSeq;
        this.chunkSeq = finalSeq + 1;
        await this.opts.onChunk({ base64: "", chunkSeq: finalSeq, isFinal: true, mimeType: this.mimeType });
        blog(`[record-chunker] final chunk emitted (seq=${finalSeq})`);
      } catch (err: any) {
        blog(`[record-chunker] final chunk callback failed: ${err?.message || err}`);
      } finally {
        if (this.resolveFinalChunk) { this.resolveFinalChunk(); this.resolveFinalChunk = null; }
      }
    };

    recorder.start(this.opts.timesliceMs ?? 15000);
    blog(`[record-chunker] MediaRecorder started (${this.mimeType}, timeslice=${this.opts.timesliceMs ?? 15000}ms)`);
  }

  async stop(): Promise<void> {
    if (!this.recorder) { blog("[record-chunker] stop() before start() — ignoring"); return; }
    if (this.recorder.state === "inactive") { blog("[record-chunker] recorder already inactive"); return; }

    const finalChunkPromise = new Promise<void>((resolve) => {
      this.resolveFinalChunk = resolve;
      setTimeout(() => {
        if (this.resolveFinalChunk) {
          blog("[record-chunker] final chunk timeout — resolving");
          this.resolveFinalChunk(); this.resolveFinalChunk = null;
        }
      }, 10000);
    });

    try { this.recorder.stop(); }
    catch (err: any) { blog(`[record-chunker] recorder.stop() threw: ${err?.message || err}`); }

    await finalChunkPromise;
  }
}

// ───────────────────────────────────────────────────────────────────────
// createRecordingTap — the full browser recording tap (generic, all platforms)
// ───────────────────────────────────────────────────────────────────────

/**
 * Every on-page audio-bearing MediaStream that is CURRENTLY producing audio (has a `live` track).
 * Prefers `srcObject`; falls back to a captureStream() cached on the element (a fresh captureStream()
 * each scan would mint a new stream id, defeating the mixer's dedup and leaking source nodes). Deduped
 * downstream by stream id. Platform-agnostic — a recording grabs every audio element on the page.
 */
function collectAudioStreams(): MediaStream[] {
  const els = Array.from(document.querySelectorAll("audio, video")) as any[];
  const out: MediaStream[] = [];
  for (const el of els) {
    try {
      let s: MediaStream | null = el.srcObject instanceof MediaStream ? el.srcObject : null;
      if (!s) {
        const cap: unknown =
          el.__vexaRecCapStream instanceof MediaStream ? el.__vexaRecCapStream :
          typeof el.captureStream === "function" ? el.captureStream() :
          typeof el.mozCaptureStream === "function" ? el.mozCaptureStream() : null;
        if (cap instanceof MediaStream) { el.__vexaRecCapStream = cap; s = cap; }
      }
      // Only LIVE audio: an element lingering with an ENDED track must not re-enter the mix (it would
      // reconnect a dead source every scan and oscillate against the prune below).
      if (s && s.getAudioTracks().some((t) => t.readyState === "live")) out.push(s);
    } catch { /* not probeable; skip */ }
  }
  return out;
}

/**
 * A LIVE audio mix that FOLLOWS the meeting. One AudioContext destination that a periodic rescan keeps
 * wired to every current on-page audio stream: it connects newly-appearing streams and prunes ones whose
 * tracks have ended. Zoom/Teams deliver each participant as a separate WebRTC track and churn them
 * constantly (each new track is mirrored into a fresh injected <audio> by @vexa/mixed-capture-core's
 * hook) — so a ONE-TIME combine records silence the moment the originally-captured tracks end. Recording
 * `dest.stream` (a STABLE handle whose sources come and go underneath) keeps the mix complete for the
 * whole meeting. Full sample rate — deliberately NOT the 16 kHz transcription mix.
 */
class DynamicAudioMixer {
  private ctx: AudioContext;
  private dest: MediaStreamAudioDestinationNode;
  private sources = new Map<string, { node: MediaStreamAudioSourceNode; stream: MediaStream }>();
  private timer: ReturnType<typeof setInterval> | null = null;
  /** Fired when the live-source count falls to 0 (all participant tracks ended — the meeting is
   *  closing). The recording host uses it to flush the recorder's buffered tail before the page dies. */
  private onEmpty: (() => void) | null;

  constructor(onEmpty?: () => void) {
    this.ctx = new AudioContext();   // default (full) sample rate — NOT the 16 kHz transcription mix
    this.ctx.resume?.();
    this.dest = this.ctx.createMediaStreamDestination();
    this.onEmpty = onEmpty ?? null;
  }

  get stream(): MediaStream { return this.dest.stream; }
  get connectedCount(): number { return this.sources.size; }

  /** Connect newly-seen streams; prune streams whose audio has ended. Idempotent — safe every tick. */
  scan(): void {
    const present = new Map(collectAudioStreams().map((s) => [s.id, s]));
    for (const [id, s] of present) {
      if (this.sources.has(id)) continue;
      try {
        const node = this.ctx.createMediaStreamSource(s);
        node.connect(this.dest);
        this.sources.set(id, { node, stream: s });
        blog(`[record-chunker] mix +stream (${this.sources.size} live)`);
      } catch { /* not connectable yet — retried next tick */ }
    }
    // Prune truly-dead streams (all tracks ended) so a long meeting's track churn doesn't accumulate
    // source nodes. A still-live stream is KEPT even if its element left the DOM (the node holds the
    // stream ref and still pulls audio) — presence is not the liveness signal, the track state is.
    const had = this.sources.size;
    for (const [id, entry] of this.sources) {
      if (entry.stream.getAudioTracks().some((t) => t.readyState === "live")) continue;
      try { entry.node.disconnect(); } catch { /* */ }
      this.sources.delete(id);
      blog(`[record-chunker] mix -stream (${this.sources.size} live)`);
    }
    // Just went silent (all tracks ended → the meeting is closing) — flush the recorder's buffered tail
    // NOW, before the platform navigates the page away and destroys the recorder mid-partial-timeslice.
    if (had > 0 && this.sources.size === 0 && this.onEmpty) { try { this.onEmpty(); } catch { /* */ } }
  }

  start(): void {
    this.scan();
    this.timer = setInterval(() => { try { this.scan(); } catch { /* the rescan must never die */ } }, 2000);
  }

  stop(): void {
    if (this.timer) { clearInterval(this.timer); this.timer = null; }
    for (const { node } of this.sources.values()) { try { node.disconnect(); } catch { /* */ } }
    this.sources.clear();
    try { void this.ctx.close(); } catch { /* */ }
  }
}

/** Options for createRecordingTap — combine all audio elements then optionally override. */
export interface CreateRecordingTapOptions extends RecordingTapOptions {
  /** Provide a ready stream to record (e.g. the mixed-lane tab stream); else all audio elements are combined. */
  stream?: MediaStream;
}

/**
 * The browser recording tap, used by BOTH lanes (gmeet, teams) and both hosts
 * (bot, extension): find every audio element → combine → `MediaRecorderChunker`
 * → recording.v1 chunks via `onChunk`. Recording is platform-agnostic — it
 * records the whole meeting mix — so this is ONE generic tap, not per-lane.
 * (Zoom records via node PulseAudio in @vexa/recording, no browser tap.)
 *
 * Pass `opts.stream` to record a ready stream directly (skips the element
 * combine); otherwise it finds + combines the page's audio elements.
 */
export function createRecordingTap(opts: CreateRecordingTapOptions): RecordingTap {
  let chunker: MediaRecorderChunker | null = null;
  let mixer: DynamicAudioMixer | null = null;
  return {
    async start(): Promise<void> {
      let stream = opts.stream;
      if (!stream) {
        // No ready stream ⇒ build a LIVE mix of the page's audio. Wait (bounded) for the first stream —
        // post-admission the participant / injected <audio> elements appear a beat late — so the
        // MediaRecorder's onStarted t=0 aligns with real audio; the rescan then follows the churn.
        // onEmpty → flush the recorder's buffered tail the instant the mix goes silent (meeting close),
        // so the final partial timeslice (the last few seconds of speech) is a real chunk before the
        // page navigates away on eviction. `chunker` is assigned just below and set by the time this fires.
        mixer = new DynamicAudioMixer(() => chunker?.flush());
        let ready = false;
        for (let i = 0; i < 5; i++) {
          mixer.scan();
          if (mixer.connectedCount > 0) { ready = true; break; }
          await new Promise((r) => setTimeout(r, 2000));
        }
        if (!ready) { blog("[record-chunker] no audio streams — cannot record"); mixer.stop(); mixer = null; return; }
        mixer.start();
        stream = mixer.stream;
      }
      chunker = new MediaRecorderChunker({
        stream,
        timesliceMs: opts.timesliceMs ?? 15000,
        onChunk: opts.onChunk,
        onStarted: opts.onStarted,
      });
      await chunker.start();
    },
    async stop(): Promise<void> {
      await chunker?.stop();
      chunker = null;
      mixer?.stop();
      mixer = null;
    },
  };
}
