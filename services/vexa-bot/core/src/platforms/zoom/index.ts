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

  // Route to web-based Playwright implementation when ZOOM_WEB=true
  // or when the native SDK addon is not available
  const useWebClient = process.env.ZOOM_WEB === 'true';
  if (useWebClient) {
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
