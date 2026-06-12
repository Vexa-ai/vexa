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
  setHooks,
} from "@vexa/meet-join";
import { startGoogleRecording } from "./recording";
import { callStatusChangeCallback, MeetingStatus } from "../../services/unified-callback";
import { callNeedsHumanHelpCallback } from "../../utils";

// --- Google Meet Main Handler ---

export async function handleGoogleMeet(
  botConfig: BotConfig,
  page: Page,
  gracefulLeaveFunction: (page: Page | null, exitCode: number, reason: string, errorDetails?: any) => Promise<void>
): Promise<void> {

  // The brick's contract-out: bridge its JoinState (Hooks.onState) to the bot's
  // real lifecycle wire (callStatusChangeCallback -> meeting-api). Without this the
  // brick detects admission but the platform never hears it ("code wired, contract
  // not wired"). The map adapts the brick vocabulary to the platform's. (lifecycle.v1 embryo.)
  const STATE_TO_STATUS: Record<string, MeetingStatus> = {
    joining: "joining",
    awaiting_admission: "awaiting_admission",
    admitted: "active",
  };
  setHooks({
    onState: async (state, detail) => {
      const status = STATE_TO_STATUS[state];
      if (status) {
        await callStatusChangeCallback(botConfig, status).catch(() => {});
      } else if (state === "needs_human_help" || state === "blocked" || state === "rejected") {
        await callNeedsHumanHelpCallback(botConfig, detail?.reason || state, detail?.screenshotPath).catch(() => {});
      }
    },
  });

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