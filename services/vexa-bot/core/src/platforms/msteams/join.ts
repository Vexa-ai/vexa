import { Page } from "playwright";
import { log, callJoiningCallback } from "../../utils";
import { BotConfig } from "../../types";
import {
  teamsContinueButtonSelectors,
  teamsJoinButtonSelectors,
  teamsCameraButtonSelectors,
  teamsNameInputSelectors,
  teamsComputerAudioRadioSelectors,
  teamsDontUseAudioRadioSelectors,
  teamsSpeakerEnableSelectors,
  teamsSpeakerDisableSelectors
} from "./selectors";

export async function joinMicrosoftTeams(page: Page, botConfig: BotConfig): Promise<void> {
  // Install RTCPeerConnection hook before any Teams scripts run - ensures remote audio tracks
  // are mirrored into hidden <audio> elements that BrowserAudioService can capture later.
  await page.addInitScript(() => {
    try {
      const win = window as any;
      if (win.__vexaRemoteAudioHookInstalled || typeof RTCPeerConnection !== 'function') {
        return;
      }

      win.__vexaRemoteAudioHookInstalled = true;
      win.__vexaInjectedAudioElements = win.__vexaInjectedAudioElements || [];
      const OriginalPC = RTCPeerConnection;

      function wrapPeerConnection(this: any, ...args: any[]) {
        const pc: RTCPeerConnection = new (OriginalPC as any)(...args);

        const handleTrack = (event: RTCTrackEvent) => {
          try {
            if (!event.track || event.track.kind !== 'audio') {
              return;
            }

            const stream = (event.streams && event.streams[0]) || new MediaStream([event.track]);

            const audioEl = document.createElement('audio');
            audioEl.autoplay = true;
            audioEl.muted = false;
            audioEl.volume = 1.0;
            audioEl.dataset.vexaInjected = 'true';
            audioEl.style.position = 'absolute';
            audioEl.style.left = '-9999px';
            audioEl.style.width = '1px';
            audioEl.style.height = '1px';
            audioEl.srcObject = stream;
            audioEl.play?.().catch(() => {});

            if (document.body) {
              document.body.appendChild(audioEl);
            } else {
              document.addEventListener('DOMContentLoaded', () => document.body?.appendChild(audioEl), { once: true });
            }

            (win.__vexaInjectedAudioElements as HTMLAudioElement[]).push(audioEl);
            win.__vexaCapturedRemoteAudioStreams = win.__vexaCapturedRemoteAudioStreams || [];
            win.__vexaCapturedRemoteAudioStreams.push(stream);

            win.logBot?.(`[Audio Hook] Injected remote audio element (track=${event.track.id}, readyState=${event.track.readyState}).`);
          } catch (hookError) {
            console.error('Vexa audio hook error:', hookError);
          }
        };

        pc.addEventListener('track', handleTrack);

        const originalOnTrack = Object.getOwnPropertyDescriptor(OriginalPC.prototype, 'ontrack');
        if (originalOnTrack && originalOnTrack.set) {
          Object.defineProperty(pc, 'ontrack', {
            set(handler: any) {
              if (typeof handler !== 'function') {
                return originalOnTrack.set!.call(this, handler);
              }
              const wrapped = function (this: RTCPeerConnection, event: RTCTrackEvent) {
                handleTrack(event);
                return handler.call(this, event);
              };
              return originalOnTrack.set!.call(this, wrapped);
            },
            get: originalOnTrack.get,
            configurable: true,
            enumerable: true
          });
        }

        return pc;
      }

      wrapPeerConnection.prototype = OriginalPC.prototype;
      Object.setPrototypeOf(wrapPeerConnection, OriginalPC);
      (window as any).RTCPeerConnection = wrapPeerConnection as any;

      win.logBot?.('[Audio Hook] RTCPeerConnection patched to mirror remote audio tracks.');
    } catch (initError) {
      console.error('Failed to install Vexa audio hook:', initError);
    }
  });

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

  log("Step 5.5: Ensuring Computer audio is selected...");
  try {
    await page.waitForTimeout(1000);
    const computerAudioRadio = page.locator(teamsComputerAudioRadioSelectors.join(', ')).first();
    const dontUseAudioRadio = page.locator(teamsDontUseAudioRadioSelectors.join(', ')).first();
    const computerAudioVisible = await computerAudioRadio.isVisible().catch(() => false);

    if (computerAudioVisible) {
      const dontUseAudioChecked =
        (await dontUseAudioRadio.isVisible().catch(() => false)) &&
        (await dontUseAudioRadio.getAttribute('aria-checked')) === 'true';

      if (dontUseAudioChecked) {
        log("⚠️ 'Don't use audio' detected. Switching to Computer audio...");
        await computerAudioRadio.click({ timeout: 5000 });
        await page.waitForTimeout(500);
      } else {
        await computerAudioRadio.click({ timeout: 5000 });
        await page.waitForTimeout(200);
      }
      log("✅ Computer audio selected.");
    } else {
      log("ℹ️ Audio radios not visible. Attempting to force-enable speaker...");
    }

    const speakerOnButton = page.locator(teamsSpeakerEnableSelectors.join(', ')).first();
    const speakerOffButton = page.locator(teamsSpeakerDisableSelectors.join(', ')).first();

    const speakerOnVisible = await speakerOnButton.isVisible().catch(() => false);
    const speakerOffVisible = await speakerOffButton.isVisible().catch(() => false);

    if (speakerOnVisible) {
      await speakerOnButton.click({ timeout: 5000 });
      await page.waitForTimeout(300);
      log("✅ Speaker enabled via toggle.");
    } else if (speakerOffVisible) {
      log("ℹ️ Speaker already enabled.");
    } else {
      log("ℹ️ Speaker controls not visible; continuing with defaults.");
    }

    await page.evaluate(() => {
      const audioEls = Array.from(document.querySelectorAll('audio'));
      audioEls.forEach((el: any) => {
        try {
          el.muted = false;
          el.autoplay = true;
          el.dataset.vexaTouched = 'true';
          if (typeof el.play === 'function') {
            el.play().catch(() => {});
          }
        } catch {}
      });
    });
  } catch (error: any) {
    log(`ℹ️ Could not enforce Computer audio: ${error.message}. Continuing...`);
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
