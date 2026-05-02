import { Page } from 'playwright';
import { BotConfig } from '../../../types';
import { RecordingService } from '../../../services/recording';
import { getRawCaptureService, getSegmentPublisher } from '../../../index';
import { log } from '../../../utils';
import { PulseAudioCapture, UnifiedRecordingPipeline } from '../../../services/audio-pipeline';
import { zoomParticipantNameSelector } from './selectors';
import { dismissZoomPopups } from './prepare';
import { startZoomRichObservation } from './observe';

let recordingService: RecordingService | null = null;
let recordingStopResolver: (() => void) | null = null;
let pipeline: UnifiedRecordingPipeline | null = null;
let speakerPollInterval: NodeJS.Timeout | null = null;
let lastActiveSpeaker: string | null = null;
let popupDismissInterval: NodeJS.Timeout | null = null;

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
      recordingService = new RecordingService(botConfig.meeting_id, sessionUid);
      const source = new PulseAudioCapture();

      // Pack U.4 segment-alignment hook (#GH-zoom-segment-drift, 2026-05-02):
      // Reset SegmentPublisher.sessionStartMs the moment parecord delivers
      // its first sample — that's t=0 of the master.wav. Without this,
      // segment timestamps remain anchored to per-speaker-pipeline init
      // (~admission moment) which can be 20-30s BEFORE parecord-start.
      // Result pre-fix: clicking a segment in the dashboard seeks the
      // audio player past end-of-file. GMeet/Teams have an equivalent
      // hook via __vexaRecordingStarted (browser-side, fires on
      // MediaRecorder.onstart) — Zoom Web wasn't covered because
      // parecord doesn't fire a browser event.
      source.on('started', () => {
        const publisher = getSegmentPublisher();
        if (publisher) {
          publisher.resetSessionStart();
          log(`[Zoom Web] Session start re-aligned to parecord t=0: ${new Date(publisher.sessionStartMs).toISOString()}`);
        }
      });

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
  speakerPollInterval = setInterval(async () => {
    if (!page || page.isClosed()) return;
    try {
      const speakerName = await page.evaluate((footerSelector: string) => {
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
