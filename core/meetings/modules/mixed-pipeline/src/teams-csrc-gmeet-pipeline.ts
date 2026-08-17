import {
  GmeetCompatibleBuffer,
  type GmeetCompatibleBufferConfig,
  type GmeetCompatibleSubmissionTrigger,
} from '@vexa/transcribe-buffer';
import type { TranscriptionResult } from '@vexa/transcribe-whisper';
import { teamsWindowHallucinationRule } from './hallucination-gate.js';
import {
  detectTeamsWordContest,
  type TeamsContestSubmission,
  type TeamsRoutedSpan,
  type TeamsWordContest,
} from './teams-contested-word-marker.js';
import {
  TeamsCsrcChannelizer,
  type TeamsCsrcChannelizerHealth,
  type TeamsCsrcVirtualFrame,
} from './teams-csrc-channelizer.js';
import type { TransportEvent } from './turn-source.js';
import { TrackNamer } from './track-namer.js';

export interface TeamsCsrcTranscriptSegment {
  csrc: number;
  /** Human name earned by the existing Teams track namer, or its stable Speaker A/B fallback. */
  speaker: string;
  sourceKey: string;
  segmentId: string;
  text: string;
  startMs: number;
  endMs: number;
  completed: boolean;
  language?: string;
}

export interface TeamsCsrcGmeetPipelineOptions {
  transcribe: (
    pcm: Float32Array,
    prompt?: string,
    context?: {
      csrc: number;
      sourceKey: string;
      forced: boolean;
      trigger: 'scheduled' | 'forced' | 'buffer';
      bufferTrigger: GmeetCompatibleSubmissionTrigger;
    },
  ) => Promise<TranscriptionResult>;
  onSegment: (segment: TeamsCsrcTranscriptSegment) => void;
  onRejectedOwnership?: (segment: TeamsCsrcTranscriptSegment, intervals: CsrcOwnershipInterval[]) => void;
  /** Evaluation/telemetry seam: every frame the smoothed channelizer actually routes. */
  onRoutedFrame?: (frame: TeamsCsrcVirtualFrame) => void;
  onError?: (error: unknown) => void;
  /** Our own Teams display name; the existing namer excludes it from human identity. */
  selfName?: string;
  /** CSRC activation may arrive after its first PCM callback. Default: 600 ms. */
  lookbackMs?: number;
  /** Whisper segment may begin before Teams reports CSRC activation. Default: 1200 ms. */
  ownershipLookbackMs?: number;
  /** A short false/true flicker below this gap keeps the same continuous GMeet window. Default: 1000 ms. */
  onsetGapMs?: number;
  /** Hold a nested new CSRC provisionally before it receives mixed PCM. Default: 1500 ms. */
  flickerHoldMs?: number;
  /** Maximum Whisper segment overhang past a CSRC ownership edge. Default: 600 ms. */
  ownershipSlackMs?: number;
  /** Persist the latest clean GMeet draft if confirmation has not arrived. Default: 4000 ms. */
  persistTimeoutMs?: number;
  /** GMeet-compatible window tuning. Defaults are identical to Google Meet. */
  buffer?: GmeetCompatibleBufferConfig;
}

export interface TeamsCsrcGmeetPipelineHealth extends TeamsCsrcChannelizerHealth {
  turns: number;
  /** Turn keys the shared GMeet window still holds a buffer and a submission timer for. Bounded by
   *  the number of concurrently active CSRC lanes, never by the meeting's turn count. */
  openSources: number;
  inFlight: number;
  rejectedOwnership: number;
  timeoutPromotions: number;
  contestedPairs: number;
}

export interface CsrcOwnershipInterval { startMs: number; endMs?: number }

/** Deterministic fail-closed check used before a confirmed segment may leave a CSRC lane. */
export function spanFitsCsrcOwnership(
  intervals: CsrcOwnershipInterval[],
  startMs: number,
  endMs: number,
  lookbackMs = 600,
  endSlackMs = 400,
  bridgeGapMs = 1000,
): boolean {
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs < startMs) return false;
  const ordered = intervals.slice().sort((a, b) => a.startMs - b.startMs);
  const merged: CsrcOwnershipInterval[] = [];
  for (const interval of ordered) {
    const previous = merged[merged.length - 1];
    if (!previous) {
      merged.push({ ...interval });
      continue;
    }
    const previousEnd = previous.endMs ?? Number.POSITIVE_INFINITY;
    if (interval.startMs <= previousEnd + bridgeGapMs) {
      previous.endMs = previous.endMs === undefined || interval.endMs === undefined
        ? undefined
        : Math.max(previous.endMs, interval.endMs);
    } else {
      merged.push({ ...interval });
    }
  }
  return merged.some((interval) => {
    const end = interval.endMs ?? Number.POSITIVE_INFINITY;
    return startMs >= interval.startMs - lookbackMs && endMs <= end + endSlackMs;
  });
}

