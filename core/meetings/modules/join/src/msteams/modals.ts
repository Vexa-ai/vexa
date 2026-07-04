import { Page } from "playwright";
import { log } from "../_host";
import { teamsContinueWithoutMediaSelectors } from "./selectors";

/**
 * Dismiss the Teams "Are you sure you don't want audio or video?" confirmation
 * modal. Ported from Vexa-ai/vexa#467 (@lucasantoro97).
 *
 * Teams' anonymous "light meeting" flow renders this modal when "Join now" is
 * clicked while camera + mic are off. CRITICAL: in this flow the modal appears
 * AFTER the Join-now click, not before — so the pre-join readiness handler in
 * join.ts (which clicks the same button earlier) never sees it.
 *
 * Left undismissed it BLOCKS the join indefinitely, and worse: the underlying
 * pre-join "Join now" button stays in the DOM behind the modal. The admission
 * detector counts a visible "Join now" as a waiting-room indicator
 * (selectors.ts teamsWaitingRoomIndicators), so the bot loops
 * "Still in Teams waiting room…" forever instead of clicking through.
 *
 * This helper is the single point that clears it. Call it both right after the
 * Join-now click (join.ts) and inside the admission wait loops (admission.ts)
 * so the modal is handled no matter when Teams decides to show it.
 *
 * Returns true if the modal was found and a dismiss click was issued.
 */
export async function dismissTeamsAvConfirmModal(page: Page): Promise<boolean> {
  const selector = teamsContinueWithoutMediaSelectors.join(", ");
  try {
    const btn = page.locator(selector).first();
    if (await btn.isVisible().catch(() => false)) {
      await btn.click({ timeout: 5000 });
      log('✅ Dismissed "Continue without audio or video" confirmation modal');
      await page.waitForTimeout(500);
      return true;
    }
  } catch (err: any) {
    log(`ℹ️ Could not dismiss AV-confirm modal: ${err?.message || err}`);
  }
  return false;
}

/**
 * True if the Teams AV-confirm modal is currently on screen.
 */
export async function isTeamsAvConfirmModalVisible(page: Page): Promise<boolean> {
  const selector = teamsContinueWithoutMediaSelectors.join(", ");
  return await page.locator(selector).first().isVisible().catch(() => false);
}
