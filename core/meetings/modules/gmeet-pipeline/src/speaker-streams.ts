import { log } from './log.js';
import { isHallucination } from './hallucination-filter.js';
import { longestCommonWordPrefix } from '@vexa/transcribe-buffer';

/**
 * Per-speaker audio buffer with offset-based sliding window.
 *
 * Two pointers track progress through a continuous audio stream:
 *   - confirmedSamples: audio before this has been confirmed and emitted
 *   - totalSamples: end of audio buffer
 *
 * Each Whisper submission sends only unconfirmed audio (confirmedSamples → totalSamples).
 * On confirmation, confirmedSamples advances — audio is trimmed from the front.
 * Buffer never fully resets during continuous speech. Full reset only on speaker
 * change or idle timeout.
 */

interface WhisperSegment {
  text: string;
  start: number;
  end: number;
}

interface SpeakerBuffer {
  speakerId: string;
  speakerName: string;
  chunks: Float32Array[];
  totalSamples: number;
  /** Samples already confirmed and emitted — next submission starts here */
  confirmedSamples: number;
  lastTranscript: string;
  confirmCount: number;
  /** Word-level prefix confirmation: words from previous Whisper submission */
  lastWords: string[];
  inFlight: boolean;
  /** Wall-clock time (ms) when the current unconfirmed window started */
  windowStartMs: number;
  /** Wall-clock time (ms) when the buffer first started (for segment timing) */
  bufferStartMs: number;
  /** Monotonic sequence number for segment_id generation */
  sequenceNumber: number;
  /** Wall-clock time (ms) when audio was last fed */
  lastAudioTimestamp: number;
  /** Whether we already submitted a final idle attempt */
  idleSubmitted: boolean;
  /** Segmentation closed this buffer while a draft request was in flight:
   *  that response's text covers the pre-trim window — discard it and
   *  resubmit the owned (trimmed) audio as the final window. */
  pendingFinal: boolean;
  /** Samples inherited from a previous speaker via carry-forward */
  carryForwardSamples: number;
  /** Generation counter — incremented on full reset to detect stale responses */
  generation: number;
  /** Last confirmed text — passed as prompt to Whisper for context continuity */
  lastConfirmedText: string;
  /** The text of the OUTSTANDING (unconfirmed) live pending draft, or '' if none.
   *  Set every time onSegmentPending publishes a draft; cleared when that draft is
   *  finalized (re-emitted confirmed under the same id) or the buffer fully resets.
   *  A turn-close uses it to FINALIZE the draft so it never lingers as completed:false. */
  pendingDraftText: string;
  /** STT-detected (or invocation-forced, echoed by the service) language of the most recently
   *  accepted transcription result — stamped on every segment this buffer emits. Undefined until
   *  the first accepted result: undetected stays honest (never defaulted to 'en' here). */
  lastLanguage?: string;
  /** windowStartMs the outstanding pending draft was published under — its segment id.
   *  Finalizing re-emits the confirmed under THIS exact start so the consumer's
   *  upsert-by-id replaces the pending row (rather than appending a new id and
   *  leaving the draft dangling). */
  pendingDraftStartMs: number;
}

export interface SpeakerStreamManagerConfig {
  /** Minimum unconfirmed audio before submission (seconds). Default: 2 */
  minAudioDuration?: number;
  /** Interval between submissions (seconds). Default: 2 */
  submitInterval?: number;
  /** Consecutive matches to confirm. Default: 2 */
  confirmThreshold?: number;
  /** Max total buffer size before force-flush (seconds). Default: 30 */
  maxBufferDuration?: number;
  /** Idle timeout — emit and reset after this many seconds of no audio. Default: 15 */
  idleTimeoutSec?: number;
  /** Sample rate. Default: 16000 */
  sampleRate?: number;
  /** #617: don't submit a window whose RMS energy is below this to Whisper — near-silent audio
   *  yields "YouTube-outro" hallucinations. Conservative default (well under speech) so it only
   *  drops true silence; the phrase-list filter is the language-agnostic backstop. Default: 0.0025 */
  silenceRmsThreshold?: number;
}

/** Root-mean-square energy of a PCM window in [-1,1]; the near-silent oracle (#617). */
export function rms(samples: Float32Array): number {
  if (samples.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
  return Math.sqrt(sum / samples.length);
}

export class SpeakerStreamManager {
  private buffers: Map<string, SpeakerBuffer> = new Map();
  private timers: Map<string, ReturnType<typeof setInterval>> = new Map();
  private minAudioDuration: number;
  private submitInterval: number;
  private confirmThreshold: number;
  private maxBufferDuration: number;
  private idleTimeoutSec: number;
  private sampleRate: number;
  private silenceRmsThreshold: number;
  /** Audio carried forward from a flushed short segment — prepended to the next feedAudio call */
  private carryForward: Float32Array[] = [];
  /** Generation at time of last submission — used to detect stale responses after fullReset */
  private submitGeneration: Map<string, number> = new Map();

  /** Called when unconfirmed audio needs transcription. */
  onSegmentReady: ((speakerId: string, speakerName: string, audioBuffer: Float32Array) => void) | null = null;

  /** Called when a segment is confirmed and should be published. */
  onSegmentConfirmed: ((speakerId: string, speakerName: string, transcript: string, bufferStartMs: number, bufferEndMs: number, segmentId: string, language?: string) => void) | null = null;

  /** Called with the UNCONFIRMED forming tail after each submission (the "pending"
   *  draft) — parity with ChunkedTranscriber's publishPending, so the multistream
   *  (gmeet per-participant) path shows a live forming tail like the mixed path,
   *  not just confirmed text. Empty string ⇒ clear the speaker's draft. */
  onSegmentPending: ((speakerId: string, speakerName: string, text: string, bufferStartMs: number, language?: string) => void) | null = null;

  constructor(config?: SpeakerStreamManagerConfig) {
    this.minAudioDuration = config?.minAudioDuration ?? 2;
    this.submitInterval = config?.submitInterval ?? 2;
    this.confirmThreshold = config?.confirmThreshold ?? 2;
    this.maxBufferDuration = config?.maxBufferDuration ?? 30;
    this.idleTimeoutSec = config?.idleTimeoutSec ?? 15;
    this.sampleRate = config?.sampleRate ?? 16000;
    this.silenceRmsThreshold = config?.silenceRmsThreshold ?? 0.0025;
  }

  addSpeaker(speakerId: string, speakerName: string): void {
    if (this.buffers.has(speakerId)) return;

    const now = Date.now();
    this.buffers.set(speakerId, {
      speakerId,
      speakerName,
      chunks: [],
      totalSamples: 0,
      confirmedSamples: 0,
      lastTranscript: '',
      confirmCount: 0,
      lastWords: [],
      inFlight: false,
      windowStartMs: now,
      bufferStartMs: now,
      sequenceNumber: 0,
      lastAudioTimestamp: now,
      idleSubmitted: false,
      pendingFinal: false,
      carryForwardSamples: 0,
      generation: 0,
      lastConfirmedText: '',
      pendingDraftText: '',
      pendingDraftStartMs: now,
    });

    const timer = setInterval(() => this.trySubmit(speakerId), this.submitInterval * 1000);
    this.timers.set(speakerId, timer);

    log(`[SpeakerStreams] Added speaker "${speakerName}" (${speakerId})`);
  }

  /**
   * @param atMs Optional wall-clock time the audio was actually SPOKEN
   *             (turn start). Batch feeders (MixedAudioPipeline turns arrive
   *             seconds after speech, all at once) pass it so published
   *             segment times reflect speech time, not feed time. Live
   *             streamers omit it (feed time ≈ speech time).
   */
  feedAudio(speakerId: string, audioData: Float32Array, atMs?: number): void {
    const buffer = this.buffers.get(speakerId);
    if (!buffer) return;

    // Gap guard for batch feeders: turns of one speaker arrive separated by
    // other speakers' turns. Concatenating non-contiguous audio into one
    // buffer maps Whisper offsets onto a gapless timeline — the second turn's
    // words get stamped near the first turn's end instead of when they were
    // actually spoken, shuffling cross-speaker order. If the new audio is not
    // contiguous with what's buffered (>2s gap), flush the buffer first so
    // each contiguous stretch keeps a truthful time base.
    if (atMs !== undefined && buffer.totalSamples > 0) {
      const bufferedEndMs = buffer.windowStartMs + (buffer.totalSamples / this.sampleRate) * 1000;
      if (atMs - bufferedEndMs > 2000) {
        // Detach the buffered stretch and finish it asynchronously on the
        // snapshot, then reset the live buffer NOW — the new turn must never
        // append to (or race with) the old stretch. fullReset() assigns fresh
        // arrays, so the snapshot keeps the old chunks untouched.
        const detached: SpeakerBuffer = { ...buffer };
        this.fullReset(buffer);
        if (detached.lastTranscript) {
          this.emitSegment(detached, detached.lastTranscript);
        } else if (detached.totalSamples - detached.confirmedSamples > 0 && !detached.inFlight) {
          void this.submitBuffer(detached).then(() => {
            if (detached.lastTranscript) this.emitSegment(detached, detached.lastTranscript);
          }).catch((err: any) => {
            // P18 (ADR 0010): fail LOUD + attributed — never drop a transcription failure silently.
            // The typed STT fault carries `.kind` (unauthorized / payment_required / rate_limited / …).
            log(`[SpeakerStreams] [STT-FAULT] ${err?.kind ?? 'error'} on deferred close for ` +
                `"${detached.speakerName}": ${String(err?.message ?? err)} (audio dropped)`);
          });
        }
      }
    }

    // Set window start on first audio after reset — this ensures the segment's
    // start time reflects when the audio was actually spoken, not when the
    // buffer was cleared. Critical for speaker-mapper and cross-speaker order.
    if (buffer.totalSamples === 0) {
      buffer.windowStartMs = atMs ?? Date.now();
      buffer.bufferStartMs = atMs ?? Date.now();
    }

    buffer.chunks.push(audioData);
    buffer.totalSamples += audioData.length;
    buffer.lastAudioTimestamp = Date.now();
    buffer.idleSubmitted = false;
  }

  /**
   * Handle Whisper result. Accepts individual Whisper segments for incremental
   * confirmation — stable leading segments are emitted individually rather than
   * waiting for the entire text to stabilize.
   *
   * @param segments - Whisper segments with text and timing. If empty/undefined,
   *                   falls back to full-text confirmation using transcript param.
   * @param segmentEndSec - end time (seconds) of the last segment Whisper returned,
   *                        relative to the start of the submitted audio.
   * @returns true if the result was accepted into the confirmation pipeline;
   *          false if it was discarded (stale generation, or a deferred-close
   *          finalization superseded it). Callers must NOT publish a pending
   *          draft for a rejected result — its text describes audio this
   *          buffer no longer owns.
   */
  handleTranscriptionResult(speakerId: string, transcript: string, segmentEndSec?: number, segments?: WhisperSegment[], language?: string): boolean {
    const buffer = this.buffers.get(speakerId);
    if (!buffer) return false;

    buffer.inFlight = false;

    // Discard stale responses: if the buffer was reset (generation bumped)
    // while a Whisper request was in flight, this response is for audio that
    // no longer exists. Accepting it would poison lastTranscript with text
    // from a previous segment.
    const submitGen = this.submitGeneration.get(speakerId);
    if (submitGen !== undefined && submitGen < buffer.generation) {
      return false;
    }

    // The window's language (STT-detected, or the forced code the service echoes back). Stamped
    // now — before any emit path below — so the very confirm this result triggers carries it.
    if (language) buffer.lastLanguage = language;

    // Segmentation closed this buffer while this request was in flight: the
    // text covers the pre-trim window (may include the next segment's audio).
    // Discard it and submit the owned audio as the final window.
    if (buffer.pendingFinal) {
      buffer.pendingFinal = false;
      if (this.unconfirmedSamples(buffer) === 0) {
        this.fullReset(buffer);
        return false;
      }
      buffer.idleSubmitted = true;
      log(`[SpeakerStreams] Final resubmit for "${buffer.speakerName}" after deferred close (${(this.unconfirmedSamples(buffer) / this.sampleRate).toFixed(1)}s audio)`);
      void this.submitBuffer(buffer);
      return false;
    }

    if (!transcript || transcript.trim().length === 0) {
      if (buffer.idleSubmitted) {
        // The FINAL re-transcription came back empty (VAD/Whisper drift), but we
        // already showed good text as pending. Finalize THAT instead of dropping
        // the turn — the "pending then lost" loss. emitSegment self-guards.
        if (buffer.lastTranscript) this.emitSegment(buffer, buffer.lastTranscript);
        this.fullReset(buffer);
      }
      return false;
    }

    const trimmed = transcript.trim();

    // Hallucination filter — drop known junk before it enters the confirmation pipeline
    if (isHallucination(trimmed)) {
      log(`[SpeakerStreams] [FILTERED] Hallucination for "${buffer.speakerName}": "${trimmed.substring(0, 60)}"`);
      if (buffer.idleSubmitted) {
        // The FINAL submit hallucinated, but earlier pending text was clean —
        // finalize the last good text rather than losing the whole turn.
        if (buffer.lastTranscript) this.emitSegment(buffer, buffer.lastTranscript);
        this.fullReset(buffer);
      }
      return false;
    }

    // Idle/flush submit — emit immediately, this is the last chance
    if (buffer.idleSubmitted) {
      this.emitSegment(buffer, trimmed);
      this.fullReset(buffer);
      return true;
    }

    // Word-level prefix confirmation (LocalAgreement-2, UFAL whisper_streaming).
    // Instead of comparing segment texts by position (which fails because Whisper
    // re-segments as the buffer grows), we concatenate all segments into words and
    // find the longest common prefix across consecutive submissions. This is robust
    // to segment boundary shifts — only the leading WORDS need to be stable.
    if (segments && segments.length > 0) {
      const currentWords = segments.flatMap(s => s.text.trim().split(/\s+/).filter(w => w.length > 0));
      const prevWords = buffer.lastWords;

      // Longest common word prefix across consecutive submissions (shared core).
      const prefixLen = longestCommonWordPrefix(currentWords, prevWords);

      buffer.lastWords = currentWords;

      // The live draft is published ONCE per submission, as the WHOLE forming window text, by the
      // full-text fall-through below — so a consumer that upserts by segment_id sees a single draft
      // that only grows. Emitting a second draft here (the unconfirmed tail, a shorter fragment under
      // the SAME id `key:round(windowStartMs)`) made the draft alternate full↔fragment → a visible
      // upsert flicker. The whole-window draft is the sole pending representation; confirmed prefixes
      // below are published separately as confirmed segments.

      // Confirm if prefix covers at least 1 word but NOT all current words
      // (trailing words are still forming and may change next submission).
      // With confirmThreshold=2, having a common prefix between 2 consecutive
      // submissions already satisfies the threshold.
      if (prefixLen > 0 && prefixLen < currentWords.length) {
        // Map confirmed prefix words back to full Whisper segments for timestamps.
        // Only emit segments whose words are entirely within the confirmed prefix.
        let wordsRemaining = prefixLen;
        let confirmedSegCount = 0;
        for (const seg of segments) {
          const segWordCount = seg.text.trim().split(/\s+/).filter(w => w.length > 0).length;
          if (wordsRemaining >= segWordCount) {
            wordsRemaining -= segWordCount;
            confirmedSegCount++;
          } else {
            break; // Partial segment — don't emit partial
          }
        }

        if (confirmedSegCount > 0) {
          const baseWindowMs = buffer.windowStartMs;
          for (let i = 0; i < confirmedSegCount; i++) {
            const seg = segments[i];
            buffer.windowStartMs = baseWindowMs + Math.floor(seg.start * 1000);
            const segEndMs = baseWindowMs + Math.floor(seg.end * 1000);
            if (!seg.text.trim() || !this.onSegmentConfirmed) continue;
            if (isHallucination(seg.text.trim())) {
              log(`[SpeakerStreams] [FILTERED] Hallucination segment for "${buffer.speakerName}": "${seg.text.trim().substring(0, 60)}"`);
              continue;
            }
            const segmentId = `${buffer.speakerId}:${buffer.sequenceNumber}`;
            this.onSegmentConfirmed(buffer.speakerId, buffer.speakerName, seg.text.trim(), buffer.windowStartMs, segEndMs, segmentId, buffer.lastLanguage);
            buffer.sequenceNumber++;
            buffer.lastConfirmedText = seg.text.trim();
          }
          const lastConfirmedSeg = segments[confirmedSegCount - 1];
          this.advanceOffset(buffer, lastConfirmedSeg.end);
          buffer.windowStartMs = baseWindowMs + Math.floor(lastConfirmedSeg.end * 1000);
          return true;
        }
      }

      // No prefix confirmed yet — fall through to full-text check
    }

    // Full string match — text must be identical across consecutive submissions.
    // Ensures Whisper has fully stabilized before we confirm and advance the offset.
    if (trimmed === buffer.lastTranscript) {
      buffer.confirmCount++;
    } else {
      buffer.lastTranscript = trimmed;
      buffer.confirmCount = 1;
    }

    if (buffer.confirmCount >= this.confirmThreshold) {
      // CONFIRMED — emit and advance offset to Whisper's segment boundary.
      this.emitSegment(buffer, trimmed);
      this.advanceOffset(buffer, segmentEndSec);
    } else if (this.onSegmentPending) {
      // Still forming — emit the whole draft as pending (no per-segment timing here).
      // Remember it (text + the id it went out under) so a turn-close can FINALIZE it
      // instead of leaving a dangling completed:false.
      buffer.pendingDraftText = trimmed;
      buffer.pendingDraftStartMs = buffer.windowStartMs;
      this.onSegmentPending(buffer.speakerId, buffer.speakerName, trimmed, buffer.windowStartMs, buffer.lastLanguage);
    }
    return true;
  }

  removeSpeaker(speakerId: string): void {
    const timer = this.timers.get(speakerId);
    if (timer) clearInterval(timer);
    this.timers.delete(speakerId);

    const buffer = this.buffers.get(speakerId);
    if (buffer) {
      if (this.unconfirmedSamples(buffer) > 0 && buffer.lastTranscript) {
        this.emitSegment(buffer, buffer.lastTranscript);
      }
      // Finalize any still-outstanding pending draft (emitSegment above may have cleared it; if
      // not — e.g. no unconfirmed audio remained — this confirms it so no completed:false dangles).
      this.finalizePendingDraft(buffer);
    }

    this.buffers.delete(speakerId);
  }

  hasSpeaker(speakerId: string): boolean {
    return this.buffers.has(speakerId);
  }

  updateSpeakerName(speakerId: string, newName: string): boolean {
    const buffer = this.buffers.get(speakerId);
    if (!buffer || buffer.speakerName === newName) return false;
    log(`[SpeakerStreams] Updated speaker name "${buffer.speakerName}" → "${newName}" (${speakerId})`);
    buffer.speakerName = newName;
    return true;
  }

  getSpeakerName(speakerId: string): string | undefined {
    return this.buffers.get(speakerId)?.speakerName;
  }

  getSegmentId(speakerId: string): string {
    const buffer = this.buffers.get(speakerId);
    const seq = buffer?.sequenceNumber ?? 0;
    return `${speakerId}:${seq}`;
  }

  getActiveSpeakers(): string[] {
    return Array.from(this.buffers.keys());
  }

  getBufferStartMs(speakerId: string): number {
    return this.buffers.get(speakerId)?.windowStartMs ?? Date.now();
  }

  getLastConfirmedText(speakerId: string): string {
    return this.buffers.get(speakerId)?.lastConfirmedText ?? '';
  }

  removeAll(): void {
    for (const speakerId of Array.from(this.buffers.keys())) {
      this.removeSpeaker(speakerId);
    }
  }

  /**
   * Force-flush on speaker change. If enough audio, emit and full reset.
   * If too short, keep chunks for the speaker's next turn.
   */
  /**
   * @param force - if true, flush regardless of minAudioDuration (end-of-stream)
   * @param trimAtMs - segmentation boundary (audio-time ms): audio after this
   *                   belongs to the NEXT segment buffer (the pipeline re-feeds
   *                   it there) — drop it here so the same frames are never
   *                   transcribed under both segments.
   */
  async flushSpeaker(speakerId: string, force: boolean = false, trimAtMs?: number): Promise<void> {
    const buffer = this.buffers.get(speakerId);
    if (!buffer) return;

    if (trimAtMs !== undefined) this.trimTailAfter(buffer, trimAtMs);
    if (buffer.totalSamples === 0) {
      // No audio owned, but a pending draft may still be dangling (the live "stale pending"
      // case: a draft formed, the window was trimmed/confirmed to empty, then the turn closes).
      // Finalize it so it doesn't linger as completed:false.
      this.finalizePendingDraft(buffer);
      this.fullReset(buffer);
      return;
    }

    const unconfirmedSec = this.unconfirmedSamples(buffer) / this.sampleRate;

    // Short audio on speaker change: submit to Whisper directly rather than
    // carry-forward. Carry-forward shifts word timestamps relative to the next
    // speaker's buffer start, which makes the speaker-mapper unable to attribute
    // carried words correctly. Direct submission preserves correct timing.

    // Audio the draft does not cover — transcribe it. The draft is the FALLBACK for when this
    // final submission yields nothing (handleTranscriptionResult re-emits it on an empty or
    // hallucinated response), never the first choice: emitting it here publishes the draft's few
    // words stamped with the buffer's FULL extent, so the turn's remaining audio disappears
    // behind a segment that looks complete to every consumer downstream.
    if (this.unconfirmedSamples(buffer) > 0) {
      if (buffer.inFlight) {
        // A draft request is in flight for the PRE-TRIM window. Discarding
        // the buffer here loses the whole segment's audio (multi-second
        // transcript holes). Instead: when the response lands, its text is
        // discarded and the owned audio resubmitted as the final window.
        buffer.pendingFinal = true;
        log(`[SpeakerStreams] Close while in-flight for "${buffer.speakerName}" — finalize deferred to response (${unconfirmedSec.toFixed(1)}s audio held)`);
        return;
      }
      buffer.idleSubmitted = true;
      log(`[SpeakerStreams] Flush-submit for "${buffer.speakerName}" (${unconfirmedSec.toFixed(1)}s audio owed)`);
      await this.submitBuffer(buffer);
      return;
    }

    // Nothing owed — the draft IS the whole turn.
    if (buffer.lastTranscript) {
      this.emitSegment(buffer, buffer.lastTranscript);
      this.fullReset(buffer);
      return;
    }

    this.finalizePendingDraft(buffer);
    this.fullReset(buffer);
  }

  // ── Private ──────────────────────────────────────────────────

  /**
   * TURN-CLOSE finalize: if the speaker has an OUTSTANDING unconfirmed pending draft,
   * confirm it (emit via onSegmentConfirmed) under the SAME segment id it was published
   * under, then CLEAR the draft (onSegmentPending "") so NO completed:false lingers.
   *
   * This is the source-side fix for the "stale pending" bug: the draft was published
   * under id `key:round(pendingDraftStartMs)`; if the turn closes via a path that emits
   * the final confirmed segment under a DIFFERENT id (windowStartMs advanced), or that
   * doesn't emit at all (no audio buffered, or emit deduped to a no-op), the consumer's
   * upsert-by-id never replaces the draft and it dangles forever. Finalizing under the
   * draft's own id guarantees the upsert replaces it; the explicit clear is belt-and-
   * suspenders so no completed:false survives even if the confirm is filtered/deduped.
   *
   * Idempotent: clears pendingDraftText, so a second call is a no-op. Callers invoke it
   * before fullReset on every close path.
   */
  private finalizePendingDraft(buffer: SpeakerBuffer): void {
    const text = buffer.pendingDraftText;
    if (!text) return;
    const startMs = buffer.pendingDraftStartMs;
    // Clear the tracking FIRST so emitSegment (called below) doesn't recurse/re-finalize.
    buffer.pendingDraftText = '';

    // Finalize: emit the draft text as a CONFIRMED segment under the draft's own id, so a
    // consumer upserting by id replaces the pending row in place. We bypass emitSegment's
    // dedup-vs-lastConfirmedText guard on purpose — the pending row carries this exact text
    // as completed:false and MUST be replaced by a completed:true row of the same id.
    if (text !== buffer.lastConfirmedText && this.onSegmentConfirmed && !isHallucination(text)) {
      const endMs = buffer.totalSamples > 0
        ? buffer.windowStartMs + (buffer.totalSamples / this.sampleRate) * 1000
        : startMs;
      this.onSegmentConfirmed(buffer.speakerId, buffer.speakerName, text, startMs, endMs, `${buffer.speakerId}:${buffer.sequenceNumber}`, buffer.lastLanguage);
      buffer.sequenceNumber++;
      buffer.lastConfirmedText = text;
    } else if (this.onSegmentPending) {
      // The text already went out confirmed (or is junk) — just CLEAR the dangling draft row
      // so no completed:false survives. Empty string ⇒ consumer drops the draft.
      this.onSegmentPending(buffer.speakerId, buffer.speakerName, '', startMs);
    }
  }

  private unconfirmedSamples(buffer: SpeakerBuffer): number {
    return buffer.totalSamples - buffer.confirmedSamples;
  }

  private async trySubmit(speakerId: string): Promise<void> {
    const buffer = this.buffers.get(speakerId);
    if (!buffer || buffer.inFlight) return;

    const unconfirmedSec = this.unconfirmedSamples(buffer) / this.sampleRate;
    const totalSec = buffer.totalSamples / this.sampleRate;
    const idleMs = Date.now() - buffer.lastAudioTimestamp;

    // Idle timeout
    if (idleMs > this.idleTimeoutSec * 1000 && this.unconfirmedSamples(buffer) > 0) {
      if (!buffer.idleSubmitted) {
        buffer.idleSubmitted = true;
        log(`[SpeakerStreams] Idle submit for "${buffer.speakerName}" (${(idleMs/1000).toFixed(1)}s idle, final submission)`);
        await this.submitBuffer(buffer);
        return;
      }
      if (!buffer.inFlight) {
        if (buffer.lastTranscript) {
          this.emitSegment(buffer, buffer.lastTranscript);
        }
        log(`[SpeakerStreams] Idle cleanup for "${buffer.speakerName}" (${(idleMs/1000).toFixed(1)}s idle)`);
        this.fullReset(buffer);
        return;
      }
      return;
    }

    // Buffer too large — force-flush or trim
    if (totalSec > this.maxBufferDuration) {
      if (buffer.confirmedSamples === 0) {
        // Nothing confirmed — confirmation never triggered. Force-flush whatever
        // transcript we have to prevent monolith segments (e.g. 120s+ buffer).
        if (buffer.lastTranscript) {
          log(`[SpeakerStreams] Hard cap force-flush for "${buffer.speakerName}" (${totalSec.toFixed(1)}s > ${this.maxBufferDuration}s, no confirmation)`);
          this.emitSegment(buffer, buffer.lastTranscript);
        }
        this.fullReset(buffer);
        return;
      }
      this.trimBuffer(buffer);
    }

    // Submit if enough unconfirmed audio
    if (unconfirmedSec >= this.minAudioDuration) {
      await this.submitBuffer(buffer);
    }
  }

  /**
   * Submit only the UNCONFIRMED portion of the buffer to Whisper.
   * Audio before confirmedSamples has already been transcribed and emitted.
   * Near-silent windows (RMS < silenceRmsThreshold) are NOT submitted (#617) — silence yields
   * hallucinated boilerplate, so it never reaches Whisper.
   */
  private async submitBuffer(buffer: SpeakerBuffer): Promise<void> {
    const unconfirmed = this.unconfirmedSamples(buffer);
    if (unconfirmed === 0 || !this.onSegmentReady) return;

    // Build audio from confirmedSamples onward
    const combined = new Float32Array(unconfirmed);
    let dstOffset = 0;
    let samplesToSkip = buffer.confirmedSamples;

    for (const chunk of buffer.chunks) {
      if (samplesToSkip >= chunk.length) {
        samplesToSkip -= chunk.length;
        continue;
      }
      const start = samplesToSkip;
      samplesToSkip = 0;
      const toCopy = chunk.length - start;
      combined.set(chunk.subarray(start), dstOffset);
      dstOffset += toCopy;
    }

    // #617: near-silent guard. faster-whisper emits "YouTube-outro" boilerplate on silence
    // (ご視聴… / Abone… / "thanks for watching"), which then rides a phantom speaker with
    // out-of-order timestamps. This method's docstring long PROMISED "skips Whisper if silence" —
    // it was never implemented. Skip the submission (never set inFlight): the buffer stays, so a
    // later louder window submits normally, and the idle path (trySubmit) still emits any earlier
    // lastTranscript and resets. The phrase-list filter remains the language-agnostic backstop.
    if (rms(combined) < this.silenceRmsThreshold) {
      log(`[SpeakerStreams] [SILENT-SKIP] "${buffer.speakerName}" ${(unconfirmed / this.sampleRate).toFixed(1)}s window ` +
          `below RMS ${this.silenceRmsThreshold} — not submitting (no hallucination surface)`);
      return;
    }

    buffer.inFlight = true;
    this.submitGeneration.set(buffer.speakerId, buffer.generation);

    try {
      this.onSegmentReady(buffer.speakerId, buffer.speakerName, combined);
    } catch (err: any) {
      buffer.inFlight = false;
      // P18: surface the transcription submit failure with its typed kind — do not swallow it.
      log(`[SpeakerStreams] [STT-FAULT] ${err?.kind ?? 'error'} submitting for ` +
          `"${buffer.speakerName}": ${String(err?.message ?? err)}`);
    }
  }

  /**
   * Emit a confirmed segment. Does NOT reset the buffer — just publishes.
   */
  private emitSegment(buffer: SpeakerBuffer, text: string): void {
    if (!text || !this.onSegmentConfirmed) return;
    if (isHallucination(text)) {
      log(`[SpeakerStreams] [FILTERED] Hallucination in emit for "${buffer.speakerName}": "${text.substring(0, 60)}"`);
      return;
    }
    // Dedup: don't re-emit the same text that was just confirmed (acoustic echo / residual audio)
    if (text === buffer.lastConfirmedText) {
      log(`[SpeakerStreams] Dedup skip for "${buffer.speakerName}": "${text.substring(0, 50)}" (same as last confirmed)`);
      // The text is already confirmed, but a pending draft of it may still be dangling under a
      // DIFFERENT id — clear it so no completed:false lingers (the stale-pending bug's dedup case).
      this.clearStaleDraft(buffer, buffer.windowStartMs);
      return;
    }
    // Audio-time end via the buffer's gapless timeline — NOT Date.now(),
    // which is submit/commit ARRIVAL time and overstates the span by the
    // whole commit lag (segments then visually overlap their successors).
    const endMs = buffer.totalSamples > 0
      ? buffer.windowStartMs + (buffer.totalSamples / this.sampleRate) * 1000
      : Date.now();
    const segmentId = `${buffer.speakerId}:${buffer.sequenceNumber}`;
    this.onSegmentConfirmed(buffer.speakerId, buffer.speakerName, text, buffer.windowStartMs, endMs, segmentId, buffer.lastLanguage);
    buffer.sequenceNumber++;
    buffer.lastConfirmedText = text;
    // This confirmed segment supersedes the outstanding pending draft. If it went out under a
    // DIFFERENT id than the draft (windowStartMs advanced), the consumer's upsert-by-id won't
    // replace the draft — clear the stale draft row explicitly so it can't dangle as completed:false.
    this.clearStaleDraft(buffer, buffer.windowStartMs);
  }

  /**
   * Reconcile the outstanding pending draft after a confirmed emit at `confirmedStartMs`.
   * If the draft was published under a DIFFERENT id, emit an empty pending to drop that stale
   * row (the consumer keys drafts by `key:round(startMs)` and treats "" as a delete). Always
   * clears the in-memory tracking so finalizePendingDraft is a subsequent no-op.
   */
  private clearStaleDraft(buffer: SpeakerBuffer, confirmedStartMs: number): void {
    if (!buffer.pendingDraftText) return;
    const draftStartMs = buffer.pendingDraftStartMs;
    buffer.pendingDraftText = '';
    if (Math.round(draftStartMs) !== Math.round(confirmedStartMs) && this.onSegmentPending) {
      this.onSegmentPending(buffer.speakerId, buffer.speakerName, '', draftStartMs);
    }
  }

  /**
   * Advance the offset to Whisper's segment boundary. Trim confirmed audio.
   * The buffer continues — audio after the segment boundary stays for next submission.
   *
   * @param segmentEndSec - Whisper's last segment end time (seconds relative to
   *                        submitted audio start). If undefined, trims the full
   *                        unconfirmed window (fallback, loses boundary context).
   */
  private advanceOffset(buffer: SpeakerBuffer, segmentEndSec?: number): void {
    if (segmentEndSec !== undefined) {
      // Advance to Whisper's segment boundary — preserves audio context
      // after the boundary for the next submission
      const samplesToAdvance = Math.floor(segmentEndSec * this.sampleRate);
      buffer.confirmedSamples += Math.min(samplesToAdvance, this.unconfirmedSamples(buffer));

      // Keep remaining unconfirmed audio — it may contain real speech that
      // Whisper transcribed but wasn't confirmed yet. It will accumulate with
      // new audio until long enough for the next submission.
    } else {
      // Fallback: trim everything (old behavior, loses boundary words)
      buffer.confirmedSamples = buffer.totalSamples;
    }

    // Trim confirmed chunks from the front to free memory
    this.trimBuffer(buffer);

    // Reset confirmation state for the next segment window
    buffer.lastTranscript = '';
    buffer.confirmCount = 0;
    buffer.lastWords = [];
    buffer.windowStartMs = Date.now();
    // The window moved on; the prior pending draft (under the OLD windowStartMs) is superseded.
    // Drop the in-memory tracking so a later turn-close finalize can't re-emit this stale window's
    // text. We don't clear the consumer's draft row here (mid-stream) to avoid a live-edge flicker —
    // the next submission republishes the forming draft under the new id, and turn-close finalizes it.
    buffer.pendingDraftText = '';

    log(`[SpeakerStreams] Offset advanced for "${buffer.speakerName}" (confirmed=${buffer.confirmedSamples}, total=${buffer.totalSamples}, trimmed to ${buffer.chunks.length} chunks)`);
  }

  /**
   * Drop buffered audio AFTER an audio-time boundary (segmentation close).
   * The buffer's gapless timeline maps samples to windowStartMs + offset, so
   * everything past `tMs` is excess that streamed in during commit lag. Any
   * draft transcript described the untrimmed audio, so it is invalidated —
   * the flush that follows re-submits only the owned window.
   */
  private trimTailAfter(buffer: SpeakerBuffer, tMs: number): void {
    if (buffer.totalSamples === 0) return;
    const keep = Math.floor(((tMs - buffer.windowStartMs) / 1000) * this.sampleRate);
    if (keep >= buffer.totalSamples) return;
    if (keep <= 0) {
      // Boundary predates this window (offset drift or stale commit) — an
      // over-trim would discard real speech; keeping it only risks one
      // duplicated segment. Keep.
      log(`[SpeakerStreams] trimTailAfter skipped for "${buffer.speakerName}": boundary ${tMs} <= windowStart ${buffer.windowStartMs}`);
      return;
    }
    let excess = buffer.totalSamples - keep;
    const droppedSec = excess / this.sampleRate;
    while (excess > 0 && buffer.chunks.length > 0) {
      const last = buffer.chunks[buffer.chunks.length - 1];
      if (last.length <= excess) {
        excess -= last.length;
        buffer.chunks.pop();
      } else {
        buffer.chunks[buffer.chunks.length - 1] = last.subarray(0, last.length - excess);
        excess = 0;
      }
    }
    buffer.totalSamples = keep;
    if (buffer.confirmedSamples > keep) buffer.confirmedSamples = keep;
    buffer.lastTranscript = '';
    buffer.confirmCount = 0;
    buffer.lastWords = [];
    log(`[SpeakerStreams] Boundary trim for "${buffer.speakerName}": dropped ${droppedSec.toFixed(2)}s past segmentation boundary`);
  }

  /**
   * Trim confirmed audio chunks from the front of the buffer.
   * Keeps all unconfirmed audio intact.
   */
  private trimBuffer(buffer: SpeakerBuffer): void {
    if (buffer.confirmedSamples === 0) return;

    let samplesToTrim = buffer.confirmedSamples;
    const newChunks: Float32Array[] = [];

    for (const chunk of buffer.chunks) {
      if (samplesToTrim >= chunk.length) {
        samplesToTrim -= chunk.length;
        continue;
      }
      if (samplesToTrim > 0) {
        // Partial chunk — keep the tail
        newChunks.push(chunk.subarray(samplesToTrim));
        samplesToTrim = 0;
      } else {
        newChunks.push(chunk);
      }
    }

    buffer.chunks = newChunks;
    buffer.totalSamples -= buffer.confirmedSamples;
    buffer.confirmedSamples = 0;
  }

  /**
   * Full reset — discard everything. Used on speaker change and idle cleanup.
   */
  private fullReset(buffer: SpeakerBuffer): void {
    buffer.chunks = [];
    buffer.totalSamples = 0;
    buffer.confirmedSamples = 0;
    buffer.lastTranscript = '';
    buffer.confirmCount = 0;
    buffer.lastWords = [];
    buffer.inFlight = false;
    buffer.windowStartMs = Date.now();
    buffer.bufferStartMs = Date.now();
    buffer.lastAudioTimestamp = Date.now();
    buffer.idleSubmitted = false;
    buffer.pendingFinal = false;
    buffer.carryForwardSamples = 0;
    buffer.generation++;
  }
}
