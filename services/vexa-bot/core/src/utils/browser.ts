/**
 * Browser context utilities and services
 * These classes run inside page.evaluate() browser context
 */

/**
 * Generate UUID for browser context
 */
export function generateBrowserUUID(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  } else {
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(
      /[xy]/g,
      function (c) {
        var r = (Math.random() * 16) | 0,
          v = c == "x" ? r : (r & 0x3) | 0x8;
        return v.toString(16);
      }
    );
  }
}

/**
 * Browser-compatible AudioService for browser context
 */
export class BrowserAudioService {
  private config: any;
  private processor: any = null;
  private audioContext: AudioContext | null = null;
  private destinationNode: MediaStreamAudioDestinationNode | null = null;

  constructor(config: any) {
    this.config = config;
  }

  async findMediaElements(retries: number = 10, delay: number = 3000): Promise<HTMLMediaElement[]> {
    for (let i = 0; i < retries; i++) {
      // Get all media elements
      const allMediaElements = Array.from(document.querySelectorAll("audio, video")) as HTMLMediaElement[];
      (window as any).logBot(`[Audio] Attempt ${i + 1}/${retries}: Found ${allMediaElements.length} total media elements in DOM`);
      
      // Filter for active media elements with proper checks
      const mediaElements = allMediaElements.filter((el: any) => {
        // Check if element has srcObject
        if (!el.srcObject) {
          return false;
        }
        
        // Check if srcObject is a MediaStream
        if (!(el.srcObject instanceof MediaStream)) {
          return false;
        }
        
        // Check if MediaStream has audio tracks
        const audioTracks = el.srcObject.getAudioTracks();
        if (audioTracks.length === 0) {
          return false;
        }
        
        // Check if element is not paused (like Node.js version)
        if (el.paused) {
          (window as any).logBot(`[Audio] Element found but is paused (readyState: ${el.readyState})`);
          return false;
        }
        
        // Check readyState - prefer elements that have loaded metadata or more
        // 0 = HAVE_NOTHING, 1 = HAVE_METADATA, 2 = HAVE_CURRENT_DATA, 3 = HAVE_FUTURE_DATA, 4 = HAVE_ENOUGH_DATA
        if (el.readyState < 1) {
          (window as any).logBot(`[Audio] Element found but readyState is ${el.readyState} (HAVE_NOTHING)`);
          return false;
        }
        
        // Check if audio tracks are enabled.
        // Note: track.muted is a read-only WebRTC property set by the remote end.
        // Teams initially delivers tracks with muted=true until someone speaks.
        // We accept tracks that are enabled (even if muted) — the ScriptProcessor
        // will get silence until actual audio flows, which is fine.
        const hasEnabledTracks = audioTracks.some((track: MediaStreamTrack) => track.enabled);
        if (!hasEnabledTracks) {
          (window as any).logBot(`[Audio] Element found but all audio tracks are disabled`);
          return false;
        }
        
        return true;
      });

      if (mediaElements.length > 0) {
        (window as any).logBot(`✅ Found ${mediaElements.length} active media elements with audio tracks after ${i + 1} attempt(s).`);
        // Log details about found elements
        mediaElements.forEach((el: any, idx: number) => {
          const tracks = el.srcObject.getAudioTracks();
          (window as any).logBot(`  Element ${idx + 1}: paused=${el.paused}, readyState=${el.readyState}, tracks=${tracks.length}, enabled=${tracks.filter((t: MediaStreamTrack) => t.enabled).length}`);
        });
        return mediaElements;
      }
      
      // Enhanced diagnostic logging
      if (allMediaElements.length > 0) {
        (window as any).logBot(`[Audio] Found ${allMediaElements.length} media elements but none are active. Details:`);
        allMediaElements.forEach((el: any, idx: number) => {
          const hasSrcObject = !!el.srcObject;
          const isMediaStream = el.srcObject instanceof MediaStream;
          const audioTracks = isMediaStream ? el.srcObject.getAudioTracks().length : 0;
          (window as any).logBot(`  Element ${idx + 1}: paused=${el.paused}, readyState=${el.readyState}, hasSrcObject=${hasSrcObject}, isMediaStream=${isMediaStream}, audioTracks=${audioTracks}`);
        });
      } else {
        (window as any).logBot(`[Audio] No media elements found in DOM at all`);
      }
      
      (window as any).logBot(`[Audio] Retrying in ${delay}ms... (Attempt ${i + 2}/${retries})`);
      await new Promise(resolve => setTimeout(resolve, delay));
    }
    
    (window as any).logBot(`❌ No active media elements found after ${retries} attempts`);
    return [];
  }

