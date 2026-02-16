import { Page } from 'playwright';
import { BotConfig } from '../../types';
import { runMeetingFlow, PlatformStrategies } from '../shared/meetingFlow';
import { joinZoomMeeting } from './strategies/join';
import { waitForZoomAdmission, checkZoomAdmissionSilent } from './strategies/admission';
import { prepareZoomRecording } from './strategies/prepare';
import { startZoomRecording } from './strategies/recording';
import { startZoomRemovalMonitor } from './strategies/removal';
import { leaveZoomMeeting } from './strategies/leave';

export async function handleZoom(
  botConfig: BotConfig,
  page: Page | null, // May be null for SDK-only approach
  gracefulLeaveFunction: (page: Page | null, exitCode: number, reason: string) => Promise<void>
): Promise<void> {

  // Define platform strategies for Zoom
  const strategies: PlatformStrategies = {
    join: joinZoomMeeting,
    waitForAdmission: waitForZoomAdmission,
    checkAdmissionSilent: checkZoomAdmissionSilent,
    prepare: prepareZoomRecording,
    startRecording: startZoomRecording,
    startRemovalMonitor: startZoomRemovalMonitor,
    leave: leaveZoomMeeting
  };

  // Use shared meeting flow orchestration
  await runMeetingFlow("zoom", botConfig, page, gracefulLeaveFunction, strategies);
}

// Export for graceful leave in index.ts
export { leaveZoomMeeting as leaveZoom };
