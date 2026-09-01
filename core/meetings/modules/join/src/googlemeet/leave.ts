import { Page } from "playwright";
import { log, callLeaveCallback } from "../_host";
import { logJSON } from "../_host";
import { BotConfig } from "../_host";
import { googleLeaveButtonMatchers } from "./selectors";
import { stopGoogleRecording } from "../_host";

// The canonical in-page leave click is shared across platforms
// (../shared/leave-click). Re-exported under the Google-scoped name this
// module's consumers and fixture test already use; page.evaluate serializes
// this exact function, so the no-browser fixture drives what production ships.
import { leaveBrowserClick as googleLeaveBrowserClick } from "../shared/leave-click";
export { googleLeaveBrowserClick };

// Guards a one-time DOM dump per notice (tuning aid) so a lingering match doesn't spam the log.
const _gmeetNoticeDumped = new Set<string>();

/**
 * Dismiss BENIGN Google Meet notices that linger over the call — e.g. the "Microphone muted by system"
 * snackbar (heading + advisory text, closed by an X icon, NOT a text button). They don't block the bot
 * but stay on screen and land in the server-side video recording. NOT consent gates (the Gemini "take
 * notes" prompt is escalated, never auto-clicked).
 *
 * Anchored on the notice's distinctive TEXT (Meet's class names are randomized), then clicks its close
 * control inside the tightest card that carries both the text and a button. Safe to poll: instant checks
 * (timeout:0), no-ops when absent. Add a row to `dismissTargets` for a new notice.
 */
export async function dismissGoogleMeetPopups(page: Page): Promise<void> {
  // Benign "your hardware isn't set up" snackbars Meet queues for a bot with no real mic/camera —
  // each a card with a heading + advisory, closed by an X icon (verified live: clicking the X
  // dismisses it, and Meet then surfaces the next one). NOT consent/removal — those are handled elsewhere.
  const dismissTargets = [
    { text: /microphone muted by system|muted by (your )?system/i, label: "mic muted by system" },
    { text: /camera not found|make sure your camera/i, label: "camera not found" },
  ];

  for (const { text, label } of dismissTargets) {
    try {
      // The card = the tightest element carrying the notice text AND its own button (the X). Ancestors
      // also contain the text, so .last() picks the innermost — the snackbar itself, not the page.
      const card = page.locator("div").filter({ hasText: text }).filter({ has: page.locator("button") }).last();
      if (!(await card.isVisible({ timeout: 0 }).catch(() => false))) continue;

      if (!_gmeetNoticeDumped.has(label)) {                     // one-time structure dump for tuning
        _gmeetNoticeDumped.add(label);
        const html = await card.evaluate((el) => (el as HTMLElement).outerHTML.slice(0, 1200)).catch(() => "");
        log(`[Google Meet] notice "${label}" DOM: ${html}`);
      }

      // Prefer a close/dismiss-labelled control; fall back to the card's sole button (the X icon).
      const labelled = card.getByRole("button", { name: /close|dismiss|got it|ok/i });
      const btn = (await labelled.count().catch(() => 0)) ? labelled.first() : card.getByRole("button").last();
      await btn.click({ timeout: 0 });
      log(`[Google Meet] Dismissed "${label}" popup`);
    } catch { /* not present, or the click missed — retried next poll */ }
  }
}