  async createCombinedAudioStream(mediaElements: HTMLMediaElement[]): Promise<MediaStream> {
    if (mediaElements.length === 0) {
      throw new Error("No media elements provided for audio stream creation");
    }

    (window as any).logBot(`Found ${mediaElements.length} active media elements.`);
    if (!this.audioContext) {
      this.audioContext = new AudioContext();
    }
    if (!this.destinationNode) {
      this.destinationNode = this.audioContext.createMediaStreamDestination();
    }
    let sourcesConnected = 0;

    // Connect all media elements to the destination node
    mediaElements.forEach((element: any, index: number) => {
      try {
        // Ensure element is actually audible
        if (typeof element.muted === "boolean") element.muted = false;
        if (typeof element.volume === "number") element.volume = 1.0;
        if (typeof element.play === "function") {
          element.play().catch(() => {});
        }

        const elementStream =
          element.srcObject ||
          (element.captureStream && element.captureStream()) ||
          (element.mozCaptureStream && element.mozCaptureStream());

        // Debug audio tracks and unmute them
        if (elementStream instanceof MediaStream) {
          const audioTracks = elementStream.getAudioTracks();
          (window as any).logBot(`Element ${index + 1}: Found ${audioTracks.length} audio tracks`);
          audioTracks.forEach((track, trackIndex) => {
            (window as any).logBot(`  Track ${trackIndex}: enabled=${track.enabled}, muted=${track.muted}, label=${track.label}`);
            
            // Unmute muted audio tracks
            if (track.muted) {
              track.enabled = true;
              // Force unmute by setting muted to false
              try {
                (track as any).muted = false;
                (window as any).logBot(`  Unmuted track ${trackIndex} (enabled=${track.enabled}, muted=${track.muted})`);
              } catch (e: unknown) {
                const message = e instanceof Error ? e.message : String(e);
                (window as any).logBot(`  Could not unmute track ${trackIndex}: ${message}`);
              }
            }
          });
        }

        if (
          elementStream instanceof MediaStream &&
          elementStream.getAudioTracks().length > 0
        ) {
          // Connect regardless of the read-only muted flag; WebAudio can still pull samples
          const sourceNode = this.audioContext!.createMediaStreamSource(elementStream);
          sourceNode.connect(this.destinationNode!);
          sourcesConnected++;
          (window as any).logBot(`Connected audio stream from element ${index + 1}/${mediaElements.length}. Tracks=${elementStream.getAudioTracks().length}`);
        } else {
          (window as any).logBot(`Skipping element ${index + 1}: No audio tracks found`);
        }
      } catch (error: any) {
        (window as any).logBot(`Could not connect element ${index + 1}: ${error.message}`);
      }
    });

    if (sourcesConnected === 0) {
      throw new Error("Could not connect any audio streams. Check media permissions.");
    }

    (window as any).logBot(`Successfully combined ${sourcesConnected} audio streams.`);
    return this.destinationNode!.stream;
  }

