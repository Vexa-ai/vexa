import { Page } from 'playwright';
import { BotConfig } from '../../../types';
import { WhisperLiveService } from '../../../services/whisperlive';
import { RecordingService } from '../../../services/recording';
import { setActiveRecordingService } from '../../../index';
import { log } from '../../../utils';
import { spawn, ChildProcess } from 'child_process';
import {
  zoomActiveSpeakerSelector,
  zoomParticipantNameSelector,
} from './selectors';

let whisperLive: WhisperLiveService | null = null;
let recordingService: RecordingService | null = null;
let recordingStopResolver: (() => void) | null = null;
let parecordProcess: ChildProcess | null = null;
let audioSessionStartTime: number | null = null;
let speakerPollInterval: NodeJS.Timeout | null = null;
let lastActiveSpeaker: string | null = null;
let activeBotConfig: BotConfig | null = null;
let connectWhisperFn: ((cfg: BotConfig) => Promise<void>) | null = null;
let isReconfiguring = false;

export async function startZoomWebRecording(page: Page | null, botConfig: BotConfig): Promise<void> {
  if (!page) throw new Error('[Zoom Web] Page required for recording');

  activeBotConfig = botConfig;

  const transcriptionEnabled = botConfig.transcribeEnabled !== false;

  if (transcriptionEnabled) {
    whisperLive = new WhisperLiveService({ whisperLiveUrl: process.env.WHISPER_LIVE_URL });
    const whisperLiveUrl = await whisperLive.initializeWithStubbornReconnection('Zoom Web');
    log(`[Zoom Web] WhisperLive URL: ${whisperLiveUrl}`);

    // Open the WebSocket connection (initialize() only sets allocatedServerUrl — connect is separate)
    // cfg parameter allows reconfigure to reconnect with updated language/task
    const connectWhisper = async (cfg: BotConfig) => {
      try {
        await whisperLive!.connectToWhisperLive(
          cfg,
          (data: any) => {
            if (data?.status === 'WAIT') {
              log(`[Zoom Web] WhisperLive server busy — waiting...`);
            } else if (data?.segments) {
              const texts = (data.segments as any[])
                .filter((s: any) => s.completed && s.text)
                .map((s: any) => s.text as string);
              if (texts.length > 0) log(`[Zoom Web] Transcript: ${texts.join(' ').trim()}`);
            }
          },
          (err: Event) => {
            log(`[Zoom Web] WhisperLive WebSocket error`);
          },
          (evt: CloseEvent) => {
            if (isReconfiguring) {
              log(`[Zoom Web] WhisperLive connection closed during reconfigure (code=${evt.code}) — skipping auto-reconnect`);
              return;
            }
            log(`[Zoom Web] WhisperLive connection closed (code=${evt.code}). Reconnecting in 2s...`);
            whisperLive?.setServerReady(false);
            setTimeout(() => { connectWhisper(activeBotConfig || cfg).catch(() => {}); }, 2000);
          }
        );
        log(`[Zoom Web] WhisperLive WebSocket connected (lang=${cfg.language || 'auto'})`);
      } catch (e: any) {
        log(`[Zoom Web] WhisperLive connect error: ${e.message}. Retrying in 2s...`);
        setTimeout(() => { connectWhisper(activeBotConfig || cfg).catch(() => {}); }, 2000);
      }
    };
    connectWhisperFn = connectWhisper;
    await connectWhisper(botConfig);
  } else {
    log('[Zoom Web] Transcription disabled — recording only mode');
  }

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

  audioSessionStartTime = Date.now();

  // Start PulseAudio capture from zoom_sink monitor.
  // Zoom web client routes audio through PulseAudio null sink (same as native SDK fallback).
  await startPulseAudioCapture();

  // Start speaker detection polling via DOM
  startSpeakerPolling(page, botConfig);

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

  audioSessionStartTime = null;
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

  if (whisperLive) {
    whisperLive = null;
  }

  activeBotConfig = null;
  connectWhisperFn = null;

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

/**
 * Reconfigure the active WhisperLive session with new language/task.
 * Called when a `reconfigure` Redis command is received.
 * For Zoom Web the WhisperLive socket lives in Node.js, not the browser,
 * so we reconnect the socket with the updated config instead of calling
 * the browser-side `triggerWebSocketReconfigure`.
 *
 * Mirrors the pattern used in Google Meet / Teams:
 * 1. Close existing socket (generates new session UID on reconnect)
 * 2. Reset audio session start time for speaker event timestamps
 * 3. Reconnect with updated config
 */
export async function reconfigureZoomWebRecording(language: string | null, task: string | null): Promise<void> {
  if (!whisperLive || !activeBotConfig || !connectWhisperFn) {
    log('[Zoom Web] reconfigure: WhisperLive not active — ignoring');
    return;
  }
  log(`[Zoom Web] Reconfiguring WhisperLive: lang=${language}, task=${task}`);

  // Update the stored config so reconnect loops use the new values
  activeBotConfig = { ...activeBotConfig, language: language ?? undefined, task: task ?? undefined };

  try {
    // Suppress auto-reconnect from onClose handler during reconfigure
    isReconfiguring = true;

    // 1. Close existing socket to establish fresh session (new UID generated on reconnect)
    whisperLive.setServerReady(false);
    whisperLive.closeSocketForReconfigure();

    // Brief pause to ensure socket is fully closed
    await new Promise(resolve => setTimeout(resolve, 100));

    // 2. Reconnect with updated config — connectToWhisperLive generates new sessionUid
    isReconfiguring = false;
    await connectWhisperFn(activeBotConfig);
    log('[Zoom Web] Reconfigure reconnect complete');

    // 3. Reset audioSessionStartTime — WhisperLive server resets segment timestamps
    //    to ~0 on a new connection, so our speaker event timestamps must also reset
    //    to stay aligned. Without this, re-sent SPEAKER_START would have a large
    //    relativeMs from the original session while segments start at ~0ms → gap.
    //    This mirrors Google Meet which calls audioSvc.resetSessionStartTime().
    audioSessionStartTime = Date.now();
    log(`[Zoom Web] Reset audioSessionStartTime for new session`);

    // 4. Re-send current speaker on the new session so server knows who's talking.
    // Without this, the server only sees audio but no speaker info until the next
    // speaker change (which may never happen in a 1-on-1 call).
    // No delay needed — connectToWhisperLive now resolves only after socket.onopen,
    // so the new sessionUid is already set and socket is OPEN.
    if (lastActiveSpeaker && whisperLive) {
      const relativeMs = Date.now() - audioSessionStartTime; // Will be ~0ms
      const sent = whisperLive.sendSpeakerEvent('SPEAKER_START', lastActiveSpeaker, lastActiveSpeaker, relativeMs, activeBotConfig!);
      log(`🎤 [Zoom Web] SPEAKER_START (re-sent after reconfigure): ${lastActiveSpeaker} (sent=${sent}, uid=${whisperLive.getSessionUid()}, relativeMs=${relativeMs})`);
    }
  } catch (e: any) {
    isReconfiguring = false;
    log(`[Zoom Web] Reconfigure reconnect error: ${e.message}`);
  }
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
      '--device=zoom_sink.monitor',
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
      const float32 = pcmInt16ToFloat32(chunk);
      if (whisperLive) {
        whisperLive.sendAudioData(float32);
      }
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
    // Use activeBotConfig (updated on reconfigure) — NOT the closure's botConfig
    const cfg = activeBotConfig || botConfig;
    try {
      const speakerName = await page.evaluate((footerSelector: string) => {
        // Active speaker name is in .video-avatar__avatar-footer > span inside the active container.
        // NOTE: .video-avatar__avatar-name does NOT exist in Zoom Web Client DOM.
        const activeSpeaker = document.querySelector('.speaker-active-container__video-frame');
        if (!activeSpeaker) return null;
        const footer = activeSpeaker.querySelector(footerSelector);
        if (!footer) return null;
        // The name is in a <span> child; fall back to footer innerText
        const span = footer.querySelector('span');
        return (span?.textContent?.trim() || (footer as HTMLElement).innerText?.trim()) || null;
      }, zoomParticipantNameSelector);

      if (!audioSessionStartTime) return;
      const relativeMs = Date.now() - audioSessionStartTime;

      if (speakerName && speakerName !== lastActiveSpeaker) {
        // Speaker changed
        if (lastActiveSpeaker && whisperLive) {
          whisperLive.sendSpeakerEvent('SPEAKER_END', lastActiveSpeaker, lastActiveSpeaker, relativeMs, cfg);
          log(`🔇 [Zoom Web] SPEAKER_END: ${lastActiveSpeaker}`);
        }
        lastActiveSpeaker = speakerName;
        if (whisperLive) {
          whisperLive.sendSpeakerEvent('SPEAKER_START', speakerName, speakerName, relativeMs, cfg);
          log(`🎤 [Zoom Web] SPEAKER_START: ${speakerName}`);
        }
      } else if (!speakerName && lastActiveSpeaker) {
        // No active speaker
        if (whisperLive) {
          whisperLive.sendSpeakerEvent('SPEAKER_END', lastActiveSpeaker, lastActiveSpeaker, relativeMs, cfg);
          log(`🔇 [Zoom Web] SPEAKER_END: ${lastActiveSpeaker}`);
        }
        lastActiveSpeaker = null;
      }
    } catch {
      // Page may be navigating — ignore
    }
  }, 1000);
}

// ---- Helpers ----

function pcmInt16ToFloat32(buffer: Buffer): Float32Array {
  const int16 = new Int16Array(buffer.buffer, buffer.byteOffset, buffer.length / 2);
  const float32 = new Float32Array(int16.length);
  for (let i = 0; i < int16.length; i++) {
    float32[i] = int16[i] / 32768.0;
  }
  return float32;
}
