import { Page } from "playwright";
import { log, callStartupCallback } from "../../utils";
import { BotConfig } from "../../types";
import { hasStopSignalReceived } from "../../index";

// Import modular functions
import { joinMicrosoftTeams } from "./join";
import { waitForTeamsMeetingAdmission } from "./admission";
import { startTeamsRecording } from "./recording";
import { prepareForRecording, leaveMicrosoftTeams } from "./leave";
import { startTeamsRemovalMonitor } from "./removal";

export async function handleMicrosoftTeams(
  botConfig: BotConfig,
  page: Page,
  gracefulLeaveFunction: (page: Page | null, exitCode: number, reason: string, errorDetails?: any) => Promise<void>
): Promise<void> {
  
  if (!botConfig.meetingUrl) {
    log("Error: Meeting URL is required for Microsoft Teams but is null.");
    await gracefulLeaveFunction(page, 1, "missing_meeting_url");
    return;
  }

  try {
    // Join the Teams meeting
    await joinMicrosoftTeams(page, botConfig);

    if (hasStopSignalReceived()) {
      log("⛔ Stop signal detected before admission wait. Exiting without joining.");
      await gracefulLeaveFunction(page, 0, "stop_requested_pre_admission");
      return;
    }
   
    log("Starting WebSocket connection while waiting for Teams meeting admission");
    
      // Run both processes concurrently
      const [admissionResult] = await Promise.all([
        // Wait for admission to the Teams meeting
        waitForTeamsMeetingAdmission(page, botConfig.automaticLeave.waitingRoomTimeout, botConfig).catch((error) => {
          log("Teams meeting admission failed: " + error.message);
          
          // Check if the error indicates rejection by admin
          if (error.message.includes("rejected by meeting admin")) {
            return { admitted: false, rejected: true, reason: "admission_rejected_by_admin" };
          }
          
          return { admitted: false, rejected: false, reason: "admission_timeout" };
        }),

        // Prepare for recording (expose functions, etc.) while waiting for admission
        prepareForRecording(page, botConfig),
      ]);
    
    // Handle different admission outcomes
    const isAdmitted = admissionResult === true || (typeof admissionResult === 'object' && admissionResult.admitted);
    
    if (!isAdmitted) {
      const rejectionInfo = typeof admissionResult === 'object' ? admissionResult : { reason: "admission_timeout" };
      
      if ('rejected' in rejectionInfo && rejectionInfo.rejected) {
        log("🚨 Bot was rejected from the Teams meeting by admin. Exiting gracefully...");
        
        // For rejection, we don't need to attempt leave since we're not in the meeting
        await gracefulLeaveFunction(page, 0, rejectionInfo.reason);
        return;
      } else {
        log("Bot was not admitted into the Teams meeting within the timeout period. Attempting graceful leave...");
        
        // Attempt stateless leave before calling gracefulLeaveFunction (for timeout scenarios)
        try {
          const result = await page.evaluate(async () => {
            if (typeof (window as any).performLeaveAction === "function") {
              return await (window as any).performLeaveAction();
            }
            return false;
          });
          
          if (result) {
            log("✅ Successfully performed graceful leave during admission timeout");
          } else {
            log("⚠️ Could not perform graceful leave during admission timeout - continuing with normal exit");
          }
        } catch (leaveError: any) {
          log(`⚠️ Error during graceful leave attempt: ${leaveError.message} - continuing with normal exit`);
        }
        
        await gracefulLeaveFunction(page, 0, rejectionInfo.reason);
        return;
      }
    }

    log("Successfully admitted to the Teams meeting, starting recording");
    
    // --- Call startup callback to notify bot-manager that bot is active ---
    try {
      await callStartupCallback(botConfig);
      log("Startup callback sent successfully");
    } catch (callbackError: any) {
      log(`Warning: Failed to send startup callback: ${callbackError.message}. Continuing with recording...`);
    }
    
    // Start removal monitoring and race against it
    let signalRemoval: (() => void) | null = null;
    const removalPromise = new Promise<never>((_, reject) => {
      signalRemoval = () => reject(new Error("TEAMS_BOT_REMOVED_BY_ADMIN"));
    });
    const stopRemovalMonitor = startTeamsRemovalMonitor(page, () => { if (signalRemoval) signalRemoval(); });
    
    try {
      // Start recording with Teams-specific logic and race against removal
      await Promise.race([
        startTeamsRecording(page, botConfig),
        removalPromise
      ]);
  
  // If we reach here, recording finished normally (not due to removal)
  log("Teams recording completed normally");
  await gracefulLeaveFunction(page, 0, "normal_completion");
  } catch (error: any) {
    // Handle removal detection specifically (check for the error message with or without page.evaluate prefix)
    if (error.message === "TEAMS_BOT_REMOVED_BY_ADMIN" || error.message.includes("TEAMS_BOT_REMOVED_BY_ADMIN")) {
      log("🚨 Bot was removed from Teams meeting by admin. Exiting gracefully...");
      await gracefulLeaveFunction(page, 0, "removed_by_admin");
      return;
    }
    
    // Handle left alone timeout scenarios
    if (error.message === "TEAMS_BOT_LEFT_ALONE_TIMEOUT" || error.message.includes("TEAMS_BOT_LEFT_ALONE_TIMEOUT")) {
      log("⏰ Bot was left alone in Teams meeting for 10 seconds. Exiting gracefully...");
      await gracefulLeaveFunction(page, 0, "left_alone_timeout");
      return;
    }
    
    if (error.message === "TEAMS_BOT_STARTUP_ALONE_TIMEOUT" || error.message.includes("TEAMS_BOT_STARTUP_ALONE_TIMEOUT")) {
      log("⏰ Bot was alone during startup for 10 seconds. Exiting gracefully...");
      await gracefulLeaveFunction(page, 0, "startup_alone_timeout");
      return;
    }
    
    console.error("Error after Teams join attempt (admission/recording setup): " + error.message);
    log("Error after Teams join attempt (admission/recording setup): " + error.message + ". Triggering graceful leave.");
    
    // Capture detailed error information for debugging
    const errorDetails = {
      error_message: error.message,
      error_stack: error.stack,
      error_name: error.name,
      context: "post_join_setup_error",
      platform: "teams",
      timestamp: new Date().toISOString()
    };
    
    // Use a general error code here, as it could be various issues.
    await gracefulLeaveFunction(page, 1, "post_join_setup_error", errorDetails);
    return;
    } finally {
      // Always stop removal monitoring
      stopRemovalMonitor();
  }

} catch (error: any) {
    // Handle removal detection specifically (check for the error message with or without page.evaluate prefix)
    if (error.message === "TEAMS_BOT_REMOVED_BY_ADMIN" || error.message.includes("TEAMS_BOT_REMOVED_BY_ADMIN")) {
      log("🚨 Bot was removed from Teams meeting by admin. Exiting gracefully...");
      await gracefulLeaveFunction(page, 0, "removed_by_admin");
      return;
    }
    
    // Handle left alone timeout scenarios
    if (error.message === "TEAMS_BOT_LEFT_ALONE_TIMEOUT" || error.message.includes("TEAMS_BOT_LEFT_ALONE_TIMEOUT")) {
      log("⏰ Bot was left alone in Teams meeting for 10 seconds. Exiting gracefully...");
      await gracefulLeaveFunction(page, 0, "left_alone_timeout");
      return;
    }
    
    if (error.message === "TEAMS_BOT_STARTUP_ALONE_TIMEOUT" || error.message.includes("TEAMS_BOT_STARTUP_ALONE_TIMEOUT")) {
      log("⏰ Bot was alone during startup for 10 seconds. Exiting gracefully...");
      await gracefulLeaveFunction(page, 0, "startup_alone_timeout");
      return;
    }
    
    console.error("Error after Teams join attempt (admission/recording setup): " + error.message);
    log("Error after Teams join attempt (admission/recording setup): " + error.message + ". Triggering graceful leave.");
    
    // Capture detailed error information for debugging
    const errorDetails = {
      error_message: error.message,
      error_stack: error.stack,
      error_name: error.name,
      context: "post_join_setup_error",
      platform: "teams",
      timestamp: new Date().toISOString()
    };
    
    // Use a general error code here, as it could be various issues.
    await gracefulLeaveFunction(page, 1, "teams_error", errorDetails);
  }
}

// Export the leave function for external use
export { leaveMicrosoftTeams };
