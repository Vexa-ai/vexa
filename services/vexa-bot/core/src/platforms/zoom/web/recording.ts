import { Page } from 'playwright';
import { BotConfig } from '../../../types';
import { RecordingService } from '../../../services/recording';
import { getRawCaptureService, getSegmentPublisher, feedMixedAudio, recordMixedHint, hasMixedChunkedPipeline, disposeMixedChunkedPipeline } from '../../../index';
import { log } from '../../../utils';
import { PulseAudioCapture, UnifiedRecordingPipeline } from '@vexa/audio-pipelines';
import { zoomParticipantNameSelector } from './selectors';
import { dismissZoomPopups } from './prepare';
import { startZoomRichObservation } from './observe';
import { ensureBrowserUtils } from '../../../utils/injection';

let recordingService: RecordingService | null = null;
let recordingStopResolver: (() => void) | null = null;
let pipeline: UnifiedRecordingPipeline | null = null;
let speakerPollInterval: NodeJS.Timeout | null = null;
let lastActiveSpeaker: string | null = null;
let popupDismissInterval: NodeJS.Timeout | null = null;
let feedingChunked = false;
let pulseSource: PulseAudioCapture | null = null;
let ownsPulseSource = false;

/** Current DOM-polled active speaker — used by per-speaker pipeline as fallback name */
export function getLastActiveSpeaker(): string | null {
  return lastActiveSpeaker;
}

export async function startZoomWebRecording(page: Page | null, botConfig: BotConfig): Promise<void> {
  if (!page) throw new Error('[Zoom Web] Page required for recording');

  const wantsAudioCapture =
    !!botConfig.recordingEnabled &&
    (!Array.isArray(botConfig.captureModes) || botConfig.captureModes.includes('audio'));
  const sessionUid = botConfig.connectionId || `zoom-web-${Date.now()}`;

  // ── Live transcription (ChunkedTranscriber — THE single-channel core) ──
  // Zoom web delivers ONE mixed remote stream (PulseAudio sink monitor).
  // Raw PCM goes straight into the core (created by initPerSpeakerPipeline);
  // the DOM active-speaker poll provides timestamped naming hints. Same
  // algorithm + host wiring as the in-tab extension's ingest server.
  if (botConfig.transcribeEnabled !== false && hasMixedChunkedPipeline()) {
    feedingChunked = true;
    pulseSource = new PulseAudioCapture();
    ownsPulseSource = true;
    pulseSource.on('pcm', (buf: Buffer) => {
      if (!feedingChunked) return;
      // s16le mono 16 kHz → Float32 [-1, 1]
      const n = Math.floor(buf.length / 2);
      const f32 = new Float32Array(n);
      for (let i = 0; i < n; i++) f32[i] = buf.readInt16LE(i * 2) / 32768;
      feedMixedAudio(f32, Date.now());
    });
    log('[Zoom Web] ChunkedTranscriber feed ready — live transcription on the mixed stream');
  }

  if (wantsAudioCapture) {
    if (!botConfig.recordingUploadUrl || !botConfig.token) {
      log('[Zoom Web] recordingUploadUrl or token missing — skipping audio capture');
    } else {
      // Pack U.4 (v0.10.6): unified audio pipeline. PulseAudioCapture spawns
      // parecord on zoom_sink.monitor, slices PCM into 15s WAV chunks; the
      // UnifiedRecordingPipeline forwards each chunk to RecordingService.
      // uploadChunk() so chunks land in MinIO immediately. No local-disk
      // WAV; the master is built server-side by recording_finalizer.py at
      // bot_exit_callback.
      // (Segment-to-audio alignment is owned by UnifiedRecordingPipeline —
      // it subscribes to source.on('started') and calls
      // publisher.resetSessionStart(). Same hook for all 3 platforms;
      // no per-platform handler needed here.)
      recordingService = new RecordingService(botConfig.meeting_id, sessionUid);
      const source = pulseSource ?? new PulseAudioCapture();
      if (pulseSource) ownsPulseSource = false; // UnifiedRecordingPipeline owns start/stop now

      pipeline = new UnifiedRecordingPipeline({
        source,
        recordingService,
        uploadUrl: botConfig.recordingUploadUrl,
        token: botConfig.token,
        platform: 'zoom-web',
      });
      await pipeline.start();
      log('[Zoom Web] Unified recording pipeline started (PulseAudio → chunked upload)');
    }
  }

  // Transcription without recording: nothing else starts parecord — do it here.
  if (ownsPulseSource && pulseSource) {
    await pulseSource.start();
    log('[Zoom Web] PulseAudioCapture started (transcription-only)');
  }

  // Start speaker detection polling via DOM
  startSpeakerPolling(page, botConfig);

  // Periodically dismiss popups (AI Companion, chat guest tooltip, etc.)
  popupDismissInterval = setInterval(() => {
    dismissZoomPopups(page).catch(() => {});
  }, 2000);

  // Optional: rich observation harness — enabled by ZOOM_OBSERVE=true
  // Dumps WebRTC stats / per-element audio levels / WebSocket frames /
  // DOM badge / caption availability every 2s for architecture research.
  if (process.env.ZOOM_OBSERVE === 'true') {
    try {
      await startZoomRichObservation(page);
    } catch (e: any) {
      log(`[Zoom Web] ZOOM_OBSERVE harness failed to install: ${e.message}`);
    }
  }

  // Block until stopZoomWebRecording() is called
  await new Promise<void>((resolve) => {
    recordingStopResolver = resolve;
  });
}

