import { Page } from "playwright";
import { log, randomDelay } from "../utils";
import { BotConfig } from "../types";

export async function handleMicrosoftTeams(
  botConfig: BotConfig,
  page: Page,
  gracefulLeaveFunction: (page: Page | null, exitCode: number, reason: string, errorDetails?: any) => Promise<void>
): Promise<void> {
  log("Starting Microsoft Teams bot - Using simple approach with MS Edge");
  
  if (!botConfig.meetingUrl) {
    log("Error: Meeting URL is required for Microsoft Teams but is null.");
    await gracefulLeaveFunction(page, 1, "missing_meeting_url");
    return;
  }

  try {
    // Step 1: Navigate to Teams meeting (exactly like simple-bot.js)
    log(`Step 1: Navigating to Teams meeting: ${botConfig.meetingUrl}`);
    await page.goto(botConfig.meetingUrl, { waitUntil: 'networkidle', timeout: 30000 });
    await page.waitForTimeout(5000);
    
    // Take initial screenshot
    await page.screenshot({ path: '/app/screenshots/teams-step-1-initial.png', fullPage: true });
    log("📸 Screenshot: Step 1 - Initial page load");

    // Step 2: Try to find and click "Continue on this browser" button (exactly like simple-bot.js)
    log("Step 2: Looking for 'Continue on this browser' button...");
    try {
      const continueButton = page.getByRole('button', { name: 'Continue on this browser' });
      await continueButton.waitFor({ timeout: 10000 });
      await continueButton.click();
      log("✅ Clicked 'Continue on this browser'");
      await page.waitForTimeout(3000);
    } catch (error) {
      log("ℹ️ 'Continue on this browser' button not found, trying alternative selectors...");
      
      // Try alternative selectors for continue button
      try {
        const altContinueButton = page.locator('button:has-text("Continue")').first();
        await altContinueButton.waitFor({ timeout: 5000 });
        await altContinueButton.click();
        log("✅ Clicked alternative continue button");
        await page.waitForTimeout(3000);
      } catch (altError) {
        log("ℹ️ Alternative continue button not found, continuing...");
      }
    }

    await page.screenshot({ path: '/app/screenshots/teams-step-2-after-continue.png', fullPage: true });
    log("📸 Screenshot: Step 2 - After continue click");

    // Step 3: Try to find and click "Join now" button (exactly like simple-bot.js)
    log("Step 3: Looking for 'Join now' button...");
    try {
      const joinNowButton = page.getByRole('button', { name: 'Join now' });
      await joinNowButton.waitFor({ timeout: 10000 });
      await joinNowButton.click();
      log("✅ Clicked 'Join now'");
      await page.waitForTimeout(3000);
    } catch (error) {
      log("ℹ️ 'Join now' button not found, trying alternative selectors...");
      
      // Try alternative selectors for join button
      try {
        const altJoinButton = page.locator('button:has-text("Join")').first();
        await altJoinButton.waitFor({ timeout: 5000 });
        await altJoinButton.click();
        log("✅ Clicked alternative join button");
        await page.waitForTimeout(3000);
      } catch (altError) {
        log("ℹ️ Alternative join button not found, continuing...");
      }
    }

    // Step 4: Try to turn off camera (exactly like simple-bot.js)
    log("Step 4: Trying to turn off camera...");
    try {
      const cameraButton = page.getByRole('button', { name: 'Turn off camera' });
      await cameraButton.waitFor({ timeout: 5000 });
      await cameraButton.click();
      log("✅ Camera turned off");
    } catch (error) {
      log("ℹ️ Camera button not found or already off");
    }

    // Step 5: Try to set display name (exactly like simple-bot.js)
    log("Step 5: Trying to set display name...");
    try {
      const nameInput = page.getByRole('textbox', { name: 'Display name' });
      await nameInput.waitFor({ timeout: 5000 });
      await nameInput.fill(botConfig.botName);
      log(`✅ Display name set to "${botConfig.botName}"`);
    } catch (error) {
      log("ℹ️ Display name input not found, trying alternative selectors...");
      
      // Try alternative selectors for name input
      try {
        const altNameInput = page.locator('input[placeholder*="name"], input[placeholder*="Name"], input[type="text"]').first();
        await altNameInput.waitFor({ timeout: 5000 });
        await altNameInput.fill(botConfig.botName);
        log(`✅ Display name set to "${botConfig.botName}" using alternative selector`);
      } catch (altError) {
        log("ℹ️ Alternative name input not found, trying text-based selector...");
        
        // Try text-based selector
        try {
          const textNameInput = page.locator('input:has-text("Type your name")').first();
          await textNameInput.waitFor({ timeout: 3000 });
          await textNameInput.fill(botConfig.botName);
          log(`✅ Display name set to "${botConfig.botName}" using text selector`);
        } catch (textError) {
          log("ℹ️ Text-based name input not found, trying CSS selector...");
          
          // Try CSS selector for the name input
          try {
            const cssNameInput = page.locator('input[type="text"]').first();
            await cssNameInput.waitFor({ timeout: 3000 });
            await cssNameInput.fill(botConfig.botName);
            log(`✅ Display name set to "${botConfig.botName}" using CSS selector`);
          } catch (cssError) {
            log("ℹ️ CSS selector name input not found");
          }
        }
      }
    }

    // Step 6: Try to find final join button (exactly like simple-bot.js)
    log("Step 6: Looking for final join button...");
    try {
      const finalJoinButton = page.getByRole('button', { name: 'Join' });
      await finalJoinButton.waitFor({ timeout: 10000 });
      await finalJoinButton.click();
      log("✅ Clicked final join button");
      await page.waitForTimeout(5000);
    } catch (error) {
      log("ℹ️ Final join button not found, trying alternative selectors...");
      
      // Try alternative selectors for join button
      try {
        const altJoinButton = page.locator('button:has-text("Join now"), button:has-text("Join")').first();
        await altJoinButton.waitFor({ timeout: 5000 });
        await altJoinButton.click();
        log("✅ Clicked alternative join button");
        await page.waitForTimeout(5000);
      } catch (altError) {
        log("ℹ️ Alternative join button not found");
      }
    }

    await page.screenshot({ path: '/app/screenshots/teams-step-3-after-permissions.png', fullPage: true });
    log("📸 Screenshot: Step 3 - After join attempts");

    // Step 7: Check current state (exactly like simple-bot.js)
    log("Step 7: Checking current state...");
    const currentUrl = page.url();
    log(`📍 Current URL: ${currentUrl}`);
    
    // Check if we're in the meeting
    try {
      const meetingControls = page.getByRole('toolbar');
      await meetingControls.waitFor({ timeout: 3000 });
      log("🎉 Successfully joined the meeting!");
    } catch (error) {
      log("ℹ️ Meeting state unclear - may be in lobby or need manual intervention");
      
      // Check for lobby state
      try {
        const lobbyText = page.getByText('You\'re in the lobby');
        await lobbyText.waitFor({ timeout: 2000 });
        log("🚪 Currently in lobby - waiting for admission");
      } catch (lobbyError) {
        log("ℹ️ Not in lobby, checking for other states...");
      }
    }

    log("✅ Bot execution completed");

  } catch (error: any) {
    log(`❌ Error in Microsoft Teams bot: ${error.message}`);
    await gracefulLeaveFunction(page, 1, "teams_error", error);
  }
}

export async function leaveMicrosoftTeams(page: Page | null): Promise<boolean> {
  log("[leaveMicrosoftTeams] Debug implementation");
  return true;
}