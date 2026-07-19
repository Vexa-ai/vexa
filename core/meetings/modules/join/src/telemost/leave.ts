import type { Page } from "playwright";
import {
  callLeaveCallback,
  log,
  stopTelemostRecording,
  type BotConfig,
} from "../_host";
import { telemostInMeetingIndicators } from "./selectors";

export async function leaveTelemostMeeting(
  page: Page | null,
  botConfig?: BotConfig,
  reason: string = "manual_leave",
): Promise<boolean> {
  if (botConfig) await callLeaveCallback(botConfig, reason).catch(() => {});
  if (!page || page.isClosed()) {
    await stopTelemostRecording(page ?? undefined, botConfig).catch(() => {});
    return true;
  }

  let clicked = false;
  for (const selector of telemostInMeetingIndicators) {
    const button = page.locator(selector).first();
    if (!await button.isVisible({ timeout: 200 }).catch(() => false)) continue;
    await button.click({ timeout: 3000 }).catch(() => {});
    clicked = true;
    break;
  }
  if (!clicked) {
    log("[Telemost] Leave control missed — navigating away to tear down WebRTC");
    await page.goto("about:blank").catch(() => {});
  } else {
    await page.waitForTimeout(1000);
  }
  await stopTelemostRecording(page, botConfig).catch(() => {});
  return true;
}
