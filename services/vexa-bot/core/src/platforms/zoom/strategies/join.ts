import { Page } from 'playwright';
import { BotConfig } from '../../../types';
import { ZoomSDKManager } from '../sdk-manager';
import { log, callJoiningCallback } from '../../../utils';

let sdkManager: ZoomSDKManager | null = null;

export async function joinZoomMeeting(page: Page | null, botConfig: BotConfig): Promise<void> {
  log(`[Zoom] Initializing SDK and joining meeting: ${botConfig.meetingUrl}`);

  // Signal "joining" so bot-manager transitions: requested → joining → active
  await callJoiningCallback(botConfig);

  // Validate environment variables
  const clientId = process.env.ZOOM_CLIENT_ID;
  const clientSecret = process.env.ZOOM_CLIENT_SECRET;

  if (!clientId || !clientSecret) {
    throw new Error('[Zoom] ZOOM_CLIENT_ID and ZOOM_CLIENT_SECRET environment variables are required');
  }

  // Create SDK manager
  sdkManager = new ZoomSDKManager(botConfig);
  sdkManager.ensureSdkAvailable();

  try {
    // Initialize SDK
    await sdkManager.initialize();
    log('[Zoom] SDK initialized');

    // Authenticate with Zoom
    await sdkManager.authenticate(clientId, clientSecret);
    log('[Zoom] Authentication successful');

    // Join meeting
    await sdkManager.joinMeeting(botConfig.meetingUrl!);
    log('[Zoom] Successfully joined meeting');

    // Join VoIP audio to enable audio reception
    await sdkManager.joinAudio();
    log('[Zoom] Successfully joined VoIP audio');
  } catch (error) {
    log(`[Zoom] Error during join: ${error}`);
    throw error;
  }
}

export function getSDKManager(): ZoomSDKManager {
  if (!sdkManager) {
    throw new Error('[Zoom] SDK Manager not initialized');
  }
  return sdkManager;
}
