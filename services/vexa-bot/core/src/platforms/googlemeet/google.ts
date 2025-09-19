import { Page } from "playwright";
import { log, callStartupCallback } from "../../utils";
import { BotConfig } from "../../types";
import { hasStopSignalReceived } from "../../index";

// Import modular functions
import { joinGoogleMeeting } from "./join";
import { waitForGoogleMeetingAdmission } from "./admission";
import { startGoogleRecording } from "./recording";
import { prepareForRecording, leaveGoogleMeet } from "./leave";
import { startGoogleRemovalMonitor } from "./removal";

// --- Google Meet Main Handler ---

export async function handleGoogleMeet(
  botConfig: BotConfig,
  page: Page,
  gracefulLeaveFunction: (page: Page | null, exitCode: number, reason: string, errorDetails?: any) => Promise<void>
): Promise<void> {
  
  if (!botConfig.meetingUrl) {
    log("Error: Meeting URL is required for Google Meet but is null.");
    await gracefulLeaveFunction(page, 1, "missing_meeting_url");
    return;
  }

  log("Joining Google Meet");
  try {
    await joinGoogleMeeting(page, botConfig.meetingUrl, botConfig.botName, botConfig);
  } catch (error: any) {
    console.error("Error during joinGoogleMeeting: " + error.message);
    log("Error during joinGoogleMeeting: " + error.message + ". Triggering graceful leave.");
    
    const errorDetails = {
      error_message: error.message,
      error_stack: error.stack,
      error_name: error.name,
      context: "join_meeting_error",
      platform: "google_meet",
      timestamp: new Date().toISOString()
    };
    
    await gracefulLeaveFunction(page, 1, "join_meeting_error", errorDetails);
    return;
  }

  // Add stop signal guard before admission wait (canonical Teams pattern)
  if (hasStopSignalReceived()) {
    log("⛔ Stop signal detected before admission wait. Exiting without joining.");
    await gracefulLeaveFunction(page, 0, "stop_requested_pre_admission");
    return;
  }

  // Setup websocket connection and meeting admission concurrently
  log("Starting WebSocket connection while waiting for Google Meet meeting admission");
  try {
    // Run both processes concurrently
    const [admissionResult] = await Promise.all([
      // Wait for admission to the meeting
      waitForGoogleMeetingAdmission(page, botConfig.automaticLeave.waitingRoomTimeout, botConfig).catch((error) => {
        log("Google Meet meeting admission failed: " + error.message);
        
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
        log("🚨 Bot was rejected from the Google Meet meeting by admin. Exiting gracefully...");
        
        // For rejection, we don't need to attempt leave since we're not in the meeting
        await gracefulLeaveFunction(page, 0, rejectionInfo.reason);
        return;
      } else {
        log("Bot was not admitted into the Google Meet meeting within the timeout period. Attempting graceful leave...");
        
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

    log("Successfully admitted to the Google Meet meeting, starting recording");
    
    // --- Call startup callback to notify bot-manager that bot is active ---
    try {
      await callStartupCallback(botConfig);
      log("Startup callback sent successfully");
    } catch (callbackError: any) {
      log(`Warning: Failed to send startup callback: ${callbackError.message}. Continuing with recording...`);
    }
    
    // Start removal monitoring and recording
    let signalRemoval: (() => void) | null = null;
    const removalPromise = new Promise<never>((_, reject) => {
      signalRemoval = () => reject(new Error("GOOGLE_MEET_BOT_REMOVED_BY_ADMIN"));
    });
    const stopRemovalMonitor = startGoogleRemovalMonitor(page, () => { if (signalRemoval) signalRemoval(); });
    
    try {
      // Start recording with Google Meet-specific logic and race against removal
      await Promise.race([
        startGoogleRecording(page, botConfig),
        removalPromise
      ]);
    
    // If we reach here, recording finished normally (not due to removal)
    log("Google Meet recording completed normally");
    await gracefulLeaveFunction(page, 0, "normal_completion");
    } catch (error: any) {
      // Handle removal detection specifically (check for the error message with or without page.evaluate prefix)
      if (error.message === "GOOGLE_MEET_BOT_REMOVED_BY_ADMIN" || error.message.includes("GOOGLE_MEET_BOT_REMOVED_BY_ADMIN")) {
        log("🚨 Bot was removed from Google Meet meeting by admin. Exiting gracefully...");
        await gracefulLeaveFunction(page, 0, "removed_by_admin");
        return;
      }
      
      // Handle left alone timeout scenarios
      if (error.message === "GOOGLE_MEET_BOT_LEFT_ALONE_TIMEOUT" || error.message.includes("GOOGLE_MEET_BOT_LEFT_ALONE_TIMEOUT")) {
        log("⏰ Bot was left alone in Google Meet meeting for 10 seconds. Exiting gracefully...");
        await gracefulLeaveFunction(page, 0, "left_alone_timeout");
        return;
      }
      
      if (error.message === "GOOGLE_MEET_BOT_STARTUP_ALONE_TIMEOUT" || error.message.includes("GOOGLE_MEET_BOT_STARTUP_ALONE_TIMEOUT")) {
        log("⏰ Bot was alone during startup for 20 minutes. Exiting gracefully...");
        await gracefulLeaveFunction(page, 0, "startup_alone_timeout");
        return;
      }
      
      console.error("Error after Google Meet join attempt (admission/recording setup): " + error.message);
      log("Error after Google Meet join attempt (admission/recording setup): " + error.message + ". Triggering graceful leave.");
      
      // Capture detailed error information for debugging
      const errorDetails = {
        error_message: error.message,
        error_stack: error.stack,
        error_name: error.name,
        context: "post_join_setup_error",
        platform: "google_meet",
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
    if (error.message === "GOOGLE_MEET_BOT_REMOVED_BY_ADMIN" || error.message.includes("GOOGLE_MEET_BOT_REMOVED_BY_ADMIN")) {
      log("🚨 Bot was removed from Google Meet meeting by admin. Exiting gracefully...");
      await gracefulLeaveFunction(page, 0, "removed_by_admin");
      return;
    }
    
    // Handle left alone timeout scenarios
    if (error.message === "GOOGLE_MEET_BOT_LEFT_ALONE_TIMEOUT" || error.message.includes("GOOGLE_MEET_BOT_LEFT_ALONE_TIMEOUT")) {
      log("⏰ Bot was left alone in Google Meet meeting for 10 seconds. Exiting gracefully...");
      await gracefulLeaveFunction(page, 0, "left_alone_timeout");
      return;
    }
    
    if (error.message === "GOOGLE_MEET_BOT_STARTUP_ALONE_TIMEOUT" || error.message.includes("GOOGLE_MEET_BOT_STARTUP_ALONE_TIMEOUT")) {
      log("⏰ Bot was alone during startup for 20 minutes. Exiting gracefully...");
      await gracefulLeaveFunction(page, 0, "startup_alone_timeout");
      return;
    }
    
    console.error("Error after Google Meet join attempt (admission/recording setup): " + error.message);
    log("Error after Google Meet join attempt (admission/recording setup): " + error.message + ". Triggering graceful leave.");
    
    // Capture detailed error information for debugging
    const errorDetails = {
      error_message: error.message,
      error_stack: error.stack,
      error_name: error.name,
      context: "post_join_setup_error",
      platform: "google_meet",
      timestamp: new Date().toISOString()
    };
    
    // Use a general error code here, as it could be various issues.
    await gracefulLeaveFunction(page, 1, "post_join_setup_error", errorDetails);
    return;
  }
}

// Export the leave function for external use
export { leaveGoogleMeet };