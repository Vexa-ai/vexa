import { Page } from "playwright";
import { log, randomDelay, callJoiningCallback } from "../../utils";
import { BotConfig } from "../../types";
import { 
  googleNameInputSelectors,
  googleJoinButtonSelectors,
  googleMicrophoneButtonSelectors,
  googleCameraButtonSelectors
} from "./selectors";

export async function joinGoogleMeeting(
  page: Page,
  meetingUrl: string,
  botName: string,
  botConfig: BotConfig
): Promise<void> {
  await page.goto(meetingUrl, { waitUntil: "domcontentloaded" });
  await page.bringToFront();

  // Take screenshot after navigation
  await page.screenshot({ path: '/app/storage/screenshots/bot-checkpoint-0-after-navigation.png', fullPage: true });
  log("📸 Screenshot taken: After navigation to meeting URL");

  // --- Call joining callback to notify meeting-api that bot is joining ---
  // Fix 2: Propagate JOINING callback failure — bot must NOT proceed if server rejected
  await callJoiningCallback(botConfig);
  log("Joining callback sent successfully");

  // Brief wait for page elements to settle (networkidle already ensures page loaded)
  await page.waitForTimeout(1000);

  if (botConfig.authenticated) {
    // Authenticated flow: browser is logged into Google, skip name input
    log("Authenticated mode: skipping name input (using Google account identity)");

    // Wait for the lobby to fully load (SPA needs time after domcontentloaded)
    log("Waiting for lobby to load...");
    await page.waitForTimeout(5000);

    // Diagnostic screenshot to see what the lobby shows
    await page.screenshot({ path: '/app/storage/screenshots/bot-checkpoint-auth-lobby.png', fullPage: true });
    log("📸 Diagnostic screenshot: auth lobby state");

    // Mute mic and camera if visible
    try {
      const micSelector = googleMicrophoneButtonSelectors[0];
      await page.click(micSelector, { timeout: 3000 });
      log("Microphone muted.");
    } catch (e) {
      log("Microphone already muted or not found.");
    }

    try {
      const cameraSelector = googleCameraButtonSelectors[0];
      await page.click(cameraSelector, { timeout: 3000 });
      log("Camera turned off.");
    } catch (e) {
      log("Camera already off or not found.");
    }

    // Authenticated users may see different buttons:
    // - "Join now" — standard authenticated join
    // - "Switch here" — same account already in the meeting
    // - "Ask to join" — cookies didn't load (fallback to anonymous)
    const joinNowSelector = 'button:has-text("Join now")';
    const switchHereSelector = 'button:has-text("Switch here")';
    const askToJoinSelector = googleJoinButtonSelectors[0];

    try {
      // Race: wait for any join button
      const joinButton = await Promise.race([
        page.waitForSelector(joinNowSelector, { timeout: 30000 }).then(el => ({ el, type: 'join_now' as const })),
        page.waitForSelector(switchHereSelector, { timeout: 30000 }).then(el => ({ el, type: 'switch_here' as const })),
        page.waitForSelector(askToJoinSelector, { timeout: 30000 }).then(el => ({ el, type: 'ask_to_join' as const })),
      ]);

      if (joinButton.type === 'join_now') {
        await joinButton.el!.click();
        log("Bot joined Google Meet as authenticated user (Join now).");
      } else if (joinButton.type === 'switch_here') {
        await joinButton.el!.click();
        log("Bot joined Google Meet as authenticated user (Switch here — same account already in call).");
      } else {
        // Cookies didn't work — fall back to anonymous join
        log("WARNING: Authenticated mode but 'Ask to join' found instead of 'Join now'. Cookies may not be loaded.");
        log("Falling back to anonymous-style join...");

        // Fill name since we're in anonymous territory
        try {
          const nameFieldSelector = googleNameInputSelectors[0];
          const nameField = await page.$(nameFieldSelector);
          if (nameField) {
            await page.fill(nameFieldSelector, botName);
            log(`Filled bot name: ${botName}`);
          }
        } catch (e) {
          log("No name field to fill.");
        }

        await joinButton.el!.click();
        log(`Bot joined Google Meet via fallback (Ask to join).`);
      }
    } catch (e) {
      // No button found — take diagnostic screenshot and fail
      await page.screenshot({ path: '/app/storage/screenshots/bot-checkpoint-auth-failed.png', fullPage: true });
      log("📸 Screenshot: No join button found after 30s");
      throw e;
    }

    await page.screenshot({ path: '/app/storage/screenshots/bot-checkpoint-0-after-join-now.png', fullPage: true });
    log("📸 Screenshot taken: After join click (authenticated)");
  } else {
    // Anonymous flow: enter bot name and join
    log("Attempting to find name input field...");

    const nameFieldSelector = googleNameInputSelectors[0];
    await page.waitForSelector(nameFieldSelector, { timeout: 120000 });
    log("Name input field found.");

    await page.screenshot({ path: '/app/storage/screenshots/bot-checkpoint-0-name-field-found.png', fullPage: true });

    await page.fill(nameFieldSelector, botName);

    // Mute mic and camera if available
    try {
      const micSelector = googleMicrophoneButtonSelectors[0];
      await page.click(micSelector, { timeout: 200 });
    } catch (e) {
      log("Microphone already muted or not found.");
    }

    try {
      const cameraSelector = googleCameraButtonSelectors[0];
      await page.click(cameraSelector, { timeout: 200 });
    } catch (e) {
      log("Camera already off or not found.");
    }

    // Try all join button selectors in order.
    // googleJoinButtonSelectors[0] is 'Ask to join' (regular/lobby meetings).
    // OPEN meetings created via Google Meet API (accessType=OPEN) show 'Join now'
    // instead — the loop finds whichever button is present, avoiding a 60s timeout.
    let joinClicked = false;
    for (const sel of googleJoinButtonSelectors) {
      try {
        await page.waitForSelector(sel, { timeout: 8000 });
        await page.click(sel);
        joinClicked = true;
        log(`${botName} joined the Google Meet Meeting (selector: ${sel}).`);
        break;
      } catch (e) {
        log(`Join button not found with selector: ${sel}, trying next...`);
      }
    }
    if (!joinClicked) {
      throw new Error('No join button found after trying all selectors');
    }

    await page.screenshot({ path: '/app/storage/screenshots/bot-checkpoint-0-after-ask-to-join.png', fullPage: true });
    log("📸 Screenshot taken: After clicking join button");
  }
}
