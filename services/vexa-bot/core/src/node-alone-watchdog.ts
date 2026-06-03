import { Page } from 'playwright';
import { log } from './utils';

const POLL_INTERVAL_MS = 5000;
const EVALUATE_TIMEOUT_MS = 4000; // Must be < POLL_INTERVAL_MS

/**
 * AIS-151 Fix #1 — Node.js-side alone-detection fallback.
 *
 * Browser-side setInterval timers never fire if the page JS context hangs.
 * This watchdog runs entirely on the Node.js event loop and independently
 * tracks how long the bot appears to be alone by polling participant state
 * via Playwright page.evaluate(). If the page is unresponsive (evaluate
 * times out) it counts as "alone". Triggers onTimeout() when the bot has
 * been alone for timeoutSeconds consecutively.
 *
 * This is a safety net only — browser-side logic remains primary.
 */
export function startNodeAloneWatchdog(
  page: Page,
  timeoutSeconds: number,
  onTimeout: () => Promise<void>
): () => void {
  let aloneSeconds = 0;
  let stopped = false;
  // Prevents overlapping evaluate calls if tick fires while previous is still pending.
  // Defensive only — evaluate timeout (4s) < interval (5s) so overlap is unlikely.
  let evaluating = false;

  log(`[NodeWatchdog] Started — threshold=${timeoutSeconds}s, poll=${POLL_INTERVAL_MS / 1000}s`);

  const interval = setInterval(async () => {
    if (stopped) return;
    if (evaluating) return;

    evaluating = true;
    let participantCount = 0;
    try {
      participantCount = await Promise.race([
        page.evaluate(() => {
          const els = Array.from(document.querySelectorAll('audio, video'));
          // Count non-paused media elements as a rough proxy for active participants.
          // This is intentionally simple — precision is the browser-side loop's job.
          return els.filter((el: any) => !el.paused && el.readyState > 1).length;
        }),
        new Promise<number>((_, reject) =>
          setTimeout(() => reject(new Error('evaluate_timeout')), EVALUATE_TIMEOUT_MS)
        ),
      ]);
    } catch {
      // Page unresponsive or evaluate failed — treat as alone.
      log('[NodeWatchdog] page.evaluate() failed or timed out — counting as alone');
      participantCount = 0;
    } finally {
      evaluating = false;
    }

    if (participantCount > 1) {
      if (aloneSeconds > 0) {
        log(`[NodeWatchdog] Participants detected (count=${participantCount}) — resetting alone counter`);
      }
      aloneSeconds = 0;
    } else {
      aloneSeconds += POLL_INTERVAL_MS / 1000;
      if (aloneSeconds % 30 === 0) {
        log(`[NodeWatchdog] Alone for ${aloneSeconds}s / ${timeoutSeconds}s`);
      }
    }

    if (aloneSeconds >= timeoutSeconds) {
      log(`[NodeWatchdog] Threshold reached (${aloneSeconds}s) — triggering graceful leave`);
      stopped = true;
      clearInterval(interval);
      onTimeout().catch((err: any) =>
        log(`[NodeWatchdog] onTimeout error: ${err?.message}`)
      );
    }
  }, POLL_INTERVAL_MS);

  return () => {
    if (stopped) return;
    stopped = true;
    clearInterval(interval);
    log('[NodeWatchdog] Stopped');
  };
}
