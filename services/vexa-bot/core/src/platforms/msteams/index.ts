import { Page } from "playwright";
import { BotConfig } from "../../types";
import { runMeetingFlow, PlatformStrategies } from "../shared/meetingFlow";

// Import modular functions
import { joinMicrosoftTeams } from "./join";
import { waitForTeamsMeetingAdmission, checkForTeamsAdmissionSilent } from "./admission";
import { startTeamsRecording } from "./recording";
import { prepareForRecording, leaveMicrosoftTeams } from "./leave";
import { startTeamsRemovalMonitor } from "./removal";

export async function handleMicrosoftTeams(
  botConfig: BotConfig,
  page: Page,
  gracefulLeaveFunction: (page: Page | null, exitCode: number, reason: string, errorDetails?: any) => Promise<void>
): Promise<void> {
  
  // Microsoft Teams is browser-based, so page is always non-null
  // Cast to satisfy PlatformStrategies interface which supports SDK-based platforms (Page | null)
  const strategies: PlatformStrategies = {
    join: joinMicrosoftTeams as any,
    waitForAdmission: waitForTeamsMeetingAdmission as any,
    checkAdmissionSilent: checkForTeamsAdmissionSilent as any,
    prepare: prepareForRecording as any,
    startRecording: startTeamsRecording as any,
    startRemovalMonitor: startTeamsRemovalMonitor as any,
    leave: leaveMicrosoftTeams
  };

  await runMeetingFlow(
    "teams",
    botConfig,
    page,
    gracefulLeaveFunction,
    strategies
  );
}

// Export the leave function for external use
export { leaveMicrosoftTeams };
