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
  /** The key a transcription request for this buffer is routed back by (the first argument of
   *  `onSegmentReady`, passed back to `handleTranscriptionResult`). The speaker id until the live
   *  buffer is detached; a detached stretch keeps the key its in-flight request went out under, and
   *  the live buffer continues under a fresh one, so neither response lands on the other's audio. */
  routeKey: string;
  /** A detached stretch: receives no more audio, finishes its own final request, then is forgotten. */
  retired: boolean;
  /** Generation this buffer's outstanding request was issued under (stale-response guard). */
  submitGeneration?: number;
  /** Samples trimmed from the front of this stretch so far — maps buffer indices to stretch-absolute
   *  ones for the coverage counter. */
  absBase: number;
  /** Stretch-absolute sample index up to which audio has been submitted at least once. */
  submittedAbsEnd: number;
  speakerName: string;
  chunks: Float32Array[];
  /** Capture time (ms) of each chunk's first sample, parallel to `chunks`. Segment and window times
   *  are read from it only with `callbackStampedFrames`. */
  chunkStartMs: number[];
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
  /** Caller-clock end (ms) of the most recently fed stamped frame. Read by the input-gap guard
   *  only when the feeder stamps frames at capture-callback time (`callbackStampedFrames`). */
  lastFedEndMs?: number;
  /** Buffered-sample index the latest transcription request covered up to (its window end). */
  submittedEndSample?: number;
  /** Window end of the request whose text is `lastTranscript`. Read by the turn-close flush only
   *  with `callbackStampedFrames`: the flush emits that text only when it covers every buffered sample. */
  lastTranscriptEndSample?: number;
  /** A hard-cap request is in flight for the window ending at this buffered-sample index
   *  (`callbackStampedFrames` only); its response finalizes that window and keeps the rest. */
  capCutSample?: number;
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
  /** The feeder stamps every frame at capture-callback time, so consecutive frames can be spaced
   *  wider than the audio they carry when callbacks arrive late or are lost (Zoom per-track capture
   *  on a starved page, #1774). The input-gap guard then measures from the end of the PREVIOUS
   *  frame, on that one clock, instead of from `windowStartMs + buffered samples`, which such a
   *  feeder outruns by construction. A turn close also emits the last transcript only when that
   *  transcript covers every buffered sample, and otherwise submits the whole window as the final
   *  one. Segment and window times are read from each frame's own stamp rather than from a gapless
   *  sample count, so a turn whose frames cover only part of its wall time keeps its timeline. At the
   *  hard cap, the window up to its quietest late frame is finalized from its own request and the
   *  audio after that cut stays buffered. Opt-in; default false keeps every other feeder's behaviour. */
  callbackStampedFrames?: boolean;
  /** A FINAL window (turn close, idle, input-gap detach) shorter than this is not submitted: STT
   *  answers sub-second fragments with phrases like "Thank you." that the hallucination filter then
   *  discards anyway. The skipped seconds are counted as `shortSec`, never silently. Default: 1 */
  minFinalAudioSec?: number;
  /** Bound on the unconfirmed audio one speaker may hold while its request is in flight (seconds).
   *  Slow STT never drops audio below it; past it new frames are dropped and logged loudly with the
   *  seconds lost. Default: 300 */
  maxHeldAudioSec?: number;
}

/** Seconds of audio per outcome, for one manager (one meeting's lane). `captured` is every fed
 *  sample; `submitted` counts each sample once however many requests it rode in; the remaining
 *  buckets say where captured audio ended up once its stretch was finished. */
export interface CoverageStats {
  capturedSec: number;
  submittedSec: number;
  transcribedSec: number;
  /** Silent, or STT returned nothing / only a filtered hallucination for it. */
  noSpeechSec: number;
  /** Final windows under `minFinalAudioSec`, not submitted. */
  shortSec: number;
  /** Lost: an STT fault after retries, the held-audio cap, or removal with untranscribed audio. */
  droppedSec: number;
}

type Outcome = 'transcribed' | 'no-speech' | 'short' | 'dropped' | 'moved';

