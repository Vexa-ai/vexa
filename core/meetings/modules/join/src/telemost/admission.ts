import { Frame, Page } from "playwright";
import { log, callAwaitingAdmissionCallback } from "../_host";
import { BotConfig } from "../_host";
import { checkEscalation, triggerEscalation, getEscalationExtensionMs } from "../shared/escalation";
import { AdmissionError } from "../shared/admission";
import {
  telemostLeaveButtonSelectors,
  telemostConferenceIndicators,
  telemostPrejoinScreenSelectors,
  telemostLobbyTexts,
  telemostRejectionTexts,
  telemostRemovalTexts,
  telemostNotFoundTexts,
  telemostNeedLoginTexts,
  telemostBannedPageIndicators,
  telemostLoginHosts,
  telemostTerminalConsolePattern,
  telemostCallFramePattern,
} from "./selectors";

/** The frame the call renders in — the `/private-join/<id>` iframe inside the messenger shell.
 *  Falls back to the main frame while the iframe is not attached (early load, or a build that
 *  renders the call in the top document). */
export function callFrame(page: Page): Frame {
  return page.frames().find((f) => f !== page.mainFrame() && telemostCallFramePattern.test(f.url()))
    ?? page.mainFrame();
}

/** True once the call iframe is attached. */
export function hasCallFrame(page: Page): boolean {
  return page.frames().some((f) => f !== page.mainFrame() && telemostCallFramePattern.test(f.url()));
}

/** The documents a text verdict may live in: the shell and, when attached, the call frame. */
function textFrames(page: Page): Frame[] {
  const main = page.mainFrame();
  const call = callFrame(page);
  return call === main ? [main] : [main, call];
}

/** Telemost's media engine logs its disconnect code to the page console (KICKED_OUT,
 *  ROOM_HAS_BEEN_CLOSED…). Latched host-side so a kick or a closed room is recognised even
 *  before — or without — any DOM change. Attach BEFORE navigation (from join.ts) so the line is
 *  never missed; the latched code lives on the page object. Idempotent per page. */
export function attachTerminalConsoleWatch(page: Page): void {
  const p = page as any;
  if (p.__vexaTelemostConsoleWatch) return;
  p.__vexaTelemostConsoleWatch = true;
  page.on("console", (msg) => {
    try {
      const m = telemostTerminalConsolePattern.exec(msg.text());
      if (m) p.__vexaTelemostTerminalCode = m[1];
    } catch { /* noop */ }
  });
}

/** The latched media-engine disconnect code, or null. */
export function terminalConsoleCode(page: Page): string | null {
  return (page as any).__vexaTelemostTerminalCode ?? null;
}

/** Case-insensitive scan of the visible text — shell and call frame — for the first matching phrase. */
export async function findBodyText(page: Page, phrases: string[]): Promise<string | null> {
  for (const frame of textFrames(page)) {
    const hit = await frame.evaluate((ps: string[]) => {
      const body = (document.body?.innerText || "").toLowerCase();
      for (const p of ps) if (body.includes(p.toLowerCase())) return p;
      return null;
    }, phrases).catch(() => null);
    if (hit) return hit;
  }
  return null;
}

/** The leave control is call-toolbar-only — never rendered on the pre-join or waiting screens. */
export async function isLeaveVisible(page: Page): Promise<boolean> {
  const frame = callFrame(page);
  for (const sel of telemostLeaveButtonSelectors) {
    if (await frame.locator(sel).first().isVisible({ timeout: 300 }).catch(() => false)) return true;
  }
  return false;
}

async function isPrejoinPresent(page: Page): Promise<boolean> {
  return await callFrame(page).evaluate((sels: string[]) => {
    return sels.some((s) => {
      const el = document.querySelector(s) as HTMLElement | null;
      return !!el && el.offsetParent !== null;
    });
  }, telemostPrejoinScreenSelectors).catch(() => true);
}

/** Check if the bot is on the waiting-room screen. */
export async function isInLobby(page: Page): Promise<boolean> {
  return (await findBodyText(page, telemostLobbyTexts)) !== null;
}

/**
 * Check if the bot is confirmed inside the call.
 *
 * Primary:  the leave control is visible AND neither the pre-join form nor the
 *           waiting-room text is on screen.
 * Fallback: an in-call toolbar control (participants / chat / hand) is visible
 *           under the same exclusions.
 */
