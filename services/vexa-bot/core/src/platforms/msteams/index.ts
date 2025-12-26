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
  
  const strategies: PlatformStrategies = {
    join: joinMicrosoftTeams,
    waitForAdmission: waitForTeamsMeetingAdmission,
    checkAdmissionSilent: checkForTeamsAdmissionSilent,
    prepare: prepareForRecording,
    startRecording: startTeamsRecording,
    startRemovalMonitor: startTeamsRemovalMonitor,
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
