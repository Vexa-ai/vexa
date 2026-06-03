import { Page } from 'playwright';
import { getSDKManager } from './join';
import { log } from '../../../utils';

export function startZoomRemovalMonitor(
  page: Page | null,
  onRemoval?: () => void | Promise<void>
): () => void {
  log('[Zoom] Starting removal monitor');

  try {
    const sdkManager = getSDKManager();
    const sdk = sdkManager.nativeSDK;

    if (!sdk) {
      log('[Zoom] No native SDK available (stub mode), skipping removal monitor');
      return () => {}; // Return no-op cleanup function
    }

    // AIS-151 Fix #6: cancelled flag prevents the callback firing after teardown.
    // SDK has no offMeetingStatus — closure flag is the correct cancel pattern.
    let cancelled = false;
    sdk.onMeetingStatus((status: any) => {
      if (cancelled) return;
      log(`[Zoom] Meeting status change: ${status.status}`);

      // Trigger removal callback on meeting end or failure
      if (status.status === 'ended' || status.status === 'failed' || status.status === 'removed') {
        log(`[Zoom] Meeting ${status.status}, triggering removal callback`);
        if (onRemoval) {
          onRemoval();
        }
      }
    });

    return () => {
      cancelled = true;
      log('[Zoom] Removal monitor cleanup');
    };
  } catch (error) {
    log(`[Zoom] Error setting up removal monitor: ${error}`);
    return () => {}; // Return no-op cleanup function on error
  }
}
