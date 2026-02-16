import { Page } from "playwright";
import { BotConfig } from "../../types";
import { log, callStartupCallback } from "../../utils";
import { hasStopSignalReceived, triggerPostAdmissionCamera, triggerPostAdmissionChat } from "../../index";

export type AdmissionDecision = {
  admitted: boolean;
  rejected?: boolean;
  reason?: string;
};

export type AdmissionResult = boolean | AdmissionDecision;

export type LeaveReason =
  | "admission_rejected_by_admin"
  | "admission_timeout"
  | "removed_by_admin"
  | "left_alone_timeout"
  | "startup_alone_timeout"
  | "normal_completion"
  | string;

function generateReasonTokens(platform: string): {
  removedToken: string;
  leftAloneToken: string;
  startupAloneToken: string;
} {
  const platformUpper = platform.toUpperCase();
  return {
    removedToken: `${platformUpper}_BOT_REMOVED_BY_ADMIN`,
    leftAloneToken: `${platformUpper}_BOT_LEFT_ALONE_TIMEOUT`,
    startupAloneToken: `${platformUpper}_BOT_STARTUP_ALONE_TIMEOUT`
  };
}

export type PlatformStrategies = {
  join: (page: Page | null, botConfig: BotConfig) => Promise<void>;
  waitForAdmission: (page: Page | null, timeoutMs: number, botConfig: BotConfig) => Promise<AdmissionResult>;
  checkAdmissionSilent: (page: Page | null) => Promise<boolean>; // Silent check without callbacks
  prepare: (page: Page | null, botConfig: BotConfig) => Promise<void>;
  startRecording: (page: Page | null, botConfig: BotConfig) => Promise<void>;
  startRemovalMonitor: (page: Page | null, onRemoval?: () => void | Promise<void>) => () => void;
  leave: (page: Page | null, botConfig?: BotConfig, reason?: LeaveReason) => Promise<boolean>;
};