interface TrackState {
  key: string;
  turn: number;
  lastAudioMs: number;
}

interface PendingDraft {
  sourceKey: string;
  segmentId: string;
  text: string;
  startMs: number;
  language?: string;
}

/**
 * Microsoft Teams candidate: raw transport activity becomes persistent virtual channels, then
 * every virtual channel is driven by the faithful shared copy of Google Meet's transcription
 * window. There is no Pyannote, diarization, voiceprint, or Whisper-timestamp ownership here.
 *
 * Teams supplies one decoded server mix, not pre-mix audio per CSRC. Consequently overlap routes
 * the same immutable PCM to each active window. This is deliberately visible in the experiment:
 * whether each window's prompt/continuity makes Whisper follow its established speaker is an
 * empirical property, never claimed as acoustic source separation.
 *
 * Time-and-word duplicates across simultaneously active CSRC lanes are detected post-confirm for
 * telemetry. Both rows remain, their public text stays verbatim, and no winner is inferred. The
 * shared GMeet-compatible buffer is unchanged; the deferred embedding resolver is documented in
 * ../TEAMS_CSRC_CONTESTED_TRANSCRIPTS.md.
 */
export class TeamsCsrcGmeetPipeline {
  private readonly manager: GmeetCompatibleBuffer;
  private readonly channelizer: TeamsCsrcChannelizer;
  private readonly trackNamer: TrackNamer;
  private readonly tracks = new Map<number, TrackState>();
  private readonly sourceToCsrc = new Map<string, number>();
  private readonly inflight = new Set<Promise<void>>();
  private readonly closing = new Set<Promise<void>>();
  private readonly onsetGapMs: number;
  private readonly lookbackMs: number;
  private readonly ownershipLookbackMs: number;
  private readonly ownershipSlackMs: number;
  private readonly persistTimeoutMs: number;
  private readonly ownership = new Map<number, CsrcOwnershipInterval[]>();
  private readonly pendingDrafts = new Map<string, PendingDraft>();
  private readonly promotedDraftIds = new Set<string>();
  private readonly sourceLastAudioMs = new Map<string, number>();
  /** Latest public row by stable id, retained only so a late proven name can repaint it. */
  private readonly published = new Map<string, TeamsCsrcTranscriptSegment>();
  /** Verbatim rows are the evidence source; diagnostic contest notation never enters public text. */
  private readonly rawPublished = new Map<string, TeamsCsrcTranscriptSegment>();
  private readonly contestSubmissions: TeamsContestSubmission[] = [];
  private readonly routedSpans: TeamsRoutedSpan[] = [];
  private readonly latestRoutedSpan = new Map<number, TeamsRoutedSpan>();
  private readonly contests = new Map<string, TeamsWordContest>();
  private turnCount = 0;
  private rejectedOwnership = 0;
  private timeoutPromotions = 0;

  constructor(private readonly options: TeamsCsrcGmeetPipelineOptions) {
    this.onsetGapMs = Math.max(0, options.onsetGapMs ?? 1000);
    this.lookbackMs = Math.max(0, options.lookbackMs ?? 600);
    this.ownershipLookbackMs = Math.max(0, options.ownershipLookbackMs ?? 1200);
    this.ownershipSlackMs = Math.max(0, options.ownershipSlackMs ?? 600);
    this.persistTimeoutMs = Math.max(0, options.persistTimeoutMs ?? 4000);
    this.trackNamer = new TrackNamer({
      selfName: options.selfName,
      // Teams sometimes exposes a media/topic handle — a session subject rather than the human's
      // canonical display name. Unknown is safer than that false attribution.
      requireCanonicalDisplayName: true,
      onNamed: (trackId) => this.repaintTrack(trackId),
    });
    this.manager = new GmeetCompatibleBuffer({
      ...options.buffer,
      // Teams' wall-clock witness proved that GMeet's confirmed-prefix early-return can otherwise
      // suppress a Whisper-available trailing draft for > 4 seconds. The shared module's default
      // remains faithful; this is a Teams-only visibility adaptation.
      publishTrailingDraftAfterPrefix: true,
      // CSRC routing already opens a new sourceKey when a virtual lane becomes discontinuous.
      // The copied GMeet batch-feeder gap guard would otherwise create a second Whisper trigger
      // directly from feedAudio(), bypassing the one shared two-second window timer.
      flushOnInputGap: false,
      isHallucination: options.buffer?.isHallucination
        // Teams owns this adapter, not the shared GMeet window. A repeated acknowledgement at
        // the head of a growing result must not delete the genuine speech that follows it.
        ?? ((text) => teamsWindowHallucinationRule(text) !== null),
    });
    this.channelizer = new TeamsCsrcChannelizer({
      lookbackMs: options.lookbackMs,
      flickerHoldMs: options.flickerHoldMs,
      onFrame: (frame) => {
        options.onRoutedFrame?.(frame);
        this.recordRoutedSpan(frame.csrc, frame.tsMs, frame.tsMs + frame.pcm.length / 16_000 * 1000);
        this.routeFrame(frame.csrc, frame.pcm, frame.tsMs);
      },
    });

    this.manager.onSegmentReady = (sourceKey, _name, audio, observedTrigger) => {
      const prompt = this.manager.getLastConfirmedText(sourceKey) || undefined;
      const bufferTrigger = observedTrigger ?? 'input-gap';
      const trigger = bufferTrigger === 'interval' || bufferTrigger === 'manual'
        ? 'scheduled'
        : bufferTrigger === 'input-gap'
          ? 'buffer'
          : 'forced';
      const job = (async () => {
        try {
          const csrc = this.sourceToCsrc.get(sourceKey);
          if (csrc === undefined) throw new Error(`unknown CSRC source ${sourceKey}`);
          const result = await this.options.transcribe(audio, prompt, {
            csrc,
            sourceKey,
            forced: trigger === 'forced',
            trigger,
            bufferTrigger,
          });
          this.contestSubmissions.push({
            sourceKey,
            segments: result.segments ?? [],
            observedAtMs: this.sourceLastAudioMs.get(sourceKey),
          });
          const lastEnd = result.segments?.length
            ? result.segments[result.segments.length - 1].end
            : undefined;
          this.manager.handleTranscriptionResult(
            sourceKey,
            (result.text ?? '').trim(),
            lastEnd,
            result.segments,
            result.language,
          );
          this.refreshContestsForSource(sourceKey);
        } catch (error) {
          this.options.onError?.(error);
          this.manager.handleTranscriptionResult(sourceKey, '');
        }
      })();
      this.inflight.add(job);
      void job.finally(() => this.inflight.delete(job));
    };

    this.manager.onSegmentConfirmed = (sourceKey, _name, text, startMs, endMs, managerSegmentId, language) => {
      // Match GMeet's public transcript key exactly: confirmed and draft rows both upsert by the
      // first observed draft window. Whisper may move the same forming boundary between passes;
      // that must update one durable row, not persist near-identical rows a few hundred ms apart.
      const draft = this.pendingDrafts.get(sourceKey);
      const segmentId = draft?.segmentId ?? managerSegmentId;
      const publicStartMs = draft?.startMs ?? startMs;
      this.pendingDrafts.delete(sourceKey);
      this.promotedDraftIds.delete(segmentId);
      this.publish(sourceKey, segmentId, text, publicStartMs, endMs, true, language);
    };
    this.manager.onSegmentPending = (sourceKey, _name, text, startMs, language) => {
      const existing = this.pendingDrafts.get(sourceKey);
      const segmentId = existing?.segmentId ?? `${sourceKey}:${Math.round(startMs)}`;
      const publicStartMs = existing?.startMs ?? startMs;
      if (text.trim()) {
        this.pendingDrafts.set(sourceKey, { sourceKey, segmentId, text, startMs: publicStartMs, language });
        // Once the timeout persisted this public row, later forming drafts must not downgrade it
        // back to completed:false. A word-prefix extension may still grow that persisted row as
        // completed:true; a non-prefix Whisper revision waits for real confirmation.
        if (this.promotedDraftIds.has(segmentId)) {
          const previous = this.published.get(segmentId)?.text ?? '';
          if (this.isWordPrefix(previous, text)) {
            this.publish(
              sourceKey,
              segmentId,
              text,
              publicStartMs,
              Math.max(publicStartMs, this.sourceLastAudioMs.get(sourceKey) ?? publicStartMs),
              true,
              language,
            );
          }
        } else {
          this.publish(sourceKey, segmentId, text, publicStartMs, publicStartMs, false, language);
        }
      } else {
        this.pendingDrafts.delete(sourceKey);
        if (!this.promotedDraftIds.has(segmentId)) {
          this.publish(sourceKey, segmentId, '', publicStartMs, publicStartMs, false, language);
        }
      }
    };
  }