  async initializeAudioProcessor(combinedStream: MediaStream): Promise<any> {
    // Reuse existing context if available
    if (!this.audioContext) {
      this.audioContext = new AudioContext();
    }
    if (!this.destinationNode) {
      this.destinationNode = this.audioContext.createMediaStreamDestination();
    }

    const mediaStream = this.audioContext.createMediaStreamSource(combinedStream);
    const recorder = this.audioContext.createScriptProcessor(
      this.config.bufferSize,
      this.config.inputChannels,
      this.config.outputChannels
    );
    const gainNode = this.audioContext.createGain();
    gainNode.gain.value = 0; // Silent playback

    // Connect the audio processing pipeline
    mediaStream.connect(recorder);
    recorder.connect(gainNode);
    gainNode.connect(this.audioContext.destination);

    this.processor = {
      audioContext: this.audioContext,
      destinationNode: this.destinationNode,
      recorder,
      mediaStream,
      gainNode,
      sessionAudioStartTimeMs: null
    };

    try { await this.audioContext.resume(); } catch {}
    (window as any).logBot("Audio processing pipeline connected and ready.");
    return this.processor;
  }

  setupAudioDataProcessor(onAudioData: (audioData: Float32Array, sessionStartTime: number | null) => void): void {
    if (!this.processor) {
      throw new Error("Audio processor not initialized");
    }

    this.processor.recorder.onaudioprocess = async (event: any) => {
      // Set session start time on first audio chunk
      if (this.processor!.sessionAudioStartTimeMs === null) {
        this.processor!.sessionAudioStartTimeMs = Date.now();
        (window as any).logBot(`[Audio] Session audio start time set: ${this.processor!.sessionAudioStartTimeMs}`);
      }

      const inputData = event.inputBuffer.getChannelData(0);
      const resampledData = this.resampleAudioData(inputData, this.processor!.audioContext.sampleRate);
      
      onAudioData(resampledData, this.processor!.sessionAudioStartTimeMs);
    };
  }

  private resampleAudioData(inputData: Float32Array, sourceSampleRate: number): Float32Array {
    const targetLength = Math.round(
      inputData.length * (this.config.targetSampleRate / sourceSampleRate)
    );
    const resampledData = new Float32Array(targetLength);
    const springFactor = (inputData.length - 1) / (targetLength - 1);
    
    resampledData[0] = inputData[0];
    resampledData[targetLength - 1] = inputData[inputData.length - 1];
    
    for (let i = 1; i < targetLength - 1; i++) {
      const index = i * springFactor;
      const leftIndex = Math.floor(index);
      const rightIndex = Math.ceil(index);
      const fraction = index - leftIndex;
      resampledData[i] =
        inputData[leftIndex] +
        (inputData[rightIndex] - inputData[leftIndex]) * fraction;
    }
    
    return resampledData;
  }

  getSessionAudioStartTime(): number | null {
    return this.processor?.sessionAudioStartTimeMs || null;
  }

  resetSessionStartTime(): void {
    if (this.processor) {
      const oldTime = this.processor.sessionAudioStartTimeMs;
      this.processor.sessionAudioStartTimeMs = null;
      (window as any).logBot(`[Audio] Reset session audio start time: ${oldTime} -> null (will be set on next audio chunk)`);
    }
  }

  disconnect(): void {
    if (this.processor) {
      try {
        this.processor.recorder.disconnect();
        this.processor.mediaStream.disconnect();
        this.processor.gainNode.disconnect();
        this.processor.audioContext.close();
        (window as any).logBot("Audio processing pipeline disconnected.");
      } catch (error: any) {
        (window as any).logBot(`Error disconnecting audio pipeline: ${error.message}`);
      }
      this.processor = null;
    }
  }
}

