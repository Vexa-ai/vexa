import { Page } from 'playwright';
import { BotConfig } from '../../types';
import { runMeetingFlow, PlatformStrategies } from '../shared/meetingFlow';
import { joinZoomMeeting } from './strategies/join';
import { waitForZoomAdmission, checkZoomAdmissionSilent } from './strategies/admission';
import { prepareZoomRecording } from './strategies/prepare';
import { startZoomRecording } from './strategies/recording';
import { startZoomRemovalMonitor } from './strategies/removal';
import { leaveZoomMeeting } from './strategies/leave';

// zoom-sdk is the native-SDK path. Pack F of release 260422-zoom-sdk split
// the former zoom/{native,web} tree into zoom-sdk/ + zoom-web/ peer
// platforms; the previous env-var-based dispatch switch is retired. Callers
// now select the path by sending `platform=zoom_sdk` or `platform=zoom_web`.
export async function handleZoomSdk(
  botConfig: BotConfig,
  page: Page | null,
  gracefulLeaveFunction: (page: Page | null, exitCode: number, reason: string) => Promise<void>
): Promise<void> {
  const strategies: PlatformStrategies = {
    join: joinZoomMeeting,
    waitForAdmission: waitForZoomAdmission,
    checkAdmissionSilent: checkZoomAdmissionSilent,
    prepare: prepareZoomRecording,
    startRecording: startZoomRecording,
    startRemovalMonitor: startZoomRemovalMonitor,
    leave: leaveZoomMeeting
  };

  await runMeetingFlow("zoom_sdk", botConfig, page, gracefulLeaveFunction, strategies);
}

// Back-compat alias — kept for one cycle while legacy `platform: "zoom"` is
// supported at the dispatcher. Remove alongside the legacy alias in cycle
// after 260422-zoom-sdk.
export const handleZoom = handleZoomSdk;

// Export for graceful leave in index.ts
export { leaveZoomMeeting as leaveZoom };
