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
  
  // Google Meet is browser-based, so page is always non-null
  // Cast to satisfy PlatformStrategies interface which supports SDK-based platforms (Page | null)
  const strategies: PlatformStrategies = {
    join: async (page: Page | null, botConfig: BotConfig) => {
      await joinGoogleMeeting(page as Page, botConfig.meetingUrl!, botConfig.botName, botConfig);
    },
    waitForAdmission: waitForGoogleMeetingAdmission as any,
    checkAdmissionSilent: checkForGoogleAdmissionSilent as any,
    prepare: prepareForRecording as any,
    startRecording: startGoogleRecording as any,
    startRemovalMonitor: startGoogleRemovalMonitor as any,
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