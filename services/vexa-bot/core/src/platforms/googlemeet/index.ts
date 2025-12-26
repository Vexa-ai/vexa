import { Page } from "playwright";
import { BotConfig } from "../../types";
import { runMeetingFlow, PlatformStrategies } from "../shared/meetingFlow";

// Import modular functions
import { joinGoogleMeeting } from "./join";
import { waitForGoogleMeetingAdmission, checkForGoogleAdmissionSilent } from "./admission";
import { startGoogleRecording } from "./recording";
import { prepareForRecording, leaveGoogleMeet } from "./leave";
import { startGoogleRemovalMonitor } from "./removal";

// --- Google Meet Main Handler ---

export async function handleGoogleMeet(
  botConfig: BotConfig,
  page: Page,
  gracefulLeaveFunction: (page: Page | null, exitCode: number, reason: string, errorDetails?: any) => Promise<void>
): Promise<void> {
  
  const strategies: PlatformStrategies = {
    join: async (page: Page, botConfig: BotConfig) => {
      await joinGoogleMeeting(page, botConfig.meetingUrl!, botConfig.botName, botConfig);
    },
    waitForAdmission: waitForGoogleMeetingAdmission,
    checkAdmissionSilent: checkForGoogleAdmissionSilent,
    prepare: prepareForRecording,
    startRecording: startGoogleRecording,
    startRemovalMonitor: startGoogleRemovalMonitor,
    leave: leaveGoogleMeet
  };

  await runMeetingFlow(
    "google_meet",
    botConfig,
    page,
    gracefulLeaveFunction,
    strategies
  );
}

// Export the leave function for external use
export { leaveGoogleMeet };