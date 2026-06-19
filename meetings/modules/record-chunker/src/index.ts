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

  constructor(opts: MediaRecorderChunkerOptions) {
    this.opts = opts;
  }

  /** The underlying MediaRecorder (null until start()). */
  getMediaRecorder(): MediaRecorder | null {
    return this.recorder;
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
        const bytes = new Uint8Array(arrBuffer);
        let binary = "";
        const encodeChunkSize = 0x8000;
        for (let i = 0; i < bytes.length; i += encodeChunkSize) {
          binary += String.fromCharCode(...bytes.subarray(i, i + encodeChunkSize));
        }
        const base64 = btoa(binary);
        blog(`[record-chunker] chunk ${seq} (${bytes.length} bytes)`);
        try {
          const ok = await this.opts.onChunk({ base64, chunkSeq: seq, isFinal: false, mimeType: this.mimeType });
          if (!ok) blog(`[record-chunker] chunk ${seq} callback returned false — sink rejected; reconciler will re-fetch`);
        } catch (cbErr: any) {
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
 * Find active media elements that expose audio. Two-pass (strict → relaxed):
 * tiles can be paused or expose audio via captureStream() rather than a direct
 * srcObject, so the relaxed pass mirrors buildCombinedStream's fallbacks. This
 * is platform-agnostic — a recording grabs every audio element on the page.
 */
async function findMediaElements(retries = 5, delay = 2000): Promise<HTMLMediaElement[]> {
  for (let i = 0; i < retries; i++) {
    const all = Array.from(document.querySelectorAll("audio, video"));
    let els = all.filter((el: any) =>
      !el.paused && el.srcObject instanceof MediaStream && el.srcObject.getAudioTracks().length > 0
    ) as HTMLMediaElement[];
    if (els.length > 0) { blog(`[record-chunker] ${els.length} media elements (strict)`); return els; }

    els = all.filter((el: any) => {
      try {
        if (el.srcObject instanceof MediaStream && el.srcObject.getAudioTracks().length > 0) return true;
        if (typeof el.captureStream === "function" && el.captureStream()?.getAudioTracks?.().length > 0) return true;
        if (typeof el.mozCaptureStream === "function" && el.mozCaptureStream()?.getAudioTracks?.().length > 0) return true;
      } catch { /* not probeable; skip */ }
      return false;
    }) as HTMLMediaElement[];
    if (els.length > 0) { blog(`[record-chunker] ${els.length} media elements (relaxed)`); return els; }

    await new Promise((r) => setTimeout(r, delay));
  }
  return [];
}

/** Mix every media element's audio into one MediaStream via a destination node. */
async function buildCombinedStream(mediaElements: HTMLMediaElement[]): Promise<MediaStream> {
  if (mediaElements.length === 0) throw new Error("[record-chunker] no media elements to combine");
  const ctx = new AudioContext();
  const dest = ctx.createMediaStreamDestination();
  let connected = 0;
  mediaElements.forEach((element: any, index) => {
    try {
      const s =
        element.srcObject ||
        (element.captureStream && element.captureStream()) ||
        (element.mozCaptureStream && element.mozCaptureStream());
      if (s instanceof MediaStream && s.getAudioTracks().length > 0) {
        ctx.createMediaStreamSource(s).connect(dest);
        connected++;
        blog(`[record-chunker] connected element ${index + 1}/${mediaElements.length}`);
      }
    } catch (e: any) { blog(`[record-chunker] could not connect element ${index + 1}: ${e?.message || e}`); }
  });
  if (connected === 0) throw new Error("[record-chunker] could not connect any audio streams");
  blog(`[record-chunker] combined ${connected} streams`);
  return dest.stream;
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
  return {
    async start(): Promise<void> {
      let stream = opts.stream;
      if (!stream) {
        const els = await findMediaElements();
        if (els.length === 0) { blog("[record-chunker] no media elements — cannot record"); return; }
        stream = await buildCombinedStream(els);
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
    },
  };
}