  feedMixedAudio(pcm: Float32Array, tsMs: number): void {
    this.channelizer.feedAudio(pcm, tsMs);
    this.trackNamer.tick(tsMs + pcm.length / 16_000 * 1000);
    this.promoteTimedOutDrafts(tsMs);
  }

  recordTransportEvent(event: TransportEvent): void {
    this.trackNamer.setTrackActive(String(event.csrc), event.active, event.tMs);
    const intervals = this.ownership.get(event.csrc) ?? [];
    if (event.active) {
      if (!intervals.some((interval) => interval.endMs === undefined)) intervals.push({ startMs: event.tMs });
    } else {
      const open = [...intervals].reverse().find((interval) => interval.endMs === undefined);
      if (open) open.endMs = event.tMs;
    }
    this.ownership.set(event.csrc, intervals);
    this.channelizer.recordTransportEvent(event);
  }

  /** Teams' named active-speaker hint. It names a transport track; it never cuts audio. */
  recordHint(name: string, tMs: number, isEnd = false): void {
    this.trackNamer.recordHint(name, tMs, isEnd);
  }

  /** Teams' own caption author is independent name evidence for the active transport track. */
  recordCaption(name: string, tMs: number): void {
    this.trackNamer.recordCaption(name, tMs);
  }

  /** A roster name can canonicalize or complete an already-evidenced transport identity. */
  recordRosterName(name: string, tMs?: number): void {
    this.trackNamer.recordRosterName(name, tMs);
  }

  /** Elimination is allowed only when Teams proved how complete the observed roster was. */
  recordRosterCoverage(named: number, participants: number, tMs?: number): void {
    this.trackNamer.recordRosterCoverage(named, participants, tMs);
  }

  /** Deterministic replay seam; production uses the GMeet-default scheduled two-second cadence. */
  async requestTranscription(csrc: number, force = false): Promise<boolean> {
    const state = this.tracks.get(csrc);
    if (!state) return false;
    const requested = await this.manager.requestTranscription(state.key, force);
    await this.settleInflight();
    return requested;
  }

  activeCsrcs(): number[] {
    return [...this.tracks.keys()];
  }

  health(): TeamsCsrcGmeetPipelineHealth {
    return {
      ...this.channelizer.health(),
      turns: this.turnCount,
      openSources: this.manager.getActiveSpeakers().length,
      inFlight: this.inflight.size,
      rejectedOwnership: this.rejectedOwnership,
      timeoutPromotions: this.timeoutPromotions,
      contestedPairs: this.contests.size,
    };
  }

  async flush(): Promise<void> {
    this.promoteTimedOutDrafts(Number.POSITIVE_INFINITY, true);
    for (const state of this.tracks.values()) await this.flushSource(state.key);
    await this.settleAll();
  }

  async dispose(): Promise<void> {
    await this.flush();
    this.trackNamer.finish();
    this.manager.removeAll();
  }

  private routeFrame(csrc: number, pcm: Float32Array, tsMs: number): void {
    let state = this.tracks.get(csrc);
    if (!state || tsMs - state.lastAudioMs > this.onsetGapMs) {
      if (state) this.closeTurn(state.key);
      const turn = (state?.turn ?? 0) + 1;
      const key = `csrc-${csrc}:${turn}`;
      state = { key, turn, lastAudioMs: tsMs };
      this.tracks.set(csrc, state);
      this.sourceToCsrc.set(key, csrc);
      this.turnCount++;
      this.manager.addSpeaker(key, `CSRC ${csrc}`);
    }
    state.lastAudioMs = tsMs;
    this.sourceLastAudioMs.set(state.key, tsMs + pcm.length / 16_000 * 1000);
    this.manager.feedAudio(state.key, pcm, tsMs);
  }

  private closeTurn(sourceKey: string): void {
    const job = (async () => {
      try {
        await this.flushSource(sourceKey);
      } catch (error) {
        this.options.onError?.(error);
      } finally {
        this.releaseTurn(sourceKey);
      }
    })();
    this.closing.add(job);
    void job.finally(() => this.closing.delete(job));
  }

