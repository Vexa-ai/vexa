import { Page } from 'playwright';
import { log } from '../../../utils';
import { zoomLeaveButtonSelector, zoomMeetingEndedModalSelector, zoomRemovalTexts } from './selectors';

/**
 * Starts polling for removal/end-of-meeting events.
 * Returns a cleanup function that stops polling.
 */
// Page titles that indicate Zoom redirected away from the meeting (to sign-in or join page)
const zoomPostMeetingTitles = ['Zoom', 'Join a Meeting - Zoom', 'Join Meeting - Zoom'];

export function startZoomWebRemovalMonitor(
  page: Page | null,
  onRemoval?: () => void | Promise<void>
): () => void {
  if (!page) return () => {};

  let stopped = false;

  const triggerRemoval = async (reason: string) => {
    if (stopped) return;
    stopped = true;
    log(`[Zoom Web] ${reason}`);
    onRemoval && await onRemoval();
  };

  // Fast path: detect navigation away from the meeting page immediately via framenavigated.
  // Zoom redirects to /wc/{id}/join or zoom.us/signin when the meeting ends without a modal.
  const onNavigated = (frame: any) => {
    if (stopped || frame !== page.mainFrame()) return;
    const url: string = frame.url();
    if (!url || url.startsWith('about:')) return;
    // Any navigation away from the zoom.us domain means the meeting ended
    // (covers company SSO redirects, homepages, sign-in pages, etc.)
    if (!/zoom\.(us|com|eu|com\.cn|com\.br|com\.au|de|fr|jp|ca|co\.uk)\b/.test(url)) {
      triggerRemoval(`Navigation away from Zoom domain: ${url}`);
    } else if (url.includes('/wc/') && !url.includes('/meeting')) {
      triggerRemoval(`Navigation to non-meeting Zoom URL: ${url}`);
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

      // Check if Leave button disappeared
      const leaveVisible = await page.locator(zoomLeaveButtonSelector).first()
        .isVisible({ timeout: 300 }).catch(() => false);
      if (!leaveVisible) {
        const url = page.url();
        const title = await page.title().catch(() => '');
        // Navigated off Zoom entirely (e.g. company SSO homepage)
        if (url && !url.startsWith('about:') && !/zoom\.(us|com|eu|com\.cn|com\.br|com\.au|de|fr|jp|ca|co\.uk)\b/.test(url)) {
          await triggerRemoval(`Leave button gone and URL left Zoom domain: ${url}`);
          return;
        }
        // Redirected away from meeting page within Zoom
        if (url.includes('/wc/') && !url.includes('/meeting')) {
          await triggerRemoval(`Leave button gone and URL is non-meeting: ${url}`);
          return;
        }
        // Redirected to sign-in
        if (url.includes('/signin') || url.includes('/login')) {
          await triggerRemoval(`Leave button gone and redirected to sign-in: ${url}`);
          return;
        }
        // Error page or blank
        if (title === 'Error - Zoom' || title === '') {
          await triggerRemoval(`Leave button gone and page shows error (title="${title}")`);
          return;
        }
        // Generic post-meeting title (Zoom redirects to a plain "Zoom" page after meeting ends)
        if (zoomPostMeetingTitles.includes(title)) {
          await triggerRemoval(`Leave button gone and post-meeting title detected: "${title}"`);
          return;
        }
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
