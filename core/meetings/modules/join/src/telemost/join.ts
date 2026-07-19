import type { Page } from "playwright";
import { callJoiningCallback, log, type BotConfig } from "../_host";
import { isTelemostAdmitted } from "./admission";
import {
  telemostBrowserContinueSelectors,
  telemostBrowserContinueTexts,
  telemostJoinButtonSelectors,
  telemostJoinTexts,
  telemostMuteActionSelectors,
  telemostNameInputSelectors,
} from "./selectors";

const TELEMOST_PATH = /^\/j\/\d{10}\/?$/;

export function buildTelemostMeetingUrl(meetingUrl: string): string {
  let url: URL;
  try {
    url = new URL(meetingUrl);
  } catch (error: any) {
    throw new Error(`Invalid Telemost meeting URL: ${meetingUrl} — ${error.message}`);
  }
  if (url.protocol !== "https:" || url.hostname !== "telemost.yandex.ru" || !TELEMOST_PATH.test(url.pathname)) {
    throw new Error(`Invalid Telemost meeting URL (expected https://telemost.yandex.ru/j/<10-digit-id>): ${meetingUrl}`);
  }
  return url.toString();
}

async function clickByText(page: Page, texts: string[]): Promise<string | null> {
  return await page.evaluate((phrases: string[]) => {
    const candidates = Array.from(document.querySelectorAll("button, a, [role=button]")) as HTMLElement[];
    for (const element of candidates) {
      const value = (element.innerText || element.textContent || "").trim().toLowerCase();
      const phrase = phrases.find((text) => value === text.toLowerCase() || value.includes(text.toLowerCase()));
      if (phrase) {
        element.click();
        return phrase;
      }
    }
    return null;
  }, texts).catch(() => null);
}

async function continueInBrowser(page: Page): Promise<void> {
  if (await clickByText(page, telemostBrowserContinueTexts)) {
    await page.waitForTimeout(1000);
    return;
  }
  for (const selector of telemostBrowserContinueSelectors) {
    const target = page.locator(selector).first();
    if (await target.isVisible({ timeout: 250 }).catch(() => false)) {
      await target.click({ timeout: 3000 }).catch(() => {});
      await page.waitForTimeout(1000);
      return;
    }
  }
}

async function fillGuestName(page: Page, botName: string): Promise<void> {
  for (const selector of telemostNameInputSelectors) {
    const field = page.locator(selector).first();
    if (!await field.isVisible({ timeout: 400 }).catch(() => false)) continue;
    await field.click({ timeout: 3000 }).catch(() => {});
    await field.fill("");
    await page.keyboard.type(botName, { delay: 30 });
    log(`[Telemost] Guest name entered: "${botName}"`);
    return;
  }
}

async function mutePrejoinDevices(page: Page): Promise<void> {
  for (const selector of telemostMuteActionSelectors) {
    const control = page.locator(selector).first();
    if (await control.isVisible({ timeout: 250 }).catch(() => false)) {
      await control.click({ timeout: 3000 }).catch(() => {});
    }
  }
}

async function clickJoin(page: Page): Promise<boolean> {
  for (const selector of telemostJoinButtonSelectors) {
    const button = page.locator(selector).first();
    if (await button.isVisible({ timeout: 300 }).catch(() => false)) {
      await button.click({ timeout: 5000 }).catch(() => {});
      return true;
    }
  }
  return Boolean(await clickByText(page, telemostJoinTexts));
}

export async function joinTelemostMeeting(
  page: Page,
  meetingUrl: string,
  botName: string,
  botConfig: BotConfig,
): Promise<void> {
  if (!page) throw new Error("[Telemost] Page is required");
  const url = buildTelemostMeetingUrl(meetingUrl);
  log(`[Telemost] Navigating to: ${url}`);
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60_000 });
  await callJoiningCallback(botConfig);
  await page.waitForTimeout(1500);

  if (await isTelemostAdmitted(page)) return;
  await continueInBrowser(page);
  await fillGuestName(page, botName);
  await mutePrejoinDevices(page);

  if (!await clickJoin(page)) {
    throw new Error("[Telemost] Join/Continue control was not found");
  }
  log("[Telemost] Join requested");
}