export async function runMeetingFlow(
  platform: string,
  botConfig: BotConfig,
  page: Page | null,
  gracefulLeaveFunction: (page: Page | null, exitCode: number, reason: string, errorDetails?: any) => Promise<void>,
  strategies: PlatformStrategies
): Promise<void> {
  const tokens = generateReasonTokens(platform);
  if (!botConfig.meetingUrl) {
    log(`Error: Meeting URL is required for ${platform} but is null.`);
    await gracefulLeaveFunction(page, 1, "missing_meeting_url");
    return;
  }

  // Join
  try {
    await strategies.join(page, botConfig);
  } catch (error: any) {
    const errorDetails = {
      error_message: error?.message,
      error_stack: error?.stack,
      error_name: error?.name,
      context: "join_meeting_error",
      platform,
      timestamp: new Date().toISOString()
    };
    await gracefulLeaveFunction(page, 1, "join_meeting_error", errorDetails);
    return;
  }

  // Stop-signal guard
  if (hasStopSignalReceived()) {
    log("⛔ Stop signal detected before admission wait. Exiting without joining.");
    await gracefulLeaveFunction(page, 0, "stop_requested_pre_admission");
    return;
  }

  // Admission + prepare in parallel
  try {
    const [admissionResult] = await Promise.all([
      strategies
        .waitForAdmission(page, botConfig.automaticLeave.waitingRoomTimeout, botConfig)
        .catch((error: any) => {
          const msg: string = error?.message || String(error);
          if (msg.includes("rejected by meeting admin")) {
            return { admitted: false, rejected: true, reason: "admission_rejected_by_admin" } as AdmissionDecision;
          }
          return { admitted: false, rejected: false, reason: "admission_timeout" } as AdmissionDecision;
        }),
      strategies.prepare(page, botConfig),
    ]);

    const isAdmitted = admissionResult === true || (typeof admissionResult === "object" && !!(admissionResult as AdmissionDecision).admitted);
    if (!isAdmitted) {
      const decision: AdmissionDecision = typeof admissionResult === "object"
        ? (admissionResult as AdmissionDecision)
        : { admitted: false, reason: "admission_timeout" };

      if (decision.rejected) {
        await gracefulLeaveFunction(page, 0, decision.reason || "admission_rejected_by_admin");
        return;
      }

      // Attempt stateless leave before graceful exit (browser-based platforms only)
      if (page) {
        try {
          const result = await page.evaluate(async () => {
            if (typeof (window as any).performLeaveAction === "function") {
              return await (window as any).performLeaveAction();
            }
            return false;
          });
          if (result) log("✅ Successfully performed graceful leave during admission timeout");
        } catch {}
      }

      await gracefulLeaveFunction(page, 0, decision.reason || "admission_timeout");
      return;
    }

    // CRITICAL: If bot was immediately admitted, ensure AWAITING_ADMISSION state is processed before ACTIVE
    // The waitForAdmission function sends AWAITING_ADMISSION callback when immediately admitted,
    // but we need to wait a moment for the state machine to process that transition before sending ACTIVE
    log("Bot admitted - ensuring AWAITING_ADMISSION state is processed before sending ACTIVE...");
    await new Promise(resolve => setTimeout(resolve, 1000)); // Wait 1 second for state transition
    
    // Startup callback (sends ACTIVE status)
    try {
      await callStartupCallback(botConfig);
      
      // CRITICAL: Verify bot is still in meeting after callback (prevent false positives)
      // Use silent check to avoid sending AWAITING_ADMISSION callback again
      log("Verifying bot is still in meeting after ACTIVE callback...");
      const stillAdmitted = await strategies.checkAdmissionSilent(page);
      if (!stillAdmitted) {
        log("🚨 Bot is NOT in meeting after ACTIVE callback - false positive detected!");
        await gracefulLeaveFunction(page, 0, "admission_false_positive");
        return;
      }
      log("✅ Bot verified to be in meeting after ACTIVE callback");

      // Re-enable virtual camera after admission. Google Meet may re-negotiate
      // WebRTC tracks during the waiting-room → meeting transition, killing
      // any canvas track that was set up before admission.
      triggerPostAdmissionCamera().catch((err: any) => {
        log(`[VoiceAgent] Post-admission camera error (non-fatal): ${err?.message || err}`);
      });

      // Start chat observer now that the bot is in the meeting.
      // The chat panel can only be opened/read when admitted.
      triggerPostAdmissionChat().catch((err: any) => {
        log(`[Chat] Post-admission chat error (non-fatal): ${err?.message || err}`);
      });
    } catch (error: any) {
      log(`Error during startup callback or verification: ${error?.message || String(error)}`);
      // Continue to recording phase even if callback/verification fails
    }

    // Removal monitoring + recording race
    let signalRemoval: (() => void) | null = null;
    const removalPromise = new Promise<never>((_, reject) => {
      signalRemoval = () => reject(new Error(tokens.removedToken));
    });
    const stopRemoval = strategies.startRemovalMonitor(page, () => { if (signalRemoval) signalRemoval(); });

    try {
      await Promise.race([
        strategies.startRecording(page, botConfig),
        removalPromise
      ]);

      // Normal completion
      await gracefulLeaveFunction(page, 0, "normal_completion");
    } catch (error: any) {
      const msg: string = error?.message || String(error);
      if (msg === tokens.removedToken || msg.includes(tokens.removedToken)) {
        await gracefulLeaveFunction(page, 0, "removed_by_admin");
        return;
      }
      if (msg === tokens.leftAloneToken || msg.includes(tokens.leftAloneToken)) {
        await gracefulLeaveFunction(page, 0, "left_alone_timeout");
        return;
      }
      if (msg === tokens.startupAloneToken || msg.includes(tokens.startupAloneToken)) {
        await gracefulLeaveFunction(page, 0, "startup_alone_timeout");
        return;
      }

      const errorDetails = {
        error_message: error?.message,
        error_stack: error?.stack,
        error_name: error?.name,
        context: "post_join_setup_error",
        platform,
        timestamp: new Date().toISOString()
      };
      await gracefulLeaveFunction(page, 1, "post_join_setup_error", errorDetails);
      return;
    } finally {
      stopRemoval();
    }
  } catch (error: any) {
    const msg: string = error?.message || String(error);
    if (msg.includes(tokens.removedToken)) {
      await gracefulLeaveFunction(page, 0, "removed_by_admin");
      return;
    }
    if (msg.includes(tokens.leftAloneToken)) {
      await gracefulLeaveFunction(page, 0, "left_alone_timeout");
      return;
    }
    if (msg.includes(tokens.startupAloneToken)) {
      await gracefulLeaveFunction(page, 0, "startup_alone_timeout");
      return;
    }

    const errorDetails = {
      error_message: error?.message,
      error_stack: error?.stack,
      error_name: error?.name,
      context: "post_join_setup_error",
      platform,
      timestamp: new Date().toISOString()
    };
    await gracefulLeaveFunction(page, 1, "post_join_setup_error", errorDetails);
  }
}


