import { Page } from 'playwright';
import { BotConfig } from '../../../types';
import { log } from '../../../utils';
import { zoomAudioButtonSelector, zoomChatButtonSelector } from './selectors';

/**
 * Post-admission setup: join computer audio, dismiss any popups, verify audio.
 */
export async function prepareZoomWebMeeting(page: Page | null, botConfig: BotConfig): Promise<void> {
  if (!page) throw new Error('[Zoom Web] Page required for prepare');

  log('[Zoom Web] Preparing meeting post-admission...');

  // Dismiss popups that overlay the meeting content
  await dismissZoomPopups(page);

  // Join computer audio — retry up to 8 times with escalating strategies.
  // This is CRITICAL: without joining audio, no <audio> elements are created
  // and the per-speaker capture pipeline gets zero audio data.
  let audioJoined = false;
  for (let attempt = 0; attempt < 8; attempt++) {
    try {
      // First: check if a "Join with Computer Audio" dialog is already open (ReactModal).
      // This MUST come before clicking the footer button, because the modal blocks footer clicks.
      const computerAudioBtn = page.locator([
        'button:has-text("Join with Computer Audio")',
        'button:has-text("Join Audio by Computer")',
        'button:has-text("Computer Audio")',
      ].join(', ')).first();
      try {
        if (await computerAudioBtn.isVisible({ timeout: 1500 })) {
          await computerAudioBtn.click();
          log('[Zoom Web] Clicked "Join with Computer Audio" dialog button');
          audioJoined = true;
          break;
        }
      } catch { /* dialog not open */ }

      // Check if audio is already joined (Mute/Unmute button visible)
      const audioBtn = page.locator(zoomAudioButtonSelector).first();
      const visible = await audioBtn.isVisible({ timeout: 2000 });
      if (visible) {
        const ariaLabel = await audioBtn.getAttribute('aria-label');
        log(`[Zoom Web] Audio button aria-label: "${ariaLabel}" (attempt ${attempt + 1})`);

        // If aria-label is "Mute" or "Unmute", audio is already joined
        if (ariaLabel && (ariaLabel === 'Mute' || ariaLabel === 'Unmute')) {
          log('[Zoom Web] Audio already joined (mic toggle visible)');
          audioJoined = true;
          break;
        }

        // If aria-label contains "join audio" or is just "audio", click to open dialog
        if (ariaLabel && (ariaLabel.toLowerCase().includes('join audio') || ariaLabel.toLowerCase() === 'audio')) {
          await audioBtn.click({ timeout: 5000 });
          log('[Zoom Web] Clicked Join Audio footer button — waiting for dialog...');
          await page.waitForTimeout(1500);

          // Immediately check for dialog that just opened
          try {
            if (await computerAudioBtn.isVisible({ timeout: 3000 })) {
              await computerAudioBtn.click();
              log('[Zoom Web] Clicked "Join with Computer Audio" in dialog');
              audioJoined = true;
              break;
            }
          } catch { /* dialog didn't appear — will retry */ }
          continue;
        }
      }

      // Try the floating "Join Audio" banner (appears on some Zoom versions)
      const joinAudioBanner = page.locator('button:has-text("Join Audio")').first();
      const bannerVisible = await joinAudioBanner.isVisible({ timeout: 1000 }).catch(() => false);
      if (bannerVisible) {
        await joinAudioBanner.click();
        log(`[Zoom Web] Clicked "Join Audio" banner (attempt ${attempt + 1})`);
        await page.waitForTimeout(1500);

        // Check for dialog
        try {
          if (await computerAudioBtn.isVisible({ timeout: 3000 })) {
            await computerAudioBtn.click();
            log('[Zoom Web] Clicked "Join with Computer Audio" after banner');
            audioJoined = true;
            break;
          }
        } catch { /* no dialog */ }
        continue;
      }

      // Nothing found yet — wait and retry
      log(`[Zoom Web] No audio controls visible (attempt ${attempt + 1}), waiting...`);
      await page.waitForTimeout(2000);
    } catch (e: any) {
      log(`[Zoom Web] Audio join attempt ${attempt + 1} failed: ${e.message}`);
    }
  }

  // Final verification: check if Mute/Unmute appeared after all attempts
  if (!audioJoined) {
    try {
      const finalCheck = page.locator(zoomAudioButtonSelector).first();
      const finalLabel = await finalCheck.getAttribute('aria-label').catch(() => null);
      if (finalLabel === 'Mute' || finalLabel === 'Unmute') {
        log('[Zoom Web] Audio joined (confirmed on final check)');
        audioJoined = true;
      }
    } catch { /* ignore */ }
  }

  if (!audioJoined) {
    log('[Zoom Web] WARNING: Could not confirm audio join after all attempts — per-speaker capture may fail');
  }

  // Dismiss the "Please enable microphone/camera" notification banner if present
  try {
    const closeNotif = page.locator('button[aria-label="Close notification"], .notification-close, button:has-text("×")').first();
    if (await closeNotif.isVisible({ timeout: 1000 })) {
      await closeNotif.click();
    }
  } catch { /* no banner */ }

  // Verify audio elements exist after joining (delayed check — elements may take time to appear)
  await verifyAudioElements(page);

  log('[Zoom Web] Meeting preparation complete');
}

