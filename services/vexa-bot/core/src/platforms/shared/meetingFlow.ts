import { Page } from "playwright";
import { BotConfig } from "../../types";
import { log, callStartupCallback } from "../../utils";
import { hasStopSignalReceived } from "../../index";

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
  join: (page: Page, botConfig: BotConfig) => Promise<void>;
  waitForAdmission: (page: Page, timeoutMs: number, botConfig: BotConfig) => Promise<AdmissionResult>;
  prepare: (page: Page, botConfig: BotConfig) => Promise<void>;
  startRecording: (page: Page, botConfig: BotConfig) => Promise<void>;
  startRemovalMonitor: (page: Page, onRemoval?: () => void | Promise<void>) => () => void;
  leave: (page: Page | null, botConfig?: BotConfig, reason?: LeaveReason) => Promise<boolean>;
};

export async function runMeetingFlow(
  platform: string,
  botConfig: BotConfig,
  page: Page,
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

      // Attempt stateless leave before graceful exit
      try {
        const result = await page.evaluate(async () => {
          if (typeof (window as any).performLeaveAction === "function") {
            return await (window as any).performLeaveAction();
          }
          return false;
        });
        if (result) log("✅ Successfully performed graceful leave during admission timeout");
      } catch {}

      await gracefulLeaveFunction(page, 0, decision.reason || "admission_timeout");
      return;
    }

    // Startup callback
    try {
      await callStartupCallback(botConfig);
    } catch {}

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


