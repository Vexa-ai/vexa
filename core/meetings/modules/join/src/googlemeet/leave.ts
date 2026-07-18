import { Page } from "playwright";
import { log, callLeaveCallback } from "../_host";
import { logJSON } from "../_host";
import { BotConfig } from "../_host";
import { googleLeaveButtonMatchers, BrowserContextButtonMatcher } from "./selectors";
import { stopGoogleRecording } from "../_host";

// The leave click that runs INSIDE the page (shipped through page.evaluate by
// both consumers below — ONE canonical routine, so the injected hook and the
// direct leave can never drift, and the no-browser fixture test drives exactly
// the function production serializes into the browser).
//
// Self-contained by contract: it is serialized into the browser context, where
// module scope does not exist — DOM globals and its argument only.
// document.querySelector understands plain CSS (no Playwright engines), so
// text-labelled buttons are expressed as `text` fields and matched here by
// whitespace-normalized, case-insensitive substring of textContent — the
// semantics `:has-text()` applies in Playwright contexts. Matchers are tried
// in order; the first visible match is clicked.
export async function googleLeaveBrowserClick(
  matchers: BrowserContextButtonMatcher[],
): Promise<boolean> {
  // Serialization contract: esbuild-family compilers (tsx — the debug harness
  // lane) emit nested function expressions wrapped in a `__name` helper that
  // does not exist inside the page. Define an identity fallback BEFORE any
  // nested function is created, so the serialized source is self-contained
  // under every compiler (tsc emits none of this; the line is then inert).
  (globalThis as any).__name = (globalThis as any).__name || ((f: unknown) => f);
  const blog = (m: string) => { try { (window as any).logBot?.(m); } catch { /* logging is best-effort */ } };
  const normalize = (s: string) => s.replace(/\s+/g, " ").trim().toLowerCase();
  const isVisible = (el: Element) => {
    const rect = el.getBoundingClientRect();
    const cs = getComputedStyle(el as HTMLElement);
    return rect.width > 0 && rect.height > 0
      && cs.display !== "none" && cs.visibility !== "hidden" && cs.opacity !== "0";
  };
  for (const matcher of matchers) {
    const scope = matcher.css ?? 'button, [role="button"]';
    let candidates: Element[];
    try {
      candidates = Array.from(document.querySelectorAll(scope));
    } catch (e: any) {
      // The selector-validity gate CSS-parses every declared browser-context
      // entry, so this only fires on drift — loudly, never silently.
      blog(`[leave] selector failed in browser context: ${scope} — ${e?.message}`);
      continue;
    }
    const needle = matcher.text === undefined ? null : normalize(matcher.text);
    const button = candidates.find(
      (el) => (needle === null || normalize(el.textContent || "").includes(needle)) && isVisible(el),
    ) as HTMLElement | undefined;
    if (!button) continue;
    button.scrollIntoView({ behavior: "smooth", block: "center" });
    await new Promise((r) => setTimeout(r, 300));
    button.click();
    await new Promise((r) => setTimeout(r, 800));
    const via = [matcher.css, matcher.text === undefined ? undefined : `text~"${matcher.text}"`]
      .filter(Boolean).join(" ");
    blog(`[leave] clicked leave button via ${via}`);
    return true;
  }
  blog("[leave] no visible leave button matched any matcher");
  return false;
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