export async function stopZoomWebRecording(): Promise<void> {
  log('[Zoom Web] Stopping recording');

  // Close the single-channel core first — its closing pass publishes the
  // final turn; this MUST complete before session_end goes out. Bounded so a
  // wedged transcription service can't hang the leave.
  feedingChunked = false;
  try {
    await Promise.race([
      disposeMixedChunkedPipeline(),
      new Promise<void>(r => setTimeout(r, 10_000)),
    ]);
  } catch { /* best effort */ }
  if (ownsPulseSource && pulseSource) { try { await pulseSource.stop(); } catch { /* best effort */ } }
  pulseSource = null;
  ownsPulseSource = false;

  // Stop speaker polling
  if (speakerPollInterval) {
    clearInterval(speakerPollInterval);
    speakerPollInterval = null;
  }

  // Stop popup dismissal
  if (popupDismissInterval) {
    clearInterval(popupDismissInterval);
    popupDismissInterval = null;
  }

  lastActiveSpeaker = null;

  // Unblock the blocking wait
  if (recordingStopResolver) {
    recordingStopResolver();
    recordingStopResolver = null;
  }

  // Stop the unified pipeline. This kills parecord, emits the final chunk
  // with isFinal=true, and drains the upload queue so meeting-api flips
  // Recording.status to COMPLETED before the bot exits. Pack P / Pack U
  // contract: the pipeline owns the shutdown sequence — no manual SIGTERM
  // fallback here.
  if (pipeline) {
    await pipeline.stop();
    pipeline = null;
  }

  recordingService = null;
}

export async function reconfigureZoomWebRecording(language: string | null, task: string | null): Promise<void> {
  // Language/task changes are handled at the per-speaker pipeline level.
  log(`[Zoom Web] reconfigure: ignoring (lang=${language}, task=${task})`);
}

export function getZoomWebRecordingService(): RecordingService | null {
  return recordingService;
}

// ---- Speaker detection via DOM polling ----

function startSpeakerPolling(page: Page, botConfig: BotConfig): void {
  // Install the SHARED zoom-speakers module in-page (the SAME DOM active-speaker
  // logic the extension runs) — one codebase for the Zoom attribution layer.
  ensureBrowserUtils(page, require('path').join(__dirname, '../../../browser-utils.global.js'))
    .then(() => page.evaluate((selfName: string) => {
      const w = window as any;
      if (!w.__vexaZoomSpeakers && w.VexaBrowserUtils?.createZoomSpeakers) {
        w.__vexaZoomSpeakers = w.VexaBrowserUtils.createZoomSpeakers({
          selfName,
          log: (m: string) => w.logBot?.('[ZoomSpeakers] ' + m),
        });
        w.logBot?.('[Zoom Web] shared zoom-speakers attribution installed');
      }
    }, botConfig.botName || 'Vexa').catch(() => { /* best-effort; inline fallback below */ }))
    .catch(() => { /* bundle inject failed; inline fallback below */ });

  speakerPollInterval = setInterval(async () => {
    if (!page || page.isClosed()) return;
    try {
      const speakerName = await page.evaluate((footerSelector: string) => {
        // Preferred: the shared module's current active speaker (one codebase).
        const shared = (window as any).__vexaZoomSpeakers;
        if (shared) return shared.getActiveSpeaker();

        // Fallback (module not yet installed): identical inline DOM read.
        function nameFromContainer(container: Element | null): string | null {
          if (!container) return null;
          const footer = container.querySelector(footerSelector);
          if (!footer) return null;
          const span = footer.querySelector('span');
          return (span?.textContent?.trim() || (footer as HTMLElement).innerText?.trim()) || null;
        }

        // Layout 1: Normal view — active speaker has a dedicated full-size container
        const name1 = nameFromContainer(document.querySelector('.speaker-active-container__video-frame'));
        if (name1) return name1;

        // Layout 2: Screen-share view — active speaker tile has the --active modifier class
        const name2 = nameFromContainer(document.querySelector('.speaker-bar-container__video-frame--active'));
        if (name2) return name2;

        return null;
      }, zoomParticipantNameSelector);

      // Hint for the cluster↔name binder (single-channel core): the DOM's
      // current active speaker, timestamped. Null = the open turn ended.
      if (speakerName !== lastActiveSpeaker) {
        if (speakerName) recordMixedHint(speakerName, 'dom-active', Date.now());
        else recordMixedHint('', 'dom-active', Date.now(), true);
      }
      if (speakerName && speakerName !== lastActiveSpeaker) {
        // Speaker changed — log to raw capture if active
        const rawCapture = getRawCaptureService();
        if (rawCapture) {
          rawCapture.logSpeakerEvent(lastActiveSpeaker, speakerName);
        }
        if (lastActiveSpeaker) {
          log(`🔇 [Zoom Web] SPEAKER_END: ${lastActiveSpeaker}`);
        }
        lastActiveSpeaker = speakerName;
        log(`🎤 [Zoom Web] SPEAKER_START: ${speakerName}`);
      } else if (!speakerName && lastActiveSpeaker) {
        // No active speaker
        log(`🔇 [Zoom Web] SPEAKER_END: ${lastActiveSpeaker}`);
        lastActiveSpeaker = null;
      }
    } catch {
      // Page may be navigating — ignore
    }
  }, 250);
}
