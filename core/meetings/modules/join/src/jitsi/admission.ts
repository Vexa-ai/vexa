import { Page } from "playwright";
import { log, callAwaitingAdmissionCallback } from "../_host";
import { BotConfig } from "../_host";
import { checkEscalation, triggerEscalation, getEscalationExtensionMs } from "../shared/escalation";
import {
  jitsiHangupButtonSelectors,
  jitsiConferenceIndicators,
  jitsiPrejoinScreenSelectors,
  jitsiLobbyIndicators,
  jitsiLobbyTexts,
  jitsiRejectionTexts,
  jitsiRemovalTexts,
} from "./selectors";

/**
 * Check if the bot is confirmed inside the conference.
 *
 * Primary:   the app's own runtime verdict — `APP.conference.isJoined()`.
 *            jitsi-meet exposes the APP global on every stock deployment; it is
 *            the same oracle the app's UI renders from, so it cannot false-
 *            positive on lobby/prejoin (isJoined() is false while knocking).
 * Fallback1: a hangup control is visible — footer-only, never rendered on the
 *            prejoin or lobby screens.
 * Fallback2: conference stage present (#largeVideoContainer) AND no prejoin or
 *            lobby indicators — for custom builds that strip both the APP
 *            global and the stock hangup classes. The stage alone is NOT
 *            sufficient (some builds mount it behind the lobby screen), hence
 *            the exclusions.
 */
async function isAdmitted(page: Page): Promise<boolean> {
  try {
    const viaApp = await page.evaluate(() => {
      try {
        const app = (globalThis as any).APP;
        if (app?.conference?.isJoined) return app.conference.isJoined() === true ? "joined" : "not-joined";
        return "no-api";
      } catch { return "no-api"; }
    }).catch(() => "no-api");
    if (viaApp === "joined") return true;
    if (viaApp === "not-joined") return false; // authoritative negative — skip DOM guesswork

    // APP global absent (custom build) — DOM fallbacks.
    for (const sel of jitsiHangupButtonSelectors) {
      if (await page.locator(sel).first().isVisible({ timeout: 300 }).catch(() => false)) return true;
    }

    const inLobby = await isInLobby(page);
    if (inLobby) return false;

    const prejoinPresent = await page.evaluate((sels: string[]) => {
      return sels.some((s) => !!document.querySelector(s));
    }, jitsiPrejoinScreenSelectors).catch(() => true);
    if (prejoinPresent) return false;

    for (const sel of jitsiConferenceIndicators) {
      if (await page.locator(sel).first().isVisible({ timeout: 300 }).catch(() => false)) return true;
    }
    return false;
  } catch {
    return false;
  }
}

/** Check if the bot is on the lobby (knocking) screen or a waiting-for-host dialog. */
async function isInLobby(page: Page): Promise<boolean> {
  try {
    for (const sel of jitsiLobbyIndicators) {
      const visible = await page.locator(sel).first().isVisible({ timeout: 200 }).catch(() => false);
      if (visible) return true;
    }
    return await page.evaluate((texts: string[]) => {
      const bodyText = document.body?.innerText || "";
      return texts.some((t) => bodyText.toLowerCase().includes(t.toLowerCase()));
    }, jitsiLobbyTexts).catch(() => false);
  } catch {
    return false;
  }
}

/** Detect a lobby decline, a kick, or a terminated conference (terminal states). */
async function isRejectedOrEnded(page: Page): Promise<string | null> {
  try {
    return await page.evaluate((texts: string[]) => {
      const bodyText = (document.body?.innerText || "").toLowerCase();
      for (const t of texts) if (bodyText.includes(t.toLowerCase())) return t;
      return null;
    }, [...jitsiRejectionTexts, ...jitsiRemovalTexts]).catch(() => null);
  } catch {
    return null;
  }
}

export async function waitForJitsiMeetingAdmission(
  page: Page,
  timeoutMs: number,
  botConfig: BotConfig,
): Promise<boolean> {
  if (!page) throw new Error("[Jitsi] Page required for admission check");

  log("[Jitsi] Checking admission state...");

  // Fast path: rooms without a lobby admit immediately.
  if (await isAdmitted(page)) {
    log("[Jitsi] Bot immediately admitted (no lobby)");
    return true;
  }

  const inLobby = await isInLobby(page);
  if (inLobby) {
    log("[Jitsi] Bot is in the lobby — waiting for a moderator to admit");
    try {
      await callAwaitingAdmissionCallback(botConfig);
    } catch (e: any) {
      log(`[Jitsi] Warning: awaiting_admission callback failed: ${e.message}`);
    }
  }

  // Poll loop
  const startTime = Date.now();
  const pollInterval = 2000;
  let unknownStateDuration = 0;
  const effectiveTimeout = () => timeoutMs + getEscalationExtensionMs();

  while (Date.now() - startTime < effectiveTimeout()) {
    await page.waitForTimeout(pollInterval);

    const terminal = await isRejectedOrEnded(page);
    if (terminal) {
      log(`[Jitsi] Terminal state during admission wait (matched: "${terminal}")`);
      throw new Error(`Bot was rejected from the Jitsi meeting or meeting ended (matched: "${terminal}")`);
    }

    if (await isAdmitted(page)) {
      log("[Jitsi] Bot admitted — conference is live");
      return true;
    }

    // Track unknown state (neither admitted, nor lobby, nor terminal)
    const inLobbyNow = await isInLobby(page);
    if (!inLobbyNow) {
      unknownStateDuration += pollInterval;
    } else {
      unknownStateDuration = 0;
    }

    // Escalation check (VNC/human-help surfacing — same policy as the other platforms)
    const elapsedMs = Date.now() - startTime;
    const escalation = checkEscalation(elapsedMs, timeoutMs, unknownStateDuration);
    if (escalation) {
      await triggerEscalation(botConfig, escalation.reason);
    }

    const elapsed = Math.round(elapsedMs / 1000);
    log(`[Jitsi] Still waiting for admission... ${elapsed}s elapsed`);
  }

  throw new Error(`[Jitsi] Bot not admitted within ${effectiveTimeout()}ms timeout`);
}

export async function checkForJitsiAdmissionSilent(page: Page): Promise<boolean> {
  if (!page) return false;
  // Retry with a short delay — the jitsi UI briefly unmounts controls during
  // layout transitions (filmstrip resize, notification stacking) after admission.
  for (let attempt = 0; attempt < 3; attempt++) {
    if (await isAdmitted(page)) return true;
    if (attempt < 2) {
      await page.waitForTimeout(1000);
    }
  }
  return false;
}
