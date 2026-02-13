import { Page } from 'playwright';
import { BotConfig } from '../../../types';
import { WhisperLiveService } from '../../../services/whisperlive';
import { RecordingService } from '../../../services/recording';
import { setActiveRecordingService } from '../../../index';
import { getSDKManager } from './join';
import { log } from '../../../utils';
import { spawn, ChildProcess } from 'child_process';

let whisperLive: WhisperLiveService | null = null;
let whisperSocket: WebSocket | null = null;
let recordingStopResolver: (() => void) | null = null;
let parecordProcess: ChildProcess | null = null;
let audioSessionStartTime: number | null = null;
let activeSpeakers = new Set<number>();  // Currently active speaker user IDs
let recordingService: RecordingService | null = null;

export async function startZoomRecording(page: Page | null, botConfig: BotConfig): Promise<void> {
  log('[Zoom] Starting audio recording and WhisperLive connection');

  const sdkManager = getSDKManager();
  const transcriptionEnabled = botConfig.transcribeEnabled !== false;

  try {
    if (transcriptionEnabled) {
      // Initialize WhisperLive service
      whisperLive = new WhisperLiveService({
        whisperLiveUrl: process.env.WHISPER_LIVE_URL
      });

      // Initialize connection
      const whisperLiveUrl = await whisperLive.initialize();
      if (!whisperLiveUrl) {
        throw new Error('[Zoom] Failed to initialize WhisperLive URL');
      }
      log(`[Zoom] WhisperLive URL initialized: ${whisperLiveUrl}`);

      // Connect to WhisperLive with event handlers
      whisperSocket = await whisperLive.connectToWhisperLive(
        botConfig,
        (data: any) => {
          // Handle incoming messages (transcriptions, etc.)
          if (data.message === 'SERVER_READY') {
            log('[Zoom] WhisperLive server ready');
          }
        },
        (error: Event) => {
          log(`[Zoom] WhisperLive error: ${error}`);
        },
        (event: CloseEvent) => {
          log(`[Zoom] WhisperLive connection closed: ${event.code} ${event.reason}`);
        }
      );

      if (!whisperSocket) {
        throw new Error('[Zoom] Failed to connect to WhisperLive');
      }
      log('[Zoom] WhisperLive connected successfully');
    } else {
      log('[Zoom] Transcription disabled by config; running recording-only mode.');
      whisperLive = null;
      whisperSocket = null;
    }

    // Initialize audio recording if enabled
    if (botConfig.recordingEnabled) {
      const sessionUid = botConfig.connectionId || `zoom_${Date.now()}`;
      recordingService = new RecordingService(botConfig.meeting_id, sessionUid);
      recordingService.start();
      setActiveRecordingService(recordingService);
      log('[Zoom] Audio recording service started');
    }

    // Start SDK audio capture with callback to send to WhisperLive.
    // If SDK returns NO_PERMISSION (raw data license required), fall back to PulseAudio capture.
    let sdkRecordingSucceeded = false;
    try {
      await sdkManager.startRecording((buffer: Buffer, sampleRate: number) => {
        if (whisperLive) {
          const float32 = bufferToFloat32(buffer);
          if (transcriptionEnabled && whisperLive) {
            whisperLive.sendAudioData(float32);
          }
          // Also capture for recording
          if (recordingService) {
            recordingService.appendChunk(float32);
          }
        }
      });
      log('[Zoom] SDK raw audio recording started, streaming to WhisperLive');
      sdkRecordingSucceeded = true;
    } catch (recordingError: any) {
      const msg = recordingError?.message ?? String(recordingError);
      const isNoPermission = msg.includes('12') || msg.includes('NO_PERMISSION') || msg.includes('No permission');
      if (isNoPermission) {
        log('[Zoom] SDK raw audio not available (license missing). Falling back to PulseAudio capture...');
        // Fall back to PulseAudio capture from the null sink monitor
        try {
          await startPulseAudioCapture(whisperLive);
          log('[Zoom] PulseAudio capture started successfully');
        } catch (paError) {
          log(`[Zoom] PulseAudio capture failed: ${paError}. Staying in meeting without transcription.`);
        }
      } else {
        log(`[Zoom] Error starting SDK recording: ${msg}. Staying in meeting without transcription.`);
      }
    }

    // Set audio session start time for relative timestamps
    audioSessionStartTime = Date.now();
    log(`[Zoom] Audio session start time: ${audioSessionStartTime}`);

    // Register speaker change callback
    await sdkManager.onActiveSpeakerChange((activeUserIds: number[]) => {
      handleActiveSpeakerChange(activeUserIds, sdkManager, whisperLive, botConfig);
    });
    log('[Zoom] Speaker detection initialized');

    // Block until stopZoomRecording() is called (meeting ends or bot is removed)
    await new Promise<void>((resolve) => {
      recordingStopResolver = resolve;
    });
  } catch (error) {
    log(`[Zoom] Error in recording setup: ${error}`);
    throw error;
  }
}

