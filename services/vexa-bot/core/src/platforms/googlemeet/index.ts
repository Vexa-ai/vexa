import { Page } from "playwright";
import { BotConfig } from "../../types";
import { runMeetingFlow, PlatformStrategies } from "../shared/meetingFlow";

// Join/admission/leave/removal: the meet-join brick (packages/meet-join).
// The bot consumes the module through its public surface — MANIFEST one-way rule.
import {
  joinGoogleMeeting,
  waitForGoogleMeetingAdmission,
  checkForGoogleAdmissionSilent,
  prepareForRecording,
  leaveGoogleMeet,
  startGoogleRemovalMonitor,
} from "@vexa/meet-join";
import { startGoogleRecording } from "./recording";

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