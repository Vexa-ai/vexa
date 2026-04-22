import { Page } from 'playwright';
import { log } from '../../utils';
import { zoomLeaveButtonSelector, zoomMeetingEndedModalSelector, zoomRemovalTexts } from './selectors';

/**
 * Starts polling for removal/end-of-meeting events.
 * Returns a cleanup function that stops polling.
 */
// Page titles that indicate Zoom redirected away from the meeting (to sign-in or join page)
const zoomPostMeetingTitles = ['Zoom', 'Join a Meeting - Zoom', 'Join Meeting - Zoom'];

// URL patterns that are part of Zoom's normal join/audio-init redirect sequence.
// These should NOT trigger removal — they are transient navigations during the handshake.
const ZOOM_AUDIO_INIT_URL_PATTERNS = [
  /\/wc\/\d+\/join/,    // /wc/{id}/join — pre-join page revisited during audio handshake
  /\/wc\/\d+\/start/,   // /wc/{id}/start — host start page redirect
  /\/wc-loading\//,     // Web client loading screen
  /\/wc\/\d+\/videomeeting/, // Video meeting start redirect
];

function isZoomAudioInitUrl(url: string): boolean {
  return ZOOM_AUDIO_INIT_URL_PATTERNS.some(pattern => pattern.test(url));
}

