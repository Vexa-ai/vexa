import { Page } from 'playwright';
import { BotConfig } from '../../types';
import { runMeetingFlow, PlatformStrategies } from '../shared/meetingFlow';
import { joinZoomMeeting } from './strategies/join';
import { waitForZoomAdmission, checkZoomAdmissionSilent } from './strategies/admission';
import { prepareZoomRecording } from './strategies/prepare';
import { startZoomRecording } from './strategies/recording';
import { startZoomRemovalMonitor } from './strategies/removal';
import { leaveZoomMeeting } from './strategies/leave';
import { handleZoomWeb, leaveZoomWeb } from './web/index';

export async function handleZoom(
  botConfig: BotConfig,
  page: Page | null,
  gracefulLeaveFunction: (page: Page | null, exitCode: number, reason: string) => Promise<void>
): Promise<void> {

  // Default: web-based Playwright implementation (no proprietary SDK creds needed,
  // works on every deployment mode out-of-the-box).
  // Opt into the native Zoom Meeting SDK path by setting ZOOM_SDK=true
  // (requires ZOOM_CLIENT_ID + ZOOM_CLIENT_SECRET). The legacy
  // `ZOOM_WEB=true` env-var is still honoured for backward-compat —
  // both `ZOOM_WEB=true` and the new default route to handleZoomWeb.
  // (Wave 3 will retire both env vars in favour of an explicit
  // `platform: zoom_sdk` enum value.)
  const useNativeSdk = process.env.ZOOM_SDK === 'true' && process.env.ZOOM_WEB !== 'true';
  if (!useNativeSdk) {
    return handleZoomWeb(botConfig, page, gracefulLeaveFunction);
  }

  // Native SDK path (requires proprietary Zoom Meeting SDK binaries)
  const strategies: PlatformStrategies = {
    join: joinZoomMeeting,
    waitForAdmission: waitForZoomAdmission,
    checkAdmissionSilent: checkZoomAdmissionSilent,
    prepare: prepareZoomRecording,
    startRecording: startZoomRecording,
    startRemovalMonitor: startZoomRemovalMonitor,
    leave: leaveZoomMeeting
  };

  await runMeetingFlow("zoom", botConfig, page, gracefulLeaveFunction, strategies);
}

// Export for graceful leave in index.ts
export { leaveZoomMeeting as leaveZoom };
export { leaveZoomWeb };