/**
 * Check for <audio>/<video> elements with MediaStream srcObject.
 * Logs what was found for diagnostic purposes — does NOT block.
 */
async function verifyAudioElements(page: Page): Promise<void> {
  try {
    await page.waitForTimeout(3000); // Give Zoom time to create media elements

    const audioInfo = await page.evaluate(() => {
      const elements = Array.from(document.querySelectorAll('audio, video'));
      const withStreams = elements.filter((el: any) =>
        el.srcObject instanceof MediaStream &&
        el.srcObject.getAudioTracks().length > 0
      );
      return {
        totalElements: elements.length,
        withAudioStreams: withStreams.length,
        details: withStreams.map((el: any, i: number) => {
          const stream: MediaStream = el.srcObject;
          const tracks = stream.getAudioTracks();
          return {
            index: i,
            tag: el.tagName.toLowerCase(),
            paused: el.paused,
            trackCount: tracks.length,
            trackStates: tracks.map(t => ({ enabled: t.enabled, muted: t.muted, readyState: t.readyState })),
          };
        }),
      };
    });

    log(`[Zoom Web] Audio verification: ${audioInfo.withAudioStreams} elements with audio streams (${audioInfo.totalElements} total media elements)`);
    if (audioInfo.withAudioStreams > 0) {
      for (const d of audioInfo.details) {
        log(`[Zoom Web]   Element ${d.index} <${d.tag}>: paused=${d.paused}, tracks=${d.trackCount}, states=${JSON.stringify(d.trackStates)}`);
      }
    } else {
      log('[Zoom Web] WARNING: No audio elements found — bot may not have joined audio channel');
    }
  } catch (e: any) {
    log(`[Zoom Web] Audio verification failed: ${e.message}`);
  }
}

/**
 * Dismiss known Zoom Web popups/modals that overlay meeting content.
 * Safe to call repeatedly — each check is short-circuited if the popup isn't visible.
 */
export async function dismissZoomPopups(page: Page): Promise<void> {
  // All checks use timeout:0 — instant visibility check, no waiting.
  // This function is polled every 2s so there's no need to wait for elements to appear.
  const dismissTargets = [
    { selector: '.zm-modal button:has-text("OK")', label: 'AI Companion' },
    { selector: '.relative-tooltip button:has-text("Got it")', label: 'chatting as guest' },
    { selector: '.settings-feature-tips button:has-text("OK")', label: 'feature tip' },
    { selector: '.ReactModal__Content button:has-text("OK")', label: 'modal OK' },
    { selector: '.ReactModal__Content button:has-text("Got it")', label: 'modal Got it' },
    { selector: '[role="presentation"] button:has-text("OK")', label: 'presentation OK' },
  ];

  for (const { selector, label } of dismissTargets) {
    try {
      const btn = page.locator(selector).first();
      if (await btn.isVisible({ timeout: 0 })) {
        await btn.click();
        log(`[Zoom Web] Dismissed "${label}" popup`);
      }
    } catch { /* not present or already gone */ }
  }
}