export function startZoomWebRemovalMonitor(
  page: Page | null,
  onRemoval?: () => void | Promise<void>
): () => void {
  if (!page) return () => {};

  let stopped = false;
  let consecutiveLeaveButtonMisses = 0;
  const LEAVE_BUTTON_MISS_THRESHOLD = 3; // Require 3 consecutive misses (9s) before acting
  const joinedAtMs = Date.now();
  const GRACE_PERIOD_MS = 20_000; // 20s grace — Zoom audio init can take 10-15s with slow networks

  const triggerRemoval = async (reason: string) => {
    if (stopped) return;
    stopped = true;
    const elapsed = ((Date.now() - joinedAtMs) / 1000).toFixed(1);
    log(`[Zoom Web] REMOVAL TRIGGERED (${elapsed}s after join): ${reason}`);
    log(`[Zoom Web] Current URL at removal: ${page.url()}`);
    const title = await page.title().catch(() => '<unknown>');
    log(`[Zoom Web] Current title at removal: "${title}"`);
    onRemoval && await onRemoval();
  };

  // Fast path: detect navigation away from the meeting page immediately via framenavigated.
  // Zoom redirects to /wc/{id}/join or zoom.us/signin when the meeting ends without a modal.
  const onNavigated = (frame: any) => {
    if (stopped || frame !== page.mainFrame()) return;
    const url: string = frame.url();
    if (!url || url.startsWith('about:')) return;
    const elapsed = ((Date.now() - joinedAtMs) / 1000).toFixed(1);

    // Grace period: Zoom performs internal redirects during audio init handshake
    // right after join. Ignore navigations during this window to avoid false ejection.
    if (Date.now() - joinedAtMs < GRACE_PERIOD_MS) {
      log(`[Zoom Web] Ignoring navigation during grace period (${elapsed}s after join): ${url}`);
      return;
    }

    // Always ignore known audio-init redirect URLs regardless of grace period.
    // These patterns appear during Zoom's audio handshake which can extend beyond the grace window.
    if (isZoomAudioInitUrl(url)) {
      log(`[Zoom Web] Ignoring audio-init redirect URL (${elapsed}s after join): ${url}`);
      return;
    }

    // Any navigation away from the zoom.us domain means the meeting ended
    // (covers company SSO redirects, homepages, sign-in pages, etc.)
    if (!/zoom\.(us|com|eu|com\.cn|com\.br|com\.au|de|fr|jp|ca|co\.uk)\b/.test(url)) {
      triggerRemoval(`Navigation away from Zoom domain: ${url}`);
    } else if (url.includes('/signin') || url.includes('/login')) {
      // Explicit sign-in redirect — meeting definitely ended
      triggerRemoval(`Navigation to Zoom sign-in: ${url}`);
    } else if (url.includes('/wc/') && !url.includes('/meeting')) {
      // Non-meeting /wc/ URL — but only if it's not a known init pattern
      log(`[Zoom Web] Suspicious non-meeting /wc/ URL (${elapsed}s after join): ${url} — deferring to polling`);
      // Don't trigger immediately — let the polling loop confirm via Leave button absence
    }
  };
  page.on('framenavigated', onNavigated);

  const poll = async () => {
    if (stopped || !page || page.isClosed()) return;

    try {
      // Check for end-of-meeting modal (zm-modal-body-title)
      const modalEl = page.locator(zoomMeetingEndedModalSelector).first();
      const modalVisible = await modalEl.isVisible({ timeout: 300 }).catch(() => false);
      if (modalVisible) {
        const modalText = await modalEl.textContent() ?? '';
        const trimmed = modalText.trim();
        const isRemoval = zoomRemovalTexts.some(t => trimmed.includes(t));
        if (isRemoval) {
          await triggerRemoval(`Removal/end modal detected: "${trimmed}"`);
          return;
        } else {
          log(`[Zoom Web] Ignoring non-removal modal: "${trimmed}"`);
        }
      }

      // Check via body text for removal phrases
      const detected = await page.evaluate((texts: string[]) => {
        const bodyText = document.body.innerText || '';
        return texts.find(t => bodyText.includes(t)) || null;
      }, zoomRemovalTexts).catch(() => null);

      if (detected) {
        await triggerRemoval(`Removal detected via text: "${detected}"`);
        return;
      }

      // Check if Leave button disappeared — require consecutive misses to avoid
      // false positives from Zoom UI transitions (popups, tooltips, feature tips).
      const leaveVisible = await page.locator(zoomLeaveButtonSelector).first()
        .isVisible({ timeout: 300 }).catch(() => false);
      if (!leaveVisible) {
        consecutiveLeaveButtonMisses++;
        const url = page.url();
        const title = await page.title().catch(() => '');
        const elapsed = ((Date.now() - joinedAtMs) / 1000).toFixed(1);
        log(`[Zoom Web] Leave button miss #${consecutiveLeaveButtonMisses} (${elapsed}s after join) — URL: ${url}, title: "${title}"`);

        // During grace period or on audio-init URLs, don't act on Leave button absence.
        // Zoom's UI hasn't fully loaded yet — Leave button simply doesn't exist.
        if (Date.now() - joinedAtMs < GRACE_PERIOD_MS || isZoomAudioInitUrl(url)) {
          log(`[Zoom Web] Suppressing Leave button miss — still in grace/audio-init phase`);
        } else {
          // Navigated off Zoom entirely — immediate exit (no counter needed)
          if (url && !url.startsWith('about:') && !/zoom\.(us|com|eu|com\.cn|com\.br|com\.au|de|fr|jp|ca|co\.uk)\b/.test(url)) {
            await triggerRemoval(`Leave button gone and URL left Zoom domain: ${url}`);
            return;
          }
          // Redirected to sign-in — immediate exit
          if (url.includes('/signin') || url.includes('/login')) {
            await triggerRemoval(`Leave button gone and redirected to sign-in: ${url}`);
            return;
          }
          // For other conditions, only act after consecutive misses
          if (consecutiveLeaveButtonMisses >= LEAVE_BUTTON_MISS_THRESHOLD) {
            // Redirected away from meeting page within Zoom
            if (url.includes('/wc/') && !url.includes('/meeting')) {
              await triggerRemoval(`Leave button gone ${consecutiveLeaveButtonMisses}x and URL is non-meeting: ${url}`);
              return;
            }
            // Error page or blank
            if (title === 'Error - Zoom' || title === '') {
              await triggerRemoval(`Leave button gone ${consecutiveLeaveButtonMisses}x and page shows error (title="${title}")`);
              return;
            }
            // Generic post-meeting title
            if (zoomPostMeetingTitles.includes(title)) {
              await triggerRemoval(`Leave button gone ${consecutiveLeaveButtonMisses}x and post-meeting title: "${title}"`);
              return;
            }
          }
        }
      } else {
        if (consecutiveLeaveButtonMisses > 0) {
          log(`[Zoom Web] Leave button recovered after ${consecutiveLeaveButtonMisses} miss(es)`);
        }
        consecutiveLeaveButtonMisses = 0;
      }
    } catch {
      // Page navigated away or context destroyed
      await triggerRemoval('Exception in removal poll — page likely navigated away');
      return;
    }

    if (!stopped) {
      setTimeout(poll, 3000);
    }
  };

  setTimeout(poll, 3000);

  return () => {
    stopped = true;
    page.off('framenavigated', onNavigated);
    log('[Zoom Web] Removal monitor stopped');
  };
}
