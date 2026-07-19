import type { Page } from "playwright";
import { callAwaitingAdmissionCallback, log, type BotConfig } from "../_host";
import { AdmissionError } from "../shared/admission";
import {
  telemostInMeetingIndicators,
  telemostPrejoinIndicators,
  telemostRejectionTexts,
  telemostRemovalTexts,
  telemostWaitingTexts,
} from "./selectors";

async function bodyMatch(page: Page, texts: string[]): Promise<string | null> {
  return await page.evaluate((phrases: string[]) => {
    const body = (document.body?.innerText || "").toLowerCase();
    return phrases.find((text) => body.includes(text.toLowerCase())) || null;
  }, texts).catch(() => null);
}

export async function isTelemostAdmitted(page: Page): Promise<boolean> {
  for (const selector of telemostPrejoinIndicators) {
    if (await page.locator(selector).first().isVisible({ timeout: 150 }).catch(() => false)) return false;
  }
  for (const selector of telemostInMeetingIndicators) {
    if (await page.locator(selector).first().isVisible({ timeout: 200 }).catch(() => false)) return true;
  }
  return false;
}

export async function waitForTelemostMeetingAdmission(
  page: Page,
  timeoutMs: number,
  botConfig: BotConfig,
): Promise<boolean> {
  if (!page) throw new Error("[Telemost] Page required for admission check");
  const started = Date.now();
  let awaitingReported = false;
  let sawWaitingRoom = false;

  while (Date.now() - started < timeoutMs) {
    if (await isTelemostAdmitted(page)) {
      log("[Telemost] Bot admitted");
      return true;
    }
    const terminal = await bodyMatch(page, [...telemostRejectionTexts, ...telemostRemovalTexts]);
    if (terminal) {
      throw new AdmissionError("denial", `[Telemost] Admission rejected or meeting ended (matched: "${terminal}")`);
    }
    const waiting = await bodyMatch(page, telemostWaitingTexts);
    if (waiting) {
      sawWaitingRoom = true;
      if (!awaitingReported) {
        awaitingReported = true;
        await callAwaitingAdmissionCallback(botConfig).catch(() => {});
        log(`[Telemost] Waiting for host approval (matched: "${waiting}")`);
      }
    }
    await page.waitForTimeout(1500);
  }

  const outcome = sawWaitingRoom ? "lobby_timeout" : "join_failure";
  throw new AdmissionError(outcome, `[Telemost] Not admitted within ${timeoutMs}ms`);
}

export async function checkForTelemostAdmissionSilent(page: Page): Promise<boolean> {
  if (!page) return false;
  return isTelemostAdmitted(page);
}
