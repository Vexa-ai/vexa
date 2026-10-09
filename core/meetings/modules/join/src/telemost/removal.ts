import { Page } from "playwright";
import { log } from "../_host";
import { callFrame, findBodyText, hasCallFrame, isBannedPage, isLeaveVisible, terminalConsoleCode } from "./admission";
import {
  telemostRemovalTexts,
  telemostPostMeetingIndicators,
  telemostPostMeetingTexts,
} from "./selectors";

/**
 * Poll for removal / end-of-meeting. Returns a cleanup fn that stops polling.
 *
 * Signals, strongest first:
 *   1. The media engine's disconnect code in the page console (KICKED_OUT /
 *      ROOM_HAS_BEEN_CLOSED) — latched by join.ts before navigation.
 *   2. The kicked-with-ban page, or removal / end-of-call text
 *      ("вас удалили из звонка", "организатор завершил встречу для всех", …).
 *   3. Navigation away from the meeting path (`/j/<id>`), or the call iframe
 *      detaching for N consecutive polls (the shell tears the call down).
 *   4. Leave control gone for N consecutive polls + a post-meeting screen
 *      (rating dialog / "create a call" home) — the weakest, so it needs both.
 * A grace period suppresses the DOM signals while the call UI is still settling
 * right after admission; the console code is authoritative at any time.
 */
export function startTelemostRemovalMonitor(
  page: Page | null,
  onRemoval?: () => void | Promise<void>,
): () => void {
  if (!page) return () => {};

  let stopped = false;
  let consecutiveLeaveMisses = 0;
  const LEAVE_MISS_THRESHOLD = 3;   // 3 misses × 3s poll = 9s
  const FRAME_MISS_THRESHOLD = 3;   // call iframe gone 3 polls in a row
  let consecutiveFrameMisses = 0;
  const hadCallFrame = hasCallFrame(page);
  const joinedAtMs = Date.now();
  const GRACE_PERIOD_MS = 20_000;

  // The app rewrites `/j/<id>` to `/@/j/<id>` after load; compare on the id segment.
  const meetingId = (() => {
    try { return /\/j\/(\d+)/.exec(new URL(page.url()).pathname)?.[1] ?? null; } catch { return null; }
  })();

  const triggerRemoval = async (reason: string) => {
    if (stopped) return;
    stopped = true;
    const elapsed = ((Date.now() - joinedAtMs) / 1000).toFixed(1);
    log(`[Telemost] REMOVAL TRIGGERED (${elapsed}s after join): ${reason}`);
    log(`[Telemost] Current URL at removal: ${page.url()}`);
    onRemoval && await onRemoval();
  };

  const onNavigated = (frame: any) => {
    if (stopped || frame !== page.mainFrame()) return;
    const url: string = frame.url();
    if (!url || url.startsWith("about:")) return;
    if (Date.now() - joinedAtMs < GRACE_PERIOD_MS) return;
    try {
      const id = /\/j\/(\d+)/.exec(new URL(url).pathname)?.[1] ?? null;
      if (meetingId && id !== meetingId) {
        triggerRemoval(`Navigation away from the Telemost meeting: ${url}`);
      }
    } catch { /* unparsable URL — leave it to the poll loop */ }
  };
  page.on("framenavigated", onNavigated);

  const poll = async () => {
    if (stopped || !page || page.isClosed()) return;

    try {
      const code = terminalConsoleCode(page);
      if (code) {
        await triggerRemoval(`Media engine disconnect: ${code}`);
        return;
      }

      if (Date.now() - joinedAtMs >= GRACE_PERIOD_MS) {
        if (hadCallFrame && !hasCallFrame(page)) {
          consecutiveFrameMisses++;
          if (consecutiveFrameMisses >= FRAME_MISS_THRESHOLD) {
            await triggerRemoval(`Call frame detached ${consecutiveFrameMisses}x`);
            return;
          }
        } else {
          consecutiveFrameMisses = 0;
        }
        if (await isBannedPage(page)) {
          await triggerRemoval("Kicked — banned page shown");
          return;
        }
        const detected = await findBodyText(page, telemostRemovalTexts);
        if (detected) {
          await triggerRemoval(`Removal detected via text: "${detected}"`);
          return;
        }

        const leaveVisible = await isLeaveVisible(page);
        if (!leaveVisible) {
          consecutiveLeaveMisses++;
          if (consecutiveLeaveMisses >= LEAVE_MISS_THRESHOLD) {
            let post: string | null = null;
            for (const sel of telemostPostMeetingIndicators) {
              for (const frame of [callFrame(page), page.mainFrame()]) {
                if (await frame.locator(sel).first().isVisible({ timeout: 300 }).catch(() => false)) { post = sel; break; }
              }
              if (post) break;
            }
            post = post ?? await findBodyText(page, telemostPostMeetingTexts);
            if (post) {
              await triggerRemoval(`Leave control gone ${consecutiveLeaveMisses}x and post-meeting screen shown (${post})`);
              return;
            }
          }
        } else {
          if (consecutiveLeaveMisses > 0) {
            log(`[Telemost] Leave control recovered after ${consecutiveLeaveMisses} miss(es)`);
          }
          consecutiveLeaveMisses = 0;
        }
      }
    } catch {
      await triggerRemoval("Exception in removal poll — page likely navigated away");
      return;
    }

    if (!stopped) setTimeout(poll, 3000);
  };

  setTimeout(poll, 3000);

  return () => {
    stopped = true;
    page.off("framenavigated", onNavigated);
    log("[Telemost] Removal monitor stopped");
  };
}
