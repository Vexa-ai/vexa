import { Page } from 'playwright';
import { BotConfig } from '../../../types';
import { RecordingService } from '../../../services/recording';
import { setActiveRecordingService, getRawCaptureService } from '../../../index';
import { log } from '../../../utils';
import { spawn, ChildProcess } from 'child_process';
import { zoomParticipantNameSelector } from './selectors';
import { dismissZoomPopups } from './prepare';
import { startZoomRichObservation } from './observe';

let recordingService: RecordingService | null = null;
let recordingStopResolver: (() => void) | null = null;
let parecordProcess: ChildProcess | null = null;
let speakerPollInterval: NodeJS.Timeout | null = null;
let lastActiveSpeaker: string | null = null;
let activeBotConfig: BotConfig | null = null;
let popupDismissInterval: NodeJS.Timeout | null = null;

/** Current DOM-polled active speaker — used by per-speaker pipeline as fallback name */
export function getLastActiveSpeaker(): string | null {
  return lastActiveSpeaker;
}

export async function startZoomWebRecording(page: Page | null, botConfig: BotConfig): Promise<void> {
  if (!page) throw new Error('[Zoom Web] Page required for recording');

  activeBotConfig = botConfig;

  // Recording service
  const wantsAudioCapture =
    !!botConfig.recordingEnabled &&
    (!Array.isArray(botConfig.captureModes) || botConfig.captureModes.includes('audio'));
  const sessionUid = botConfig.connectionId || `zoom-web-${Date.now()}`;

  if (wantsAudioCapture) {
    recordingService = new RecordingService(botConfig.meeting_id, sessionUid);
    setActiveRecordingService(recordingService);
    recordingService.start();
    log('[Zoom Web] Recording service started');
  }

  // Start PulseAudio capture from zoom_sink monitor.
  // Zoom web client routes audio through PulseAudio null sink (same as native SDK fallback).
  await startPulseAudioCapture();

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

  // Stop PulseAudio capture
  if (parecordProcess) {
    parecordProcess.kill('SIGTERM');
    parecordProcess = null;
  }

  activeBotConfig = null;

  if (recordingService) {
    try {
      await recordingService.finalize();
      log('[Zoom Web] Recording finalized');
    } catch (err: any) {
      log(`[Zoom Web] Error finalizing recording: ${err.message}`);
    }
    recordingService = null;
  }
}

export async function reconfigureZoomWebRecording(language: string | null, task: string | null): Promise<void> {
  // Language/task changes are handled at the per-speaker pipeline level.
  log(`[Zoom Web] reconfigure: ignoring (lang=${language}, task=${task})`);
}

export function getZoomWebRecordingService(): RecordingService | null {
  return recordingService;
}

// ---- PulseAudio capture ----

async function startPulseAudioCapture(): Promise<void> {
  return new Promise((resolve, reject) => {
    parecordProcess = spawn('parecord', [
      '--raw',
      '--format=s16le',
      '--rate=16000',
      '--channels=1',
      `--device=${process.env.PULSE_SINK || 'zoom_sink'}.monitor`,
    ]);

    if (!parecordProcess?.stdout) {
      reject(new Error('[Zoom Web] Failed to start parecord'));
      return;
    }

    let started = false;

    parecordProcess.stdout.on('data', (chunk: Buffer) => {
      if (!started) {
        log('[Zoom Web] PulseAudio capture receiving audio');
        started = true;
        resolve();
      }
      // Audio recording only — transcription is handled by the per-speaker pipeline
      // in index.ts (startPerSpeakerAudioCapture → browser ScriptProcessor → handlePerSpeakerAudioData)
      if (recordingService) {
        recordingService.appendPCMBuffer(chunk);
      }
    });

    parecordProcess.stderr?.on('data', (data: Buffer) => {
      log(`[Zoom Web] parecord stderr: ${data.toString().trim()}`);
    });

    parecordProcess.on('error', (err: Error) => {
      log(`[Zoom Web] parecord error: ${err.message}`);
      if (!started) reject(err);
    });

    parecordProcess.on('exit', (code, signal) => {
      log(`[Zoom Web] parecord exited: code=${code}, signal=${signal}`);
      parecordProcess = null;
    });

    // Optimistic resolve after 1s even with no data yet
    setTimeout(() => {
      if (!started) {
        log('[Zoom Web] PulseAudio capture started (waiting for data)');
        resolve();
      }
    }, 1000);
  });
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