  /**
   * Retire a turn key that will never be fed again. `routeFrame` mints one key per turn and each
   * key opens its own two-second submission timer inside the shared GMeet window, so the manager
   * releases the key here — otherwise a meeting's timers and per-key bookkeeping grow with its
   * turn count for the whole session.
   *
   * Ordered strictly AFTER the flush: `flushSource` submits the turn's remaining audio and settles
   * the transcription it triggers, so the removal below finds an already-drained buffer and the
   * final rows have been published under a live `sourceToCsrc` entry.
   */
  private releaseTurn(sourceKey: string): void {
    this.manager.removeSpeaker(sourceKey);
    this.sourceToCsrc.delete(sourceKey);
    this.sourceLastAudioMs.delete(sourceKey);
    this.pendingDrafts.delete(sourceKey);
    // Both id shapes a turn publishes under (`<sourceKey>:<startMs>` and the manager's
    // `<sourceKey>:<seq>`) carry the key plus a separator, so the prefix cannot reach a sibling
    // turn such as `csrc-201:30` from `csrc-201:3`.
    const prefix = `${sourceKey}:`;
    for (const id of this.promotedDraftIds) {
      if (id.startsWith(prefix)) this.promotedDraftIds.delete(id);
    }
  }

  private async flushSource(sourceKey: string): Promise<void> {
    await this.manager.flushSpeaker(sourceKey, true);
    await this.settleInflight();
  }

  private publish(
    sourceKey: string,
    segmentId: string,
    text: string,
    startMs: number,
    endMs: number,
    completed: boolean,
    language?: string,
  ): boolean {
    const csrc = this.sourceToCsrc.get(sourceKey);
    if (csrc === undefined) return false;
    // Whisper segment ends can overhang the submitted PCM (especially on terminal flush). A public
    // transcript row must never claim audio later than the last sample actually routed to this
    // source. The start is retained; a fully overhanging tail collapses to a zero-length boundary.
    const sourceAudioEndMs = this.sourceLastAudioMs.get(sourceKey);
    const boundedEndMs = sourceAudioEndMs === undefined
      ? endMs
      : Math.max(startMs, Math.min(endMs, sourceAudioEndMs));
    if (completed && text.trim() && !spanFitsCsrcOwnership(
      this.ownership.get(csrc) ?? [],
      startMs,
      boundedEndMs,
      this.ownershipLookbackMs,
      this.ownershipSlackMs,
      this.onsetGapMs,
    )) {
      this.rejectedOwnership++;
      this.options.onRejectedOwnership?.(
        { csrc, speaker: this.trackNamer.labelFor(String(csrc)), sourceKey, segmentId, text, startMs, endMs: boundedEndMs, completed, language },
        (this.ownership.get(csrc) ?? []).map((interval) => ({ ...interval })),
      );
      // Clear an ordinary draft under the same public key if the confirmed replacement is refused.
      // A timeout-promoted durable row must survive a refused extension.
      if (!this.promotedDraftIds.has(segmentId)) {
        this.emit({ csrc, speaker: this.trackNamer.labelFor(String(csrc)), sourceKey, segmentId, text: '', startMs, endMs: startMs, completed: false, language });
      }
      return false;
    }
    this.emit({ csrc, speaker: this.trackNamer.labelFor(String(csrc)), sourceKey, segmentId, text, startMs, endMs: boundedEndMs, completed, language });
    return true;
  }

  private emit(segment: TeamsCsrcTranscriptSegment): void {
    if (!segment.completed && !segment.text.trim()) {
      this.rawPublished.delete(segment.segmentId);
      this.removeContestsFor(segment.segmentId);
      const previous = this.published.get(segment.segmentId);
      this.published.delete(segment.segmentId);
      this.options.onSegment(previous ? { ...previous, text: '', completed: false } : segment);
      return;
    }
    this.rawPublished.set(segment.segmentId, segment);
    this.refreshContestsFor(segment.segmentId);
    this.reconcilePublished();
  }

  private isWordPrefix(prefixText: string, candidateText: string): boolean {
    const words = (value: string): string[] => value
      .toLocaleLowerCase()
      .replace(/[^\p{L}\p{N}]+/gu, ' ')
      .trim()
      .split(/\s+/)
      .filter(Boolean);
    const prefix = words(prefixText);
    const candidate = words(candidateText);
    return prefix.length <= candidate.length && prefix.every((word, index) => candidate[index] === word);
  }