/** Root-mean-square energy of a PCM window in [-1,1]; the near-silent oracle (#617). */
export function rms(samples: Float32Array): number {
  if (samples.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
  return Math.sqrt(sum / samples.length);
}

export class SpeakerStreamManager {
  /** Detached stretches one speaker may have finishing at once; past it the live window extends. */
  static readonly MAX_DETACHED_PER_SPEAKER = 4;
  private buffers: Map<string, SpeakerBuffer> = new Map();
  private timers: Map<string, ReturnType<typeof setInterval>> = new Map();
  private minAudioDuration: number;
  private submitInterval: number;
  private confirmThreshold: number;
  private maxBufferDuration: number;
  private idleTimeoutSec: number;
  private sampleRate: number;
  private silenceRmsThreshold: number;
  private callbackStampedFrames: boolean;
  private minFinalAudioSec: number;
  private maxHeldAudioSec: number;
  /** Every buffer that can still receive a transcription response, by route key: the live buffers
   *  and the detached stretches still finishing. */
  private routes: Map<string, SpeakerBuffer> = new Map();
  private routeSeq = 0;
  private stats = { captured: 0, submitted: 0, transcribed: 0, noSpeech: 0, short: 0, dropped: 0 };
  /** Rate limit for the held-audio-cap loss line (ms timestamp of the last one). */
  private lastCapLossLogMs = 0;
  private capLossSinceLog = 0;
  /** Audio carried forward from a flushed short segment — prepended to the next feedAudio call */
  private carryForward: Float32Array[] = [];

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
    this.callbackStampedFrames = config?.callbackStampedFrames ?? false;
    this.minFinalAudioSec = config?.minFinalAudioSec ?? 1;
    this.maxHeldAudioSec = config?.maxHeldAudioSec ?? 300;
  }

  /** Seconds of audio captured / submitted / transcribed / skipped / dropped so far. */
  coverage(): CoverageStats {
    const sec = (n: number) => Math.round((n / this.sampleRate) * 10) / 10;
    return {
      capturedSec: sec(this.stats.captured),
      submittedSec: sec(this.stats.submitted),
      transcribedSec: sec(this.stats.transcribed),
      noSpeechSec: sec(this.stats.noSpeech),
      shortSec: sec(this.stats.short),
      droppedSec: sec(this.stats.dropped),
    };
  }

  /** True when the speaker's turn owes nothing more: no request in flight, no deferred final, no
   *  unconfirmed audio, and no detached stretch of it still finishing. A closed turn is removed only
   *  once settled, so a slow STT answer always finds its buffer. */
  isSettled(speakerId: string): boolean {
    for (const b of this.routes.values()) {
      if (b.speakerId !== speakerId) continue;
      if (b.retired) return false;
      if (b.inFlight || b.pendingFinal || this.unconfirmedSamples(b) > 0) return false;
    }
    return true;
  }

  addSpeaker(speakerId: string, speakerName: string): void {
    if (this.buffers.has(speakerId)) return;

    const now = Date.now();
    // A detached stretch of an earlier speaker under this id may still be finishing under the bare
    // id; the new buffer then takes a fresh route so neither answer lands on the other's audio.
    const routeKey = this.routes.has(speakerId) ? `${speakerId}#${++this.routeSeq}` : speakerId;
    const fresh: SpeakerBuffer = {
      speakerId,
      routeKey,
      retired: false,
      absBase: 0,
      submittedAbsEnd: 0,
      speakerName,
      chunks: [],
      chunkStartMs: [],
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
    };
    this.buffers.set(speakerId, fresh);
    this.routes.set(routeKey, fresh);

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
    //
    // A callback-stamped feeder (Zoom per-track) is measured frame to frame on its own clock: this
    // frame's stamp against the end of the previous frame. Its stamps outrun windowStartMs +
    // buffered samples whenever capture callbacks arrive late or are lost, so the sample-count
    // comparison fired every few seconds inside one continuous turn on a starved page.
    if (atMs !== undefined && buffer.totalSamples > 0) {
      const prevEndMs = this.callbackStampedFrames && buffer.lastFedEndMs !== undefined
        ? buffer.lastFedEndMs
        : buffer.windowStartMs + (buffer.totalSamples / this.sampleRate) * 1000;
      if (atMs - prevEndMs > 2000) {
        const heldSec = this.unconfirmedSamples(buffer) / this.sampleRate;
        const covered = !!buffer.lastTranscript && this.lastTranscriptCoversBuffer(buffer);
        const detachedNow = [...this.routes.values()].filter((b) => b.retired && b.speakerId === speakerId).length;
        const outcome = covered ? 'emitting its last transcript'
          : heldSec <= 0 ? 'nothing unconfirmed'
          : detachedNow >= SpeakerStreamManager.MAX_DETACHED_PER_SPEAKER ? 'extending the window: earlier stretches still finishing'
          : buffer.inFlight ? 'finalizing it after the request in flight'
          : 'submitting it';
        log(`[SpeakerStreams] Input gap for "${buffer.speakerName}": ${((atMs - prevEndMs) / 1000).toFixed(1)}s — ` +
            `detaching ${heldSec.toFixed(1)}s of buffered audio (${outcome})`);
        if (covered) {
          this.emitSegment(buffer, buffer.lastTranscript);
          this.fullReset(buffer, 'transcribed');
        } else if (heldSec <= 0) {
          this.fullReset(buffer, 'moved');
        } else if (detachedNow < SpeakerStreamManager.MAX_DETACHED_PER_SPEAKER) {
          // Detach the buffered stretch and finish it on its own (final request now, or once the
          // request in flight answers), then continue the live buffer from empty — the new turn must
          // never append to the old stretch. Audio is never dropped because a request is in flight.
          const detached = this.detach(buffer);
          if (detached.inFlight) detached.pendingFinal = true;
          else void this.finalSubmit(detached, 'input-gap detach');
        }
        // else: back-pressure — too many stretches still finishing; keep appending to this window
        // (it is submitted whole once its request answers) rather than discarding anything.
      }
    }

    this.stats.captured += audioData.length;
    // Bounded hold: a request that never answers must not grow the buffer without limit. Past the
    // cap the new frame is dropped — loudly, with the seconds lost (P18).
    if (buffer.inFlight && (this.unconfirmedSamples(buffer) + audioData.length) / this.sampleRate > this.maxHeldAudioSec) {
      this.stats.dropped += audioData.length;
      this.capLossSinceLog += audioData.length;
      const now = Date.now();
      if (now - this.lastCapLossLogMs > 5000) {
        log(`[SpeakerStreams] [AUDIO-DROPPED] "${buffer.speakerName}": ${(this.capLossSinceLog / this.sampleRate).toFixed(1)}s ` +
            `of new audio dropped — ${this.maxHeldAudioSec}s already held behind a request that has not answered ` +
            `(total dropped ${(this.stats.dropped / this.sampleRate).toFixed(1)}s)`, 'error');
        this.lastCapLossLogMs = now;
        this.capLossSinceLog = 0;
      }
      return;
    }

    // Set window start on first audio after reset — this ensures the segment's
    // start time reflects when the audio was actually spoken, not when the
    // buffer was cleared. Critical for speaker-mapper and cross-speaker order.
    if (buffer.totalSamples === 0) {
      buffer.windowStartMs = atMs ?? Date.now();
      buffer.bufferStartMs = atMs ?? Date.now();
    }

    // A stamped frame starts at its stamp; an unstamped one continues the buffered audio.
    buffer.chunkStartMs.push(atMs ?? this.bufferedEndMs(buffer));
    buffer.chunks.push(audioData);
    buffer.totalSamples += audioData.length;
    if (atMs !== undefined) buffer.lastFedEndMs = atMs + (audioData.length / this.sampleRate) * 1000;
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
    // `speakerId` is the route key `onSegmentReady` handed out (the speaker id for a buffer that was
    // never detached), so a detached stretch's answer reaches that stretch, not the live buffer.
    const buffer = this.routes.get(speakerId) ?? this.buffers.get(speakerId);
    if (!buffer) return false;

    buffer.inFlight = false;

    // Discard stale responses: if the buffer was reset (generation bumped)
    // while a Whisper request was in flight, this response is for audio that
    // no longer exists. Accepting it would poison lastTranscript with text
    // from a previous segment.
    const submitGen = buffer.submitGeneration;
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
      buffer.capCutSample = undefined;
      if (this.unconfirmedSamples(buffer) === 0) {
        this.fullReset(buffer, 'moved');
        return false;
      }
      log(`[SpeakerStreams] Final resubmit for "${buffer.speakerName}" after deferred close (${(this.unconfirmedSamples(buffer) / this.sampleRate).toFixed(1)}s audio)`);
      void this.finalSubmit(buffer, 'deferred close');
      return false;
    }

    // A hard-cap request answers for the window up to its cut, not for the whole buffer.
    if (buffer.capCutSample !== undefined) {
      this.finishCappedWindow(buffer, transcript);
      return false;
    }

    if (!transcript || transcript.trim().length === 0) {
      if (buffer.idleSubmitted) {
        // The FINAL re-transcription came back empty (VAD/Whisper drift), but we
        // already showed good text as pending. Finalize THAT instead of dropping
        // the turn — the "pending then lost" loss. emitSegment self-guards.
        if (buffer.lastTranscript) this.emitSegment(buffer, buffer.lastTranscript);
        this.fullReset(buffer, buffer.lastTranscript ? 'transcribed' : 'no-speech');
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
        this.fullReset(buffer, buffer.lastTranscript ? 'transcribed' : 'no-speech');
      }
      return false;
    }

    // Idle/flush submit — emit immediately, this is the last chance
    if (buffer.idleSubmitted) {
      this.emitSegment(buffer, trimmed);
      this.fullReset(buffer, 'transcribed');
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
          const baseSample = buffer.confirmedSamples;
          // Whisper offsets count submitted samples; a callback-stamped buffer maps them through each
          // frame's stamp, every other buffer through its gapless window.
          const timeAtOffset = (sec: number): number => this.callbackStampedFrames
            ? this.captureTimeAt(buffer, baseSample + Math.floor(sec * this.sampleRate))
            : baseWindowMs + Math.floor(sec * 1000);
          for (let i = 0; i < confirmedSegCount; i++) {
            const seg = segments[i];
            buffer.windowStartMs = timeAtOffset(seg.start);
            const segEndMs = timeAtOffset(seg.end);
            if (!seg.text.trim() || !this.onSegmentConfirmed) continue;
            if (isHallucination(seg.text.trim())) {
              log(`[SpeakerStreams] [FILTERED] Hallucination segment for "${buffer.speakerName}": "${seg.text.trim().substring(0, 60)}"`);
              continue;
            }
            const segmentId = `${buffer.speakerId}:${buffer.sequenceNumber}`;
            this.onSegmentConfirmed(buffer.speakerId, buffer.speakerName, seg.text.trim(), buffer.windowStartMs, segEndMs, segmentId, buffer.lastLanguage);
            this.clearStaleDraft(buffer, buffer.windowStartMs);
            buffer.sequenceNumber++;
            buffer.lastConfirmedText = seg.text.trim();
          }
          const lastConfirmedSeg = segments[confirmedSegCount - 1];
          this.advanceOffset(buffer, lastConfirmedSeg.end);
          if (!this.callbackStampedFrames) buffer.windowStartMs = baseWindowMs + Math.floor(lastConfirmedSeg.end * 1000);
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
    buffer.lastTranscriptEndSample = buffer.submittedEndSample;

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
      const held = this.unconfirmedSamples(buffer);
      if (held > 0 && buffer.lastTranscript) {
        this.emitSegment(buffer, buffer.lastTranscript);
        this.account(buffer, 'transcribed', held);
      } else if (held > 0) {
        this.account(buffer, 'dropped', held, buffer.inFlight ? 'removed while its request was still in flight'
          : 'removed with untranscribed audio');
      }
      // Finalize any still-outstanding pending draft (emitSegment above may have cleared it; if
      // not — e.g. no unconfirmed audio remained — this confirms it so no completed:false dangles).
      this.finalizePendingDraft(buffer);
      if (this.routes.get(buffer.routeKey) === buffer) this.routes.delete(buffer.routeKey);
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
    return (this.routes.get(speakerId) ?? this.buffers.get(speakerId))?.lastConfirmedText ?? '';
  }

  removeAll(): void {
    for (const speakerId of Array.from(this.buffers.keys())) {
      this.removeSpeaker(speakerId);
    }
    // Detached stretches still finishing are abandoned with the manager: count them, loudly.
    for (const b of Array.from(this.routes.values())) {
      const held = this.unconfirmedSamples(b);
      if (held > 0) this.account(b, 'dropped', held, 'lane disposed before its detached stretch finished');
    }
    this.routes.clear();
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
      this.fullReset(buffer, 'moved');
      return;
    }

    const unconfirmedSec = this.unconfirmedSamples(buffer) / this.sampleRate;

    // Short audio on speaker change: submit to Whisper directly rather than
    // carry-forward. Carry-forward shifts word timestamps relative to the next
    // speaker's buffer start, which makes the speaker-mapper unable to attribute
    // carried words correctly. Direct submission preserves correct timing.

    // Have transcript — emit and reset. A callback-stamped feeder keeps feeding between requests,
    // so its last transcript can describe only the audio up to the previous request; then the
    // final submit below covers the whole window instead of the newer audio being dropped.
    if (buffer.lastTranscript && this.lastTranscriptCoversBuffer(buffer)) {
      this.emitSegment(buffer, buffer.lastTranscript);
      this.fullReset(buffer, 'transcribed');
      return;
    }

    // Have audio but no transcript — final Whisper submit
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
      log(`[SpeakerStreams] Flush-submit for "${buffer.speakerName}" (${unconfirmedSec.toFixed(1)}s audio, no transcript yet)`);
      await this.finalSubmit(buffer, 'turn close');
      return;
    }

    this.finalizePendingDraft(buffer);
    this.fullReset(buffer, 'moved');
  }

  /** Fault answer for a request (the STT call threw after its retries). A draft keeps its audio —
   *  the next submission carries it again together with what arrived since; a final or deferred
   *  final finishes with what it has and counts the rest as dropped, loudly (P18). */
  handleTranscriptionFault(routeKey: string, fault?: unknown): void {
    const buffer = this.routes.get(routeKey) ?? this.buffers.get(routeKey);
    if (!buffer) return;
    buffer.inFlight = false;
    if (buffer.submitGeneration !== undefined && buffer.submitGeneration < buffer.generation) return;
    const kind = (fault as { kind?: string } | undefined)?.kind ?? 'error';
    if (buffer.pendingFinal) {
      buffer.pendingFinal = false;
      log(`[SpeakerStreams] [STT-FAULT] ${kind} on the draft before a deferred close for "${buffer.speakerName}" — submitting the final window`);
      void this.finalSubmit(buffer, 'deferred close after fault');
      return;
    }
    if (buffer.idleSubmitted || buffer.retired) {
      const held = this.unconfirmedSamples(buffer);
      let kept = 0;
      if (buffer.lastTranscript) {
        this.emitSegment(buffer, buffer.lastTranscript);
        kept = Math.min(held, Math.max(0, (buffer.lastTranscriptEndSample ?? buffer.totalSamples) - buffer.confirmedSamples));
        this.account(buffer, 'transcribed', kept);
      }
      this.account(buffer, 'dropped', held - kept, `STT ${kind} on the final window`);
      this.finalizePendingDraft(buffer);
      this.fullReset(buffer, 'moved');
      return;
    }
    // Draft: audio stays buffered; the next tick resubmits it (merged with newer audio).
  }

  /** Submit the buffer's owned audio as its FINAL window. A window under `minFinalAudioSec` is not
   *  sent (STT answers such fragments with filtered phrases); a silent one is not sent either. Both
   *  finish the window and are counted, never silently lost. */
  private async finalSubmit(buffer: SpeakerBuffer, why: string): Promise<void> {
    const held = this.unconfirmedSamples(buffer);
    if (held <= 0) { this.fullReset(buffer, 'moved'); return; }
    if (held / this.sampleRate < this.minFinalAudioSec) {
      log(`[SpeakerStreams] [SHORT-SKIP] "${buffer.speakerName}" ${(held / this.sampleRate).toFixed(1)}s final window (${why}) ` +
          `under ${this.minFinalAudioSec}s — not submitting (sub-second fragments only produce filtered phrases)`);
      if (buffer.lastTranscript) this.emitSegment(buffer, buffer.lastTranscript);
      this.finalizePendingDraft(buffer);
      this.fullReset(buffer, buffer.lastTranscript ? 'transcribed' : 'short');
      return;
    }
    buffer.idleSubmitted = true;
    if (!(await this.submitBuffer(buffer))) {
      if (buffer.lastTranscript) this.emitSegment(buffer, buffer.lastTranscript);
      this.finalizePendingDraft(buffer);
      this.fullReset(buffer, buffer.lastTranscript ? 'transcribed' : 'no-speech');
    }
  }

  /** Move the live buffer's audio into a detached stretch that finishes on its own, and continue the
   *  live buffer from empty under a fresh route key. Nothing is copied or dropped. */
  private detach(buffer: SpeakerBuffer): SpeakerBuffer {
    const detached: SpeakerBuffer = { ...buffer, retired: true };
    this.routes.set(detached.routeKey, detached);
    this.fullReset(buffer, 'moved');
    buffer.routeKey = `${buffer.speakerId}#${++this.routeSeq}`;
    this.routes.set(buffer.routeKey, buffer);
    return detached;
  }

  /** Count `samples` of a buffer's audio into one outcome bucket; a loss is logged loudly. */
  private account(buffer: SpeakerBuffer, outcome: Outcome, samples: number, reason?: string): void {
    if (samples <= 0 || outcome === 'moved') return;
    if (outcome === 'transcribed') this.stats.transcribed += samples;
    else if (outcome === 'no-speech') this.stats.noSpeech += samples;
    else if (outcome === 'short') this.stats.short += samples;
    else {
      this.stats.dropped += samples;
      log(`[SpeakerStreams] [AUDIO-DROPPED] "${buffer.speakerName}": ${(samples / this.sampleRate).toFixed(1)}s lost — ` +
          `${reason ?? 'unspecified'} (total dropped ${(this.stats.dropped / this.sampleRate).toFixed(1)}s)`, 'error');
    }
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
      const endMs = buffer.totalSamples > 0 ? this.audioEndMs(buffer) : startMs;
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
        log(`[SpeakerStreams] Idle submit for "${buffer.speakerName}" (${(idleMs/1000).toFixed(1)}s idle, final submission)`);
        await this.finalSubmit(buffer, 'idle');
        return;
      }
      if (!buffer.inFlight) {
        if (buffer.lastTranscript) {
          this.emitSegment(buffer, buffer.lastTranscript);
        }
        log(`[SpeakerStreams] Idle cleanup for "${buffer.speakerName}" (${(idleMs/1000).toFixed(1)}s idle)`);
        this.fullReset(buffer, buffer.lastTranscript ? 'transcribed' : 'no-speech');
        return;
      }
      return;
    }

    // Buffer too large — force-flush or trim
    if (totalSec > this.maxBufferDuration) {
      if (buffer.confirmedSamples === 0) {
        // A callback-stamped feeder finalizes the capped window from its own request, cut at the
        // quietest frame of its last 4 s, and keeps the audio after the cut. The force-flush below
        // emits a transcript that misses the audio fed since the last request, then drops it.
        // Every other feeder whose last transcript does NOT cover the buffer takes the same cut: the
        // force-flush below would publish stale text and drop everything fed since (with a slow STT
        // that is most of the window).
        const stale = !(buffer.lastTranscript && this.lastTranscriptCoversBuffer(buffer));
        if (this.callbackStampedFrames || stale) {
          const cut = this.quietCutSample(buffer, 4);
          buffer.capCutSample = cut;
          log(`[SpeakerStreams] Hard cap final-submit for "${buffer.speakerName}" (${totalSec.toFixed(1)}s buffered, ` +
              `${(cut / this.sampleRate).toFixed(1)}s finalized at its quietest frame)`);
          if (await this.submitBuffer(buffer, cut)) return;
          buffer.capCutSample = undefined;    // the capped window is near-silent: fall back
        }
        // Nothing confirmed — confirmation never triggered. Force-flush whatever
        // transcript we have to prevent monolith segments (e.g. 120s+ buffer).
        if (buffer.lastTranscript) {
          log(`[SpeakerStreams] Hard cap force-flush for "${buffer.speakerName}" (${totalSec.toFixed(1)}s > ${this.maxBufferDuration}s, no confirmation)`);
          this.emitSegment(buffer, buffer.lastTranscript);
        }
        this.fullReset(buffer, buffer.lastTranscript ? 'transcribed' : 'no-speech');
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
  private async submitBuffer(buffer: SpeakerBuffer, endSample?: number): Promise<boolean> {
    const end = Math.min(endSample ?? buffer.totalSamples, buffer.totalSamples);
    const unconfirmed = end - buffer.confirmedSamples;
    if (unconfirmed <= 0 || !this.onSegmentReady) return false;

    // Build audio from confirmedSamples up to `end` (the whole buffer unless a cut is given)
    const combined = new Float32Array(unconfirmed);
    let dstOffset = 0;
    let samplesToSkip = buffer.confirmedSamples;

    for (const chunk of buffer.chunks) {
      if (dstOffset >= unconfirmed) break;
      if (samplesToSkip >= chunk.length) {
        samplesToSkip -= chunk.length;
        continue;
      }
      const start = samplesToSkip;
      samplesToSkip = 0;
      const toCopy = Math.min(chunk.length - start, unconfirmed - dstOffset);
      combined.set(chunk.subarray(start, start + toCopy), dstOffset);
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
      return false;
    }

    buffer.submittedEndSample = end;
    buffer.inFlight = true;
    buffer.submitGeneration = buffer.generation;
    // Coverage: each sample counts as submitted once, however many sliding-window requests carry it.
    const absStart = buffer.absBase + buffer.confirmedSamples, absEnd = buffer.absBase + end;
    if (absEnd > Math.max(buffer.submittedAbsEnd, absStart)) {
      this.stats.submitted += absEnd - Math.max(buffer.submittedAbsEnd, absStart);
      buffer.submittedAbsEnd = absEnd;
    }

    try {
      this.onSegmentReady(buffer.routeKey, buffer.speakerName, combined);
    } catch (err: any) {
      buffer.inFlight = false;
      // P18: surface the transcription submit failure with its typed kind — do not swallow it.
      log(`[SpeakerStreams] [STT-FAULT] ${err?.kind ?? 'error'} submitting for ` +
          `"${buffer.speakerName}": ${String(err?.message ?? err)}`);
    }
    return true;
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
    const endMs = buffer.totalSamples > 0 ? this.audioEndMs(buffer) : Date.now();
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
      const advanced = Math.min(samplesToAdvance, this.unconfirmedSamples(buffer));
      this.account(buffer, 'transcribed', advanced);
      buffer.confirmedSamples += advanced;

      // Keep remaining unconfirmed audio — it may contain real speech that
      // Whisper transcribed but wasn't confirmed yet. It will accumulate with
      // new audio until long enough for the next submission.
    } else {
      // Fallback: trim everything (old behavior, loses boundary words)
      this.account(buffer, 'transcribed', this.unconfirmedSamples(buffer));
      buffer.confirmedSamples = buffer.totalSamples;
    }

    // The retained audio starts exactly after the samples removed, independent
    // of when STT finished. Clamp to actual buffered audio, including on fallback.
    const advancedMs = (buffer.confirmedSamples / this.sampleRate) * 1000;
    const retainedStartMs = this.callbackStampedFrames ? this.captureTimeAt(buffer, buffer.confirmedSamples) : 0;
    this.trimBuffer(buffer);

    // Reset confirmation state for the next segment window
    buffer.lastTranscript = '';
    buffer.lastTranscriptEndSample = undefined;
    buffer.confirmCount = 0;
    buffer.lastWords = [];
    if (this.callbackStampedFrames) buffer.windowStartMs = retainedStartMs;
    else buffer.windowStartMs += advancedMs;
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
    const keep = this.callbackStampedFrames
      ? this.sampleCapturedAt(buffer, tMs)
      : Math.floor(((tMs - buffer.windowStartMs) / 1000) * this.sampleRate);
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
    this.stats.captured -= excess;   // re-fed to the next segment buffer by the caller: not lost, not ours
    while (excess > 0 && buffer.chunks.length > 0) {
      const last = buffer.chunks[buffer.chunks.length - 1];
      if (last.length <= excess) {
        excess -= last.length;
        buffer.chunks.pop();
        buffer.chunkStartMs.pop();
      } else {
        buffer.chunks[buffer.chunks.length - 1] = last.subarray(0, last.length - excess);
        excess = 0;
      }
    }
    buffer.totalSamples = keep;
    if (buffer.confirmedSamples > keep) buffer.confirmedSamples = keep;
    if (buffer.submittedEndSample !== undefined && buffer.submittedEndSample > keep) buffer.submittedEndSample = keep;
    buffer.lastTranscript = '';
    buffer.lastTranscriptEndSample = undefined;
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
    const newStartMs: number[] = [];

    buffer.chunks.forEach((chunk, i) => {
      if (samplesToTrim >= chunk.length) {
        samplesToTrim -= chunk.length;
        return;
      }
      if (samplesToTrim > 0) {
        // Partial chunk — keep the tail
        newChunks.push(chunk.subarray(samplesToTrim));
        newStartMs.push(buffer.chunkStartMs[i] + (samplesToTrim / this.sampleRate) * 1000);
        samplesToTrim = 0;
      } else {
        newChunks.push(chunk);
        newStartMs.push(buffer.chunkStartMs[i]);
      }
    });

    buffer.chunks = newChunks;
    buffer.chunkStartMs = newStartMs;
    buffer.absBase += buffer.confirmedSamples;
    buffer.totalSamples -= buffer.confirmedSamples;
    // Sample indices shift with the trim; keep the request coverage marks on the same index base.
    if (buffer.submittedEndSample !== undefined) buffer.submittedEndSample = Math.max(0, buffer.submittedEndSample - buffer.confirmedSamples);
    if (buffer.lastTranscriptEndSample !== undefined) buffer.lastTranscriptEndSample = Math.max(0, buffer.lastTranscriptEndSample - buffer.confirmedSamples);
    buffer.confirmedSamples = 0;
  }

  /** Whether `lastTranscript` describes every buffered sample (within half a second). Every feeder
   *  asks: with a slow STT, a turn keeps receiving audio for many seconds after the request whose text
   *  is `lastTranscript`, and publishing that text at a close dropped all of it. */
  private lastTranscriptCoversBuffer(buffer: SpeakerBuffer): boolean {
    if (buffer.lastTranscriptEndSample === undefined) return true;
    const slack = this.callbackStampedFrames ? 0 : Math.floor(this.sampleRate / 2);
    return buffer.lastTranscriptEndSample + slack >= buffer.totalSamples;
  }

  /** End (ms) of the buffered audio: from the last frame's own stamp for a callback-stamped feeder,
   *  from the gapless window for every other feeder. */
  private audioEndMs(buffer: SpeakerBuffer): number {
    return this.callbackStampedFrames
      ? this.bufferedEndMs(buffer)
      : buffer.windowStartMs + (buffer.totalSamples / this.sampleRate) * 1000;
  }

  /** Capture time (ms) just past the last buffered chunk; the window start when nothing is buffered. */
  private bufferedEndMs(buffer: SpeakerBuffer): number {
    const n = buffer.chunks.length;
    if (n === 0) return buffer.windowStartMs;
    return buffer.chunkStartMs[n - 1] + (buffer.chunks[n - 1].length / this.sampleRate) * 1000;
  }

  /** Capture time (ms) of buffered sample `sample`, counted from the first buffered sample, read
   *  through the stamp of the chunk that holds it. Past the end, it continues from the last chunk. */
  private captureTimeAt(buffer: SpeakerBuffer, sample: number): number {
    let rest = sample;
    for (let i = 0; i < buffer.chunks.length; i++) {
      const n = buffer.chunks[i].length;
      if (rest < n) return buffer.chunkStartMs[i] + (rest / this.sampleRate) * 1000;
      rest -= n;
    }
    return this.bufferedEndMs(buffer) + (rest / this.sampleRate) * 1000;
  }

  /** Index of the first buffered sample captured at or after `tMs`. */
  private sampleCapturedAt(buffer: SpeakerBuffer, tMs: number): number {
    let base = 0;
    for (let i = 0; i < buffer.chunks.length; i++) {
      const n = buffer.chunks[i].length;
      const startMs = buffer.chunkStartMs[i];
      if (tMs <= startMs) return base;
      const endMs = startMs + (n / this.sampleRate) * 1000;
      if (tMs < endMs) return base + Math.floor(((tMs - startMs) / 1000) * this.sampleRate);
      base += n;
    }
    return base;
  }

  /** Start index of the quietest chunk that begins within the last `sec` seconds of buffered audio
   *  (after the confirmed samples) — where a capped window is cut. The buffer end when none does. */
  private quietCutSample(buffer: SpeakerBuffer, sec: number): number {
    const from = buffer.totalSamples - sec * this.sampleRate;
    let pos = 0, cut = buffer.totalSamples, quietest = Infinity;
    for (const chunk of buffer.chunks) {
      if (pos >= from && pos > buffer.confirmedSamples) {
        const level = rms(chunk);
        if (level < quietest) { quietest = level; cut = pos; }
      }
      pos += chunk.length;
    }
    return cut;
  }

  /** The hard-cap request answered: publish its text as the confirmed segment for the capped window,
   *  then continue the turn from the cut with the audio fed since. An empty or junk answer falls back
   *  to the last transcript only when that transcript lies within the cut. */
  private finishCappedWindow(buffer: SpeakerBuffer, transcript: string): void {
    const cut = Math.min(buffer.capCutSample ?? buffer.totalSamples, buffer.totalSamples);
    buffer.capCutSample = undefined;
    let text = (transcript || '').trim();
    if (!text || isHallucination(text)) {
      const withinCut = buffer.lastTranscriptEndSample !== undefined && buffer.lastTranscriptEndSample <= cut;
      text = withinCut ? buffer.lastTranscript : '';
    }
    const startMs = buffer.windowStartMs;
    if (text && !isHallucination(text) && text !== buffer.lastConfirmedText && this.onSegmentConfirmed) {
      this.onSegmentConfirmed(buffer.speakerId, buffer.speakerName, text, startMs, this.captureTimeAt(buffer, cut),
        `${buffer.speakerId}:${buffer.sequenceNumber}`, buffer.lastLanguage);
      buffer.sequenceNumber++;
      buffer.lastConfirmedText = text;
      this.clearStaleDraft(buffer, startMs);
    } else if (buffer.pendingDraftText && this.onSegmentPending) {
      // Nothing usable for the capped window: withdraw its draft rather than confirm stale text.
      this.onSegmentPending(buffer.speakerId, buffer.speakerName, '', buffer.pendingDraftStartMs);
    }
    log(`[SpeakerStreams] Hard cap finalized for "${buffer.speakerName}" (${(cut / this.sampleRate).toFixed(1)}s)`);
    this.account(buffer, text ? 'transcribed' : 'no-speech', Math.max(0, cut - buffer.confirmedSamples));
    buffer.confirmedSamples = cut;
    const retainedStartMs = this.captureTimeAt(buffer, cut);
    this.trimBuffer(buffer);
    buffer.lastTranscript = '';
    buffer.lastTranscriptEndSample = undefined;
    buffer.confirmCount = 0;
    buffer.lastWords = [];
    buffer.windowStartMs = retainedStartMs;
    buffer.pendingDraftText = '';
  }

  /**
   * Full reset — discard everything. Used on speaker change and idle cleanup.
   */
  private fullReset(buffer: SpeakerBuffer, outcome: Outcome, reason?: string): void {
    this.account(buffer, outcome, this.unconfirmedSamples(buffer), reason);
    if (buffer.retired && this.routes.get(buffer.routeKey) === buffer) this.routes.delete(buffer.routeKey);
    buffer.absBase = 0;
    buffer.submittedAbsEnd = 0;
    buffer.chunks = [];
    buffer.chunkStartMs = [];
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
    buffer.lastFedEndMs = undefined;
    buffer.submittedEndSample = undefined;
    buffer.lastTranscriptEndSample = undefined;
    buffer.capCutSample = undefined;
    buffer.generation++;
  }
}