export async function stopZoomRecording(): Promise<void> {
  log('[Zoom] Stopping recording');

  try {
    // Reset speaker state
    audioSessionStartTime = null;
    activeSpeakers.clear();

    // Unblock startZoomRecording's blocking wait
    if (recordingStopResolver) {
      recordingStopResolver();
      recordingStopResolver = null;
    }

    // Stop PulseAudio capture if running
    if (parecordProcess) {
      log('[Zoom] Stopping PulseAudio capture...');
      parecordProcess.kill('SIGTERM');
      parecordProcess = null;
    }

    const sdkManager = getSDKManager();
    await sdkManager.stopRecording();

    if (whisperSocket) {
      whisperSocket.close();
      whisperSocket = null;
    }

    whisperLive = null;

    // Finalize the audio recording file
    if (recordingService) {
      try {
        await recordingService.finalize();
        log('[Zoom] Audio recording finalized');
      } catch (err: any) {
        log(`[Zoom] Error finalizing audio recording: ${err.message}`);
      }
    }

    log('[Zoom] Recording stopped');
  } catch (error) {
    log(`[Zoom] Error stopping recording: ${error}`);
  }
}

/**
 * Get the RecordingService instance for upload handling in performGracefulLeave.
 */
export function getZoomRecordingService(): RecordingService | null {
  return recordingService;
}

// Helper function to convert PCM Int16 buffer to Float32Array
function bufferToFloat32(buffer: Buffer): Float32Array {
  const int16 = new Int16Array(buffer.buffer, buffer.byteOffset, buffer.length / 2);
  const float32 = new Float32Array(int16.length);

  for (let i = 0; i < int16.length; i++) {
    // Normalize int16 (-32768 to 32767) to float32 (-1.0 to 1.0)
    float32[i] = int16[i] / 32768.0;
  }

  return float32;
}

/**
 * Start PulseAudio capture from the zoom_sink monitor.
 * Captures raw PCM audio and forwards to WhisperLive.
 */
async function startPulseAudioCapture(whisperLive: WhisperLiveService | null): Promise<void> {
  return new Promise((resolve, reject) => {
    // Spawn parecord to capture from zoom_sink monitor
    // Output: raw PCM Int16LE, 16kHz, mono
    parecordProcess = spawn('parecord', [
      '--raw',
      '--format=s16le',
      '--rate=16000',
      '--channels=1',
      '--device=zoom_sink.monitor'
    ]);

    if (!parecordProcess || !parecordProcess.stdout) {
      reject(new Error('Failed to start parecord process'));
      return;
    }

    let started = false;

    // Forward captured audio to WhisperLive
    parecordProcess.stdout.on('data', (chunk: Buffer) => {
      if (!started) {
        log('[Zoom] PulseAudio capture receiving audio data');
        started = true;
        resolve();
      }
      if (whisperLive) {
        const float32 = bufferToFloat32(chunk);
        whisperLive.sendAudioData(float32);
        // Also capture for recording
        if (recordingService) {
          recordingService.appendPCMBuffer(chunk);
        }
      } else if (recordingService) {
        recordingService.appendPCMBuffer(chunk);
      }
    });

    parecordProcess.stderr?.on('data', (data: Buffer) => {
      log(`[Zoom] parecord stderr: ${data.toString()}`);
    });

    parecordProcess.on('error', (error: Error) => {
      log(`[Zoom] parecord process error: ${error.message}`);
      if (!started) {
        reject(error);
      }
    });

    parecordProcess.on('exit', (code: number | null, signal: string | null) => {
      log(`[Zoom] parecord exited: code=${code}, signal=${signal}`);
      parecordProcess = null;
    });

    // Resolve after a short delay even if no data yet (optimistic)
    setTimeout(() => {
      if (!started) {
        log('[Zoom] PulseAudio capture started (waiting for audio data)');
        resolve();
      }
    }, 1000);
  });
}

/**
 * Handle active speaker changes from Zoom SDK.
 * Tracks which users started/stopped speaking and sends events to WhisperLive.
 */
function handleActiveSpeakerChange(
  activeUserIds: number[],
  sdkManager: any,
  whisperLive: WhisperLiveService | null,
  botConfig: BotConfig
): void {
  if (!whisperLive || !audioSessionStartTime) {
    return;
  }

  const currentSpeakers = new Set(activeUserIds);
  const relativeTimestampMs = Date.now() - audioSessionStartTime;

  // Find users who started speaking (new in currentSpeakers, not in activeSpeakers)
  for (const userId of currentSpeakers) {
    if (!activeSpeakers.has(userId)) {
      const userInfo = sdkManager.getUserInfo(userId);
      if (userInfo) {
        log(`🎤 [Zoom] SPEAKER_START: ${userInfo.userName} (ID: ${userId})`);
        whisperLive.sendSpeakerEvent(
          'SPEAKER_START',
          userInfo.userName,
          String(userId),
          relativeTimestampMs,
          botConfig
        );
      }
    }
  }

  // Find users who stopped speaking (in activeSpeakers, not in currentSpeakers)
  for (const userId of activeSpeakers) {
    if (!currentSpeakers.has(userId)) {
      const userInfo = sdkManager.getUserInfo(userId);
      if (userInfo) {
        log(`🔇 [Zoom] SPEAKER_END: ${userInfo.userName} (ID: ${userId})`);
        whisperLive.sendSpeakerEvent(
          'SPEAKER_END',
          userInfo.userName,
          String(userId),
          relativeTimestampMs,
          botConfig
        );
      }
    }
  }

  // Update active speakers set
  activeSpeakers = currentSpeakers;
}