export async function isAdmitted(page: Page): Promise<boolean> {
  try {
    if (await isPrejoinPresent(page)) return false;
    if (await isInLobby(page)) return false;
    if (await isLeaveVisible(page)) return true;
    const frame = callFrame(page);
    for (const sel of telemostConferenceIndicators) {
      if (await frame.locator(sel).first().isVisible({ timeout: 300 }).catch(() => false)) return true;
    }
    return false;
  } catch {
    return false;
  }
}

/** The banned page a kicked participant lands on (no return for 24 h). */
export async function isBannedPage(page: Page): Promise<boolean> {
  for (const frame of textFrames(page)) {
    for (const sel of telemostBannedPageIndicators) {
      if (await frame.locator(sel).first().isVisible({ timeout: 300 }).catch(() => false)) return true;
    }
  }
  return false;
}

/** A terminal verdict during the admission wait — a decline, a kick/ban, a closed room, a
 *  meeting that does not exist, or the sign-in wall. Returns what matched, or null. */
async function terminalVerdict(page: Page): Promise<string | null> {
  const code = terminalConsoleCode(page);
  if (code) return `media engine: ${code}`;
  try {
    const host = new URL(page.url()).hostname.toLowerCase();
    if (telemostLoginHosts.includes(host)) return `redirected to Yandex sign-in (${host})`;
  } catch { /* unparsable URL — fall through to the DOM */ }
  if (await isBannedPage(page)) return "banned page";
  return await findBodyText(page, [
    ...telemostRejectionTexts, ...telemostRemovalTexts, ...telemostNotFoundTexts, ...telemostNeedLoginTexts,
  ]);
}

export async function waitForTelemostMeetingAdmission(
  page: Page,
  timeoutMs: number,
  botConfig: BotConfig,
): Promise<boolean> {
  if (!page) throw new Error("[Telemost] Page required for admission check");

  log("[Telemost] Checking admission state...");

  if (await isAdmitted(page)) {
    log("[Telemost] Bot immediately admitted (no waiting room)");
    return true;
  }

  let announcedLobby = false;
  const announceLobby = async () => {
    if (announcedLobby) return;
    announcedLobby = true;
    log("[Telemost] Bot is in the waiting room — waiting for the organiser to admit");
    try {
      await callAwaitingAdmissionCallback(botConfig);
    } catch (e: any) {
      log(`[Telemost] Warning: awaiting_admission callback failed: ${e.message}`);
    }
  };
  if (await isInLobby(page)) await announceLobby();

  const startTime = Date.now();
  const pollInterval = 2000;
  let unknownStateDuration = 0;
  const effectiveTimeout = () => timeoutMs + getEscalationExtensionMs();

  while (Date.now() - startTime < effectiveTimeout()) {
    await page.waitForTimeout(pollInterval);

    const terminal = await terminalVerdict(page);
    if (terminal) {
      log(`[Telemost] Terminal state during admission wait (matched: "${terminal}")`);
      // A decline, a ban, or a closed/missing room is a permanent verdict — a typed denial, not a
      // transient join_failure — so the driver records `awaiting_admission_rejected`, not a retry.
      throw new AdmissionError("denial", `Bot was not admitted to the Telemost meeting (matched: "${terminal}")`);
    }

    if (await isAdmitted(page)) {
      log("[Telemost] Bot admitted — call is live");
      return true;
    }

    const inLobbyNow = await isInLobby(page);
    if (inLobbyNow) {
      await announceLobby();
      unknownStateDuration = 0;
    } else {
      unknownStateDuration += pollInterval;
    }

    const elapsedMs = Date.now() - startTime;
    const escalation = checkEscalation(elapsedMs, timeoutMs, unknownStateDuration);
    if (escalation) {
      await triggerEscalation(botConfig, escalation.reason);
    }

    log(`[Telemost] Still waiting for admission... ${Math.round(elapsedMs / 1000)}s elapsed`);
  }

  // A typed `lobby_timeout` so the driver reports `awaiting_admission_timeout` — an honest
  // terminal reason — instead of a generic, silently-retried `join_failure`.
  throw new AdmissionError("lobby_timeout", `[Telemost] Bot not admitted within ${effectiveTimeout()}ms timeout`);
}

export async function checkForTelemostAdmissionSilent(page: Page): Promise<boolean> {
  if (!page) return false;
  // Retry with a short delay — the toolbar briefly unmounts during layout transitions.
  for (let attempt = 0; attempt < 3; attempt++) {
    if (await isAdmitted(page)) return true;
    if (attempt < 2) await page.waitForTimeout(1000);
  }
  return false;
}
