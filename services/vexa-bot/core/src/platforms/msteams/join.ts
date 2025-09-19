import { Page } from "playwright";
import { log, callJoiningCallback } from "../../utils";
import { BotConfig } from "../../types";
import {
  teamsContinueButtonSelectors,
  teamsJoinButtonSelectors,
  teamsCameraButtonSelectors,
  teamsNameInputSelectors
} from "./selectors";

export async function joinMicrosoftTeams(page: Page, botConfig: BotConfig): Promise<void> {
  // Step 1: Navigate to Teams meeting
  log(`Step 1: Navigating to Teams meeting: ${botConfig.meetingUrl}`);
  await page.goto(botConfig.meetingUrl!, { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForTimeout(500);
  
  try {
    await callJoiningCallback(botConfig);
    log("Joining callback sent successfully");
  } catch (callbackError: any) {
    log(`Warning: Failed to send joining callback: ${callbackError.message}. Continuing with join process...`);
  }

  log("Step 2: Looking for continue button...");
  try {
    const continueButton = page.locator(teamsContinueButtonSelectors[0]).first();
    await continueButton.waitFor({ timeout: 10000 });
    await continueButton.click();
    log("✅ Clicked continue button");
    await page.waitForTimeout(500);
  } catch (error) {
    log("ℹ️ Continue button not found, continuing...");
  }

  log("Step 3: Looking for join button...");
  try {
    const joinButton = page.locator(teamsJoinButtonSelectors[0]).first();
    await joinButton.waitFor({ timeout: 10000 });
    await joinButton.click();
    log("✅ Clicked join button");
    await page.waitForTimeout(500);
  } catch (error) {
    log("ℹ️ Join button not found, continuing...");
  }

  log("Step 4: Trying to turn off camera...");
  try {
    const cameraButton = page.locator(teamsCameraButtonSelectors[0]);
    await cameraButton.waitFor({ timeout: 5000 });
    await cameraButton.click();
    log("✅ Camera turned off");
  } catch (error) {
    log("ℹ️ Camera button not found or already off");
  }

  log("Step 5: Trying to set display name...");
  try {
    const nameInput = page.locator(teamsNameInputSelectors.join(', ')).first();
    await nameInput.waitFor({ timeout: 5000 });
    await nameInput.fill(botConfig.botName);
    log(`✅ Display name set to "${botConfig.botName}"`);
  } catch (error) {
    log("ℹ️ Display name input not found, continuing...");
  }

  log("Step 6: Looking for final join button...");
  try {
    const finalJoinButton = page.locator(teamsJoinButtonSelectors.join(', ')).first();
    await finalJoinButton.waitFor({ timeout: 10000 });
    await finalJoinButton.click();
    log("✅ Clicked final join button");
    await page.waitForTimeout(1000);
  } catch (error) {
    log("ℹ️ Final join button not found");
  }

  log("Step 7: Checking current state...");
}
