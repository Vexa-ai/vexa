import { Page } from 'playwright';
import { BotConfig } from '../../../types';
import { runMeetingFlow, PlatformStrategies } from '../../shared/meetingFlow';
import { joinZoomWebMeeting } from './join';
import { waitForZoomWebAdmission, checkZoomWebAdmissionSilent } from './admission';
import { prepareZoomWebMeeting } from './prepare';
import { startZoomWebRecording } from './recording';
import { startZoomWebRemovalMonitor } from './removal';
import { leaveZoomWebMeeting } from './leave';

export async function handleZoomWeb(
  botConfig: BotConfig,
  page: Page | null,
  gracefulLeaveFunction: (page: Page | null, exitCode: number, reason: string) => Promise<void>
): Promise<void> {
  const strategies: PlatformStrategies = {
    join: joinZoomWebMeeting,
    waitForAdmission: waitForZoomWebAdmission,
    checkAdmissionSilent: checkZoomWebAdmissionSilent,
    prepare: prepareZoomWebMeeting,
    startRecording: startZoomWebRecording,
    startRemovalMonitor: startZoomWebRemovalMonitor,
    leave: leaveZoomWebMeeting,
  };

  await runMeetingFlow('zoom', botConfig, page, gracefulLeaveFunction, strategies);
}

export { leaveZoomWebMeeting as leaveZoomWeb };
