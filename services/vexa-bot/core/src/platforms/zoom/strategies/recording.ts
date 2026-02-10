import { Page } from 'playwright';
import { BotConfig } from '../../../types';
import { WhisperLiveService } from '../../../services/whisperlive';
import { getSDKManager } from './join';
import { log } from '../../../utils';

let whisperLive: WhisperLiveService | null = null;

export async function startZoomRecording(page: Page | null, botConfig: BotConfig): Promise<void> {
  log('[Zoom] Starting audio recording and WhisperLive connection');

  const sdkManager = getSDKManager();

  try {
    // Initialize WhisperLive connection
    const whisperLiveUrl = process.env.WHISPER_LIVE_URL;
    if (!whisperLiveUrl) {
      throw new Error('[Zoom] WHISPER_LIVE_URL environment variable is required');
    }

    whisperLive = new WhisperLiveService(whisperLiveUrl, botConfig);
    await whisperLive.connect();
    log('[Zoom] WhisperLive connected');

    // Start SDK audio capture with callback to send to WhisperLive
    await sdkManager.startRecording((buffer: Buffer, sampleRate: number) => {
      if (whisperLive) {
        // Convert PCM Int16 buffer to Float32Array
        const float32 = bufferToFloat32(buffer);
        whisperLive.sendAudioChunk(float32);
      }
    });

    log('[Zoom] Recording started, streaming to WhisperLive at 16kHz');
  } catch (error) {
    log(`[Zoom] Error starting recording: ${error}`);
    throw error;
  }
}

export async function stopZoomRecording(): Promise<void> {
  log('[Zoom] Stopping recording');

  try {
    const sdkManager = getSDKManager();
    await sdkManager.stopRecording();

    if (whisperLive) {
      await whisperLive.disconnect();
      whisperLive = null;
    }

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
