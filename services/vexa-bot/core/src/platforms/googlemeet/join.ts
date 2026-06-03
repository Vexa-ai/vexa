import { Page, ElementHandle } from "playwright";
import { log, callJoiningCallback } from "../../utils";
import { BotConfig } from "../../types";
import {
  googleNameInputSelectors,
  googleJoinButtonSelectors,
  googleMicrophoneButtonSelectors,
  googleCameraButtonSelectors
} from "./selectors";
import { HumanizedInteractor, MOCAP_LIBRARY } from "./humanized";

// Google Meet now blocks browser-synthetic input (Playwright/CDP clicks have
// isTrusted=false and no real pointer movement). "humanized" mode routes join
// interactions through real OS-level XTEST input along recorded-style mouse
// trajectories. Default it on for Google Meet; allow explicit override/opt-out.
export function resolveUiInteractionMode(botConfig: BotConfig): "humanized" | "synthetic" {
  if (botConfig.uiInteractionMode) return botConfig.uiInteractionMode;
  return botConfig.platform === "google_meet" ? "humanized" : "synthetic";
}

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

  // --- Humanized input layer (defeats Google Meet input-authenticity detection) ---
  const uiMode = resolveUiInteractionMode(botConfig);
  let humanizer: HumanizedInteractor | null = null;
  if (uiMode === "humanized") {
    humanizer = new HumanizedInteractor(MOCAP_LIBRARY, { log });
    if (!(await humanizer.available())) {
      log("WARNING: humanized UI mode requested but xdotool/X display is unavailable — falling back to synthetic input. Install xdotool+xclip in the bot image.");
      humanizer = null;
    } else {
      log("Humanized UI interaction mode active (OS-level XTEST input).");
    }
  }

  // Click a resolved element handle via humanized motion, falling back to a
  // synthetic handle click if humanized interaction is off or errors.
  const clickHandle = async (handle: ElementHandle<Element>, label: string): Promise<void> => {
    if (humanizer) {
      try {
        await humanizer.navigateAndClick(page, handle);
        return;
      } catch (e) {
        log(`Humanized click failed for '${label}' (${e}); falling back to synthetic click.`);
      }
    }
    await handle.click();
  };

  // Fill a text field via humanized click+paste, falling back to page.fill.
  const fillField = async (
    handle: ElementHandle<Element>,
    selector: string,
    text: string,
    label: string
  ): Promise<void> => {
    if (humanizer) {
      try {
        await humanizer.fillField(page, handle, text);
        return;
      } catch (e) {
        log(`Humanized fill failed for '${label}' (${e}); falling back to page.fill.`);
      }
    }
    await page.fill(selector, text);
  };

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
      const micHandle = await page.waitForSelector(googleMicrophoneButtonSelectors[0], { timeout: 3000 });
      if (micHandle) { await clickHandle(micHandle, "microphone"); log("Microphone muted."); }
    } catch (e) {
      log("Microphone already muted or not found.");
    }

    try {
      const cameraHandle = await page.waitForSelector(googleCameraButtonSelectors[0], { timeout: 3000 });
      if (cameraHandle) { await clickHandle(cameraHandle, "camera"); log("Camera turned off."); }
    } catch (e) {
      log("Camera already off or not found.");
    }

    // Authenticated users may see different buttons:
    // - "Join now" — standard authenticated join
    // - "Switch here" — same account already in the meeting
    // - "Ask to join" — cookies didn't load (fallback to anonymous)
    // Localized for the 5 most common Vexa user locales (en/es/pt/fr/de/it).
    // Account language drives the actual rendered label, not the meeting domain.
    const joinNowVariants = [
      'button:has-text("Join now")',          // en
      'button:has-text("Unirse ahora")',      // es
      'button:has-text("Participar agora")',  // pt
      'button:has-text("Participer maintenant")', // fr
      'button:has-text("Jetzt teilnehmen")',  // de
      'button:has-text("Partecipa ora")',     // it
      'button:has-text("Uneix-te ara")',      // ca
    ];
    const switchHereVariants = [
      'button:has-text("Switch here")',       // en
      'button:has-text("Cambiar aquí")',      // es
      'button:has-text("Trocar aqui")',       // pt
      'button:has-text("Changer ici")',       // fr
      'button:has-text("Hier wechseln")',     // de
      'button:has-text("Passa qui")',         // it
      'button:has-text("Canvia aquí")',       // ca
    ];
    const askToJoinSelector = googleJoinButtonSelectors[0];

    try {
      // Wrap each promise so rejection turns into a never-resolving promise.
      // This lets Promise.race wait for the FIRST resolve across all variants
      // without being short-circuited by the first reject (locale variants
      // that don't match will time out and reject — most variants reject by
      // design since only the user's locale matches).
      // ES2021 Promise.any would be cleaner but tsconfig targets ES2020.
      const neverRejects = <T>(p: Promise<T>): Promise<T> =>
        p.catch(() => new Promise<T>(() => { /* hang forever on reject */ }));

      const joinButton = await Promise.race([
        ...joinNowVariants.map(sel => neverRejects(page.waitForSelector(sel, { timeout: 30000 }).then(el => ({ el, type: 'join_now' as const })))),
        ...switchHereVariants.map(sel => neverRejects(page.waitForSelector(sel, { timeout: 30000 }).then(el => ({ el, type: 'switch_here' as const })))),
        neverRejects(page.waitForSelector(askToJoinSelector, { timeout: 30000 }).then(el => ({ el, type: 'ask_to_join' as const }))),
        // Global timeout so the race terminates if NO variant matches
        new Promise<never>((_, rej) => setTimeout(() => rej(new Error('No join button visible in any locale within 30s')), 30000)),
      ]);

      if (joinButton.type === 'join_now') {
        await clickHandle(joinButton.el!, "join_now");
        log("Bot joined Google Meet as authenticated user (Join now).");
      } else if (joinButton.type === 'switch_here') {
        await clickHandle(joinButton.el!, "switch_here");
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
            await fillField(nameField, nameFieldSelector, botName, "name");
            log(`Filled bot name: ${botName}`);
          }
        } catch (e) {
          log("No name field to fill.");
        }

        await clickHandle(joinButton.el!, "ask_to_join");
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
    // Anonymous flow: enter bot name and ask to join
    log("Attempting to find name input field...");

    const nameFieldSelector = googleNameInputSelectors[0];
    const nameHandle = await page.waitForSelector(nameFieldSelector, { timeout: 120000 });
    log("Name input field found.");

    await page.screenshot({ path: '/app/storage/screenshots/bot-checkpoint-0-name-field-found.png', fullPage: true });

    await fillField(nameHandle!, nameFieldSelector, botName, "name");

    // Mute mic and camera if available
    try {
      const micHandle = await page.waitForSelector(googleMicrophoneButtonSelectors[0], { timeout: 1000 });
      if (micHandle) await clickHandle(micHandle, "microphone");
    } catch (e) {
      log("Microphone already muted or not found.");
    }

    try {
      const cameraHandle = await page.waitForSelector(googleCameraButtonSelectors[0], { timeout: 1000 });
      if (cameraHandle) await clickHandle(cameraHandle, "camera");
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
        const joinHandle = await page.waitForSelector(sel, { timeout: 8000 });
        if (joinHandle) {
          await clickHandle(joinHandle, "join_button");
          joinClicked = true;
          log(`${botName} joined the Google Meet Meeting (selector: ${sel}).`);
          break;
        }
      } catch (e) {
        log(`Join button not found with selector: ${sel}, trying next...`);
      }
    }
    if (!joinClicked) {
      throw new Error('No join button found after trying all selectors');
    }

    await page.screenshot({ path: '/app/storage/screenshots/bot-checkpoint-0-after-ask-to-join.png', fullPage: true });
    log('📸 Screenshot taken: After clicking join button');
  }
}
