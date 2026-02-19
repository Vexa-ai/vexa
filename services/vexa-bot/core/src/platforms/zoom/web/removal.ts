import { Page } from 'playwright';
import { log } from '../../../utils';
import { zoomLeaveButtonSelector, zoomMeetingEndedModalSelector, zoomRemovalTexts } from './selectors';

/**
 * Starts polling for removal/end-of-meeting events.
 * Returns a cleanup function that stops polling.
 */
export function startZoomWebRemovalMonitor(
  page: Page | null,
  onRemoval?: () => void | Promise<void>
): () => void {
  if (!page) return () => {};

  let stopped = false;

  const poll = async () => {
    if (stopped || !page || page.isClosed()) return;

    try {
      // Check for end-of-meeting modal (zm-modal-body-title)
      const modalEl = page.locator(zoomMeetingEndedModalSelector).first();
      const modalVisible = await modalEl.isVisible({ timeout: 300 }).catch(() => false);
      if (modalVisible) {
        const modalText = await modalEl.textContent();
        log(`[Zoom Web] Removal/end modal detected: "${modalText?.trim()}"`);
        stopped = true;
        onRemoval && await onRemoval();
        return;
      }

      // Check via body text for removal phrases
      const detected = await page.evaluate((texts: string[]) => {
        const bodyText = document.body.innerText || '';
        return texts.find(t => bodyText.includes(t)) || null;
      }, zoomRemovalTexts).catch(() => null);

      if (detected) {
        log(`[Zoom Web] Removal detected via text: "${detected}"`);
        stopped = true;
        onRemoval && await onRemoval();
        return;
      }

      // Check if Leave button disappeared (sudden disconnect)
      const leaveVisible = await page.locator(zoomLeaveButtonSelector).first()
        .isVisible({ timeout: 300 }).catch(() => false);
      if (!leaveVisible) {
        // Could be normal navigation — check title
        const title = await page.title().catch(() => '');
        if (title === 'Error - Zoom' || title === '') {
          log('[Zoom Web] Leave button gone and page shows error — meeting ended');
          stopped = true;
          onRemoval && await onRemoval();
          return;
        }
      }
    } catch {
      // Page navigated away
      if (!stopped) {
        stopped = true;
        onRemoval && await onRemoval();
      }
      return;
    }

    if (!stopped) {
      setTimeout(poll, 3000);
    }
  };

  setTimeout(poll, 3000);

  return () => {
    stopped = true;
    log('[Zoom Web] Removal monitor stopped');
  };
}