/**
 * BrowserMediaRecorderPipeline — Pack U.2 (v0.10.6) shared MediaRecorder driver.
 *
 * Runs in browser context. Wraps a MediaRecorder over a combined audio
 * MediaStream, encodes each chunk to base64, and pushes it to the Node-side
 * `__vexaSaveRecordingChunk` callback exposed by services/audio-pipeline.ts:
 * MediaRecorderCapture. Replaces the inline ondataavailable + chunk-buffer
 * boilerplate that previously lived in googlemeet/recording.ts and
 * msteams/recording.ts.
 *
 * Pack M discipline survives here: the in-flight chunk buffer is short-lived
 * (we splice on callback resolution; the cap is purely defensive). No bot-side
 * master assembly — the master is built server-side from the chunk_seq
 * sequence by recording_finalizer.py.
 *
 * Pack P / develop stage rule (no fallbacks): if the chunkCallback throws or
 * returns false, we splice the chunk anyway and log; the server-side
 * reconciler covers re-fetch via the chunk_seq contract. If MediaRecorder
 * cannot be constructed (no supported mimeType), we log and refuse to start —
 * no silent fallback to a different recording mechanism.
 *
 * Lifecycle:
 *   const pipeline = new BrowserMediaRecorderPipeline({ stream, timesliceMs, chunkCallback });
 *   await pipeline.start();   // creates MediaRecorder, calls .start(timesliceMs)
 *   // ... meeting runs, chunks flow on each timeslice ...
 *   await pipeline.stop();    // resolves AFTER the final chunk callback completes
 */
export interface BrowserMediaRecorderPipelineOptions {
  /** Combined audio stream (typically from BrowserAudioService.createCombinedAudioStream). */
  stream: MediaStream;
  /** MediaRecorder timeslice in ms. */
  timesliceMs: number;
  /**
   * Bridge to the Node-side __vexaSaveRecordingChunk callback. Returns the
   * Node side's success indicator (Boolean — true if uploaded, false if the
   * sink rejected). We splice the chunk regardless of return value because
   * the server-side reconciler covers re-fetch.
   */
  chunkCallback: (payload: {
    base64: string;
    chunkSeq: number;
    isFinal: boolean;
    mimeType: string;
  }) => Promise<boolean>;
}

export class BrowserMediaRecorderPipeline {
  private opts: BrowserMediaRecorderPipelineOptions;
  private recorder: MediaRecorder | null = null;
  private chunkSeq: number = 0;
  private pending: Blob[] = [];
  private static readonly BUFFER_CAP = 10;
  private finalChunkPromise: Promise<void> | null = null;
  private resolveFinalChunk: (() => void) | null = null;
  private mimeType: string = "audio/webm";

  constructor(opts: BrowserMediaRecorderPipelineOptions) {
    this.opts = opts;
  }

  /** Returns the underlying MediaRecorder (null until start()). */
  getMediaRecorder(): MediaRecorder | null {
    return this.recorder;
  }

  async start(): Promise<void> {
    if (this.recorder) {
      (window as any).logBot?.("[BrowserMediaRecorderPipeline] start() called twice — ignoring");
      return;
    }

    // Pick the best supported MediaRecorder mimeType. No fallback beyond the
    // candidate list — if none of these work, we log and refuse.
    const candidates = [
      "audio/webm;codecs=opus",
      "audio/webm",
      "audio/ogg;codecs=opus",
      "audio/ogg",
    ];
    let chosen = "";
    for (const mime of candidates) {
      try {
        if ((window as any).MediaRecorder?.isTypeSupported?.(mime)) {
          chosen = mime;
          break;
        }
      } catch {}
    }

    let recorder: MediaRecorder;
    try {
      recorder = chosen
        ? new MediaRecorder(this.opts.stream, { mimeType: chosen })
        : new MediaRecorder(this.opts.stream);
    } catch (err: any) {
      (window as any).logBot?.(
        `[BrowserMediaRecorderPipeline] Failed to construct MediaRecorder: ${err?.message || err}`
      );
      return;
    }

    this.recorder = recorder;
    this.mimeType = recorder.mimeType || chosen || "audio/webm";

    recorder.ondataavailable = async (event: BlobEvent) => {
      if (!(event.data && event.data.size > 0)) {
        (window as any).logBot?.("[BrowserMediaRecorderPipeline] dataavailable fired with empty data (skipping)");
        return;
      }

      // Push to defensive buffer + cap-check. The cap should never trip in
      // normal operation because successful uploads splice. If it does, we
      // drop the oldest and log — server-side reconciler covers re-fetch
      // from S3 via the chunk_seq contract.
      this.pending.push(event.data);
      if (this.pending.length > BrowserMediaRecorderPipeline.BUFFER_CAP) {
        const dropped = this.pending.shift();
        (window as any).logBot?.(
          `[BrowserMediaRecorderPipeline] WARN buffer exceeded cap ${BrowserMediaRecorderPipeline.BUFFER_CAP}, dropped oldest (${dropped?.size ?? 0} bytes); reconciler will re-fetch from S3`
        );
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

        (window as any).logBot?.(
          `[BrowserMediaRecorderPipeline] Uploading chunk ${seq} (${bytes.length} bytes)`
        );

        try {
          const ok = await this.opts.chunkCallback({
            base64,
            chunkSeq: seq,
            isFinal: false,
            mimeType: this.mimeType,
          });
          if (!ok) {
            (window as any).logBot?.(
              `[BrowserMediaRecorderPipeline] Chunk ${seq} callback returned false — sink rejected; reconciler will re-fetch`
            );
          }
        } catch (cbErr: any) {
          (window as any).logBot?.(
            `[BrowserMediaRecorderPipeline] Chunk ${seq} callback threw: ${cbErr?.message || cbErr}; reconciler will re-fetch`
          );
        } finally {
          // Splice the chunk regardless of callback outcome — see Pack M.
          const idx = this.pending.indexOf(event.data);
          if (idx >= 0) this.pending.splice(idx, 1);
        }
      } catch (err: any) {
        // Splice on encode failure too — chunk is unrecoverable.
        const idx = this.pending.indexOf(event.data);
        if (idx >= 0) this.pending.splice(idx, 1);
        (window as any).logBot?.(
          `[BrowserMediaRecorderPipeline] Chunk ${seq} encode FAILED: ${err?.message || err}; spliced from buffer`
        );
      }
    };

    recorder.onstop = async () => {
      // Emit a final chunk (empty body OK — server treats isFinal=true as the
      // signal to flip Recording.status, regardless of payload size).
      try {
        const finalSeq = this.chunkSeq;
        this.chunkSeq = finalSeq + 1;
        await this.opts.chunkCallback({
          base64: "",
          chunkSeq: finalSeq,
          isFinal: true,
          mimeType: this.mimeType,
        });
        (window as any).logBot?.(
          `[BrowserMediaRecorderPipeline] Final chunk emitted (seq=${finalSeq})`
        );
      } catch (err: any) {
        (window as any).logBot?.(
          `[BrowserMediaRecorderPipeline] Final chunk callback failed: ${err?.message || err}`
        );
      } finally {
        if (this.resolveFinalChunk) {
          this.resolveFinalChunk();
          this.resolveFinalChunk = null;
        }
      }
    };

    recorder.start(this.opts.timesliceMs);
    (window as any).logBot?.(
      `[BrowserMediaRecorderPipeline] MediaRecorder started (${this.mimeType}, timeslice=${this.opts.timesliceMs}ms)`
    );
  }

  async stop(): Promise<void> {
    if (!this.recorder) {
      (window as any).logBot?.("[BrowserMediaRecorderPipeline] stop() called before start() — ignoring");
      return;
    }
    if (this.recorder.state === "inactive") {
      (window as any).logBot?.("[BrowserMediaRecorderPipeline] recorder already inactive");
      return;
    }

    this.finalChunkPromise = new Promise<void>((resolve) => {
      this.resolveFinalChunk = resolve;
      // Safety timeout — onstop must fire within 10s of stop(), else we
      // resolve so the bot can exit.
      setTimeout(() => {
        if (this.resolveFinalChunk) {
          (window as any).logBot?.("[BrowserMediaRecorderPipeline] final chunk timeout — resolving");
          this.resolveFinalChunk();
          this.resolveFinalChunk = null;
        }
      }, 10000);
    });

    try {
      this.recorder.stop();
    } catch (err: any) {
      (window as any).logBot?.(
        `[BrowserMediaRecorderPipeline] recorder.stop() threw: ${err?.message || err}`
      );
    }

    await this.finalChunkPromise;
  }
}

