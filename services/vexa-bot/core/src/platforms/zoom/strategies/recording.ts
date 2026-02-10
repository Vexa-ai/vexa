import { Page } from 'playwright';
import { BotConfig } from '../../../types';
import { WhisperLiveService } from '../../../services/whisperlive';
import { getSDKManager } from './join';
import { log } from '../../../utils';

let whisperLive: WhisperLiveService | null = null;
let whisperSocket: WebSocket | null = null;
let recordingStopResolver: (() => void) | null = null;

export async function startZoomRecording(page: Page | null, botConfig: BotConfig): Promise<void> {
  log('[Zoom] Starting audio recording and WhisperLive connection');

  const sdkManager = getSDKManager();

  try {
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

    // Start SDK audio capture with callback to send to WhisperLive
    await sdkManager.startRecording((buffer: Buffer, sampleRate: number) => {
      if (whisperLive) {
        // Convert PCM Int16 buffer to Float32Array
        const float32 = bufferToFloat32(buffer);
        whisperLive.sendAudioData(float32);
      }
    });

    log('[Zoom] Recording started, streaming to WhisperLive at 16kHz');

    // Block until stopZoomRecording() is called (meeting ends or bot is removed)
    await new Promise<void>((resolve) => {
      recordingStopResolver = resolve;
    });
  } catch (error) {
    log(`[Zoom] Error starting recording: ${error}`);
    throw error;
  }
}

export async function stopZoomRecording(): Promise<void> {
  log('[Zoom] Stopping recording');

  try {
    // Unblock startZoomRecording's blocking wait
    if (recordingStopResolver) {
      recordingStopResolver();
      recordingStopResolver = null;
    }

    const sdkManager = getSDKManager();
    await sdkManager.stopRecording();

    if (whisperSocket) {
      whisperSocket.close();
      whisperSocket = null;
    }

    whisperLive = null;

    log('[Zoom] Recording stopped');
  } catch (error) {
    log(`[Zoom] Error stopping recording: ${error}`);
  }
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
