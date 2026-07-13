import { Page } from "playwright";
import { log } from "../_host";
import { BotConfig } from "../_host";
import { jitsiPasswordInputSelector } from "./selectors";

// Shared password-prompt fill, callable from BOTH the post-join check (join.ts)
// and the admission poll loop (admission.ts). The prompt arrives over the XMPP
// round-trip and may land seconds AFTER the join click — i.e. during the
// admission wait — so a single early check is not enough (TAKE finding #543:
// a bot holding the CORRECT passcode sat until lobby timeout when the dialog
// appeared late). Lives in its own module because join.ts already imports from
// admission.ts (a join.ts import from admission.ts would be a cycle).

// Pages whose password dialog we already filled+submitted — the fill is
// idempotent per page: if the dialog is still (or again) visible on a later
// poll, we do NOT resubmit the same passcode (a reappearing dialog after a
// submit means the passcode was wrong; looping the submit would spam the
// deployment and mask the failure).
const submittedPages = new WeakSet<object>();

export type PasswordFillResult = "absent" | "submitted" | "already-submitted";

/**
 * Fill + submit the room-password dialog IF it is currently present.
 * Instantaneous presence check (no long wait) — safe to call on every
 * admission-poll iteration. Throws the structured `password_required` error
 * when the dialog is up but no passcode was supplied (fail fast: the dialog
 * never self-dismisses).
 */
export async function fillPasswordPromptIfPresent(
  page: Page,
  botConfig: BotConfig,
): Promise<PasswordFillResult> {
  const pwField = page.locator(jitsiPasswordInputSelector).first();
  const visible = await pwField.isVisible({ timeout: 300 }).catch(() => false);
  if (!visible) return "absent";
  if (submittedPages.has(page as unknown as object)) return "already-submitted";

  const passcode = botConfig.passcode || "";
  if (!passcode) {
    throw new Error(
      "[Jitsi] password_required: room is password-protected but botConfig.passcode is empty; " +
      "pass the room password to the embedder",
    );
  }
  await pwField.fill(passcode);
  // Submit: Enter is the universal dialog confirm across jitsi versions.
  await page.keyboard.press("Enter");
  submittedPages.add(page as unknown as object);
  log("[Jitsi] Submitted room password");
  return "submitted";
}