// Prepare for recording by exposing necessary functions
export async function prepareForRecording(page: Page, botConfig: BotConfig): Promise<void> {
  // Expose the logBot function to the browser context
  await page.exposeFunction("logBot", (msg: string) => {
    log(msg);
  });

  // Expose bot config for callback functions
  await page.exposeFunction("getBotConfig", (): BotConfig => botConfig);

  // Node-side binding backing the browser-context leave hook: it drives the
  // same canonical googleLeaveBrowserClick through page.evaluate, so the hook
  // needs no in-page copy of the click logic. The binding survives
  // navigations; the hook below is re-armed per document like before.
  await page.exposeFunction("__vexaGoogleLeaveClick", async (): Promise<boolean> => {
    try {
      return Boolean(await page.evaluate(googleLeaveBrowserClick, googleLeaveButtonMatchers));
    } catch (err: any) {
      log(`[performLeaveAction] browser leave click failed: ${err?.message}`);
      return false;
    }
  });

  // Ensure leave function is available even before admission. The leave
  // callback to meeting-api is sent from the Node side (leaveGoogleMeet), not
  // from this hook.
  await page.evaluate(() => {
    if (typeof (window as any).performLeaveAction !== "function") {
      (window as any).performLeaveAction = async () => {
        try {
          (window as any).logBot?.("🔥 Leave requested from browser context — clicking the leave path...");
          return await (window as any).__vexaGoogleLeaveClick();
        } catch (err: any) {
          (window as any).logBot?.(`Error during Google Meet leave attempt: ${err?.message}`);
          return false;
        }
      };
    }
  });
}

// --- ADDED: Exported function to trigger leave from Node.js ---
export async function leaveGoogleMeet(page: Page | null, botConfig?: BotConfig, reason: string = "manual_leave"): Promise<boolean> {
  log("[leaveGoogleMeet] Triggering leave action in browser context...");
  if (!page || page.isClosed()) {
    log("[leaveGoogleMeet] Page is not available or closed.");
    return false;
  }

  // Pack U.2 (v0.10.6): drain the unified recording pipeline before UI leave.
  // This stops the browser-side MediaRecorder, emits the final isFinal=true
  // chunk, and waits for the upload queue to drain so meeting-api flips
  // Recording.status to COMPLETED before the bot exits. Replaces the old
  // __vexaFlushRecordingBlob full-blob path (dead under chunked upload).
  try {
    log("[leaveGoogleMeet] Stopping recording pipeline before leave...");
    await stopGoogleRecording();
  } catch (flushError: any) {
    // v0.10.5 Pack G.1 — recording-flush failure means the final chunk
    // never made it; chunks already in MinIO are still durable, but the
    // recording_finalizer won't see is_final=true and the meeting Recording
    // row will stay IN_PROGRESS until reconciler cleanup.
    logJSON({
      level: "error",
      msg: "[leaveGoogleMeet] Recording pipeline stop failed",
      error_message: flushError?.message,
      error_name: flushError?.name,
      error_stack: flushError?.stack,
      leave_reason: reason,
    });
  }

  // Call leave callback first to notify meeting-api
  if (botConfig) {
    try {
      log("[leaveGoogleMeet] Calling leave callback before attempting to leave");
      await callLeaveCallback(botConfig, reason);
      log("[leaveGoogleMeet] Leave callback sent successfully");
    } catch (callbackError: any) {
      logJSON({
        level: "warn",
        msg: "[leaveGoogleMeet] Leave callback failed; continuing with leave attempt",
        error_message: callbackError?.message,
        error_name: callbackError?.name,
        leave_reason: reason,
      });
    }
  } else {
    logJSON({
      level: "warn",
      msg: "[leaveGoogleMeet] No bot config provided; cannot send leave callback",
    });
  }

  try {
    // Ship the canonical leave click into the page directly (self-contained:
    // never depends on the separately-injected window.performLeaveAction).
    const result = await page.evaluate(googleLeaveBrowserClick, googleLeaveButtonMatchers);
    logJSON({
      level: "info",
      msg: "[leaveGoogleMeet] Browser leave action complete",
      leave_result: Boolean(result),
      leave_reason: reason,
    });
    // Contract: this function is typed Promise<boolean>. page.evaluate can return
    // undefined (e.g. a black/captcha page where the click routine never resolves a
    // value), which otherwise propagates as `result: undefined` to callers that treat
    // it as a tri-state. Coerce to match the declared boolean (and the log above).
    return Boolean(result);
  } catch (error: any) {
    logJSON({
      level: "error",
      msg: "[leaveGoogleMeet] Error calling the browser leave click",
      error_message: error?.message,
      error_name: error?.name,
      leave_reason: reason,
    });
    return false;
  }
}
