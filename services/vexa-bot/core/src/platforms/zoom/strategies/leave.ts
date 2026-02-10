import { Page } from 'playwright';
import { BotConfig } from '../../../types';
import { LeaveReason } from '../../shared/meetingFlow';
import { getSDKManager } from './join';
import { stopZoomRecording } from './recording';
import { log } from '../../../utils';

export async function leaveZoomMeeting(
  page: Page | null,
  botConfig?: BotConfig,
  reason?: LeaveReason
): Promise<boolean> {
  log(`[Zoom] Leaving meeting: ${reason || 'graceful shutdown'}`);

  try {
    // Stop recording first
    await stopZoomRecording();

    // Leave meeting via SDK
    const sdkManager = getSDKManager();
    await sdkManager.leaveMeeting();

    // Cleanup SDK resources
    await sdkManager.cleanup();

    log('[Zoom] Successfully left meeting and cleaned up');
    return true;
  } catch (error) {
    log(`[Zoom] Error leaving meeting: ${error}`);
    // Return true even on error to allow graceful shutdown
    return true;
  }
}
