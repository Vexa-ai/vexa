import { callStatusChangeCallback } from "./services/unified-callback";
import { logJSON } from "./utils/log";

/**
 * v0.10.5 Pack G.1 (#272 issue 6) — `log()` is now a thin shim that
 * routes existing call sites through the structured-JSON logger in
 * ./utils/log.ts. Every log line emitted by the bot is a single-line
 * JSON object so K8s `kubectl logs` capture (Pack G.2) is parseable
 * without ad-hoc text munging.
 *
 * Existing callers passing prefix-tagged messages like
 * `log("[Graceful Leave] Initiating shutdown")` get the prefix
 * extracted into a structured `subsystem: "Graceful Leave"` field
 * automatically — no call-site rewrite required.
 *
 * For new code that wants to attach arbitrary structured fields,
 * import logJSON directly:
 *
 *   import { logJSON } from "./utils/log";
 *   logJSON({ level: "warn", msg: "[Recording] upload failed",
 *            chunk_seq, attempt, error_message });
 */
export function log(message: string): void {
  logJSON({ msg: message });
}

export function randomDelay(amount: number) {
  return (2 * Math.random() - 1) * (amount / 10) + amount;
}

export async function callStartupCallback(botConfig: any): Promise<void> {
  await callStatusChangeCallback(botConfig, "active");
}

export async function callJoiningCallback(botConfig: any): Promise<void> {await callStatusChangeCallback(botConfig, "joining");}

export async function callAwaitingAdmissionCallback(botConfig: any): Promise<void> {
  await callStatusChangeCallback(botConfig, "awaiting_admission");
}

export async function callNeedsHumanHelpCallback(
  botConfig: any,
  reason: string,
  screenshotPath?: string
): Promise<void> {
  await callStatusChangeCallback(botConfig, "needs_human_help", reason);
}

export async function callLeaveCallback(botConfig: any, reason: string = "manual_leave"): Promise<void> {
  // Note: Leave callback is typically handled by the exit callback with completion status
  // This function is kept for backward compatibility but may not be used
  log(`Leave callback requested with reason: ${reason} - handled by exit callback`);
}