  /** A name is accepted only by the existing corroborated Teams namer. Re-emit the same ids so
   * collectors and the live witness perform an ordinary upsert instead of duplicating speech. */
  private repaintTrack(trackId: string): void {
    const csrc = Number(trackId);
    if (!Number.isFinite(csrc)) return;
    const speaker = this.trackNamer.labelFor(trackId);
    for (const segment of this.rawPublished.values()) {
      if (segment.csrc !== csrc || segment.speaker === speaker) continue;
      this.rawPublished.set(segment.segmentId, { ...segment, speaker });
    }
    this.reconcilePublished();
  }

  private recordRoutedSpan(csrc: number, startMs: number, endMs: number): void {
    const previous = this.latestRoutedSpan.get(csrc);
    if (previous && startMs <= previous.endMs + 20) {
      previous.endMs = Math.max(previous.endMs, endMs);
    } else {
      const span = { csrc, startMs, endMs };
      this.routedSpans.push(span);
      this.latestRoutedSpan.set(csrc, span);
    }
    // Word-contest evidence is needed only while a rival timestamp-overlapping row can still
    // arrive. Retain the detected pair count for meeting telemetry, but bound raw word traces and
    // routed spans. Diagnostic notation must never be written into the customer transcript.
    const evidenceFloorMs = endMs - 120_000;
    for (let index = this.routedSpans.length - 1; index >= 0; index--) {
      if (this.routedSpans[index].endMs < evidenceFloorMs) this.routedSpans.splice(index, 1);
    }
    for (let index = this.contestSubmissions.length - 1; index >= 0; index--) {
      if ((this.contestSubmissions[index].observedAtMs ?? endMs) < evidenceFloorMs) {
        this.contestSubmissions.splice(index, 1);
      }
    }
  }

  private contestKey(left: string, right: string): string {
    return [left, right].sort().join('~');
  }

  private removeContestsFor(segmentId: string): void {
    for (const [key, contest] of this.contests) {
      if (contest.leftSegmentId === segmentId || contest.rightSegmentId === segmentId) this.contests.delete(key);
    }
  }

  private refreshContestsFor(segmentId: string): void {
    const row = this.rawPublished.get(segmentId);
    this.removeContestsFor(segmentId);
    if (!row?.completed || !row.text.trim()) return;
    for (const rival of this.rawPublished.values()) {
      if (rival.segmentId === segmentId || !rival.completed || !rival.text.trim()) continue;
      const contest = detectTeamsWordContest(row, rival, this.contestSubmissions, this.routedSpans);
      if (contest) this.contests.set(this.contestKey(row.segmentId, rival.segmentId), contest);
    }
  }

  private refreshContestsForSource(sourceKey: string): void {
    for (const row of this.rawPublished.values()) {
      if (row.sourceKey === sourceKey) this.refreshContestsFor(row.segmentId);
    }
    this.reconcilePublished();
  }

  private reconcilePublished(): void {
    for (const raw of this.rawPublished.values()) {
      // Contest detection is internal abstention/telemetry. Customer transcript text is always
      // Whisper's confirmed text verbatim; implementation diagnostics never enter public rows.
      const desired = raw;
      const previous = this.published.get(raw.segmentId);
      if (previous && previous.text === desired.text && previous.speaker === desired.speaker
          && previous.completed === desired.completed && previous.startMs === desired.startMs
          && previous.endMs === desired.endMs && previous.language === desired.language) continue;
      this.published.set(raw.segmentId, desired);
      this.options.onSegment(desired);
    }
  }

  private promoteTimedOutDrafts(nowMs: number, force = false): void {
    for (const draft of this.pendingDrafts.values()) {
      if (this.promotedDraftIds.has(draft.segmentId)) continue;
      if (!force && nowMs - draft.startMs < this.persistTimeoutMs) continue;
      const endMs = Math.max(draft.startMs, this.sourceLastAudioMs.get(draft.sourceKey) ?? nowMs);
      if (this.publish(
        draft.sourceKey,
        draft.segmentId,
        draft.text,
        draft.startMs,
        endMs,
        true,
        draft.language,
      )) {
        this.promotedDraftIds.add(draft.segmentId);
        this.timeoutPromotions++;
      }
    }
  }

  private async settleInflight(): Promise<void> {
    while (this.inflight.size) await Promise.all([...this.inflight]);
  }

  private async settleAll(): Promise<void> {
    while (this.closing.size || this.inflight.size) {
      await Promise.all([...this.closing, ...this.inflight]);
    }
  }
}
