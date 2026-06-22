/**
 * chat-render.spec.ts — the L4 gate for @vexa/dash-chat.
 *
 * Loads the fixture page (chat-render.html) in a REAL chromium and asserts the REAL DOM that the
 * @vexa/dash-chat <ChatPanel> rendered over the 2 golden messages: one bubble per message, each showing
 * its sender + text, with the bot bubble flagged. A green here means "a human's browser renders the
 * messages this component is handed" — not "a node fake parsed JSON".
 *
 * Later this same spec mounts the component over messages injected by a real ws transport instead of the
 * golden array; the DOM assertions below are unchanged.
 */
import { test, expect } from "@playwright/test";
import { GOLDEN_MESSAGES, GOLDEN_SENDERS, GOLDEN_TEXTS } from "./golden.js";

test("a real browser renders both chat messages with senders + text", async ({ page }) => {
  // surface any in-page exception as a test failure instead of a silent blank page
  const pageErrors: string[] = [];
  page.on("pageerror", (e) => pageErrors.push(String(e)));

  // served by the tiny static server (webServer in playwright.config.ts) at baseURL "/"
  await page.goto("/");

  // deterministic: the fixture entry appends #harness-ready once the mount completed.
  await page.waitForSelector("#harness-ready", { state: "attached", timeout: 10_000 });

  // if the mount threw inside the page, the fixture surfaces it here — fail loudly
  const harnessError = await page.locator("#harness-error").count();
  if (harnessError > 0) {
    const detail = await page.locator("#harness-error").textContent();
    throw new Error("fixture mount failed in-page:\n" + detail);
  }
  expect(pageErrors, pageErrors.join("\n")).toHaveLength(0);

  // ── the actual L4 assertion: the REAL DOM shows both messages ────────────────────────────────────
  const bubbles = page.locator(".dash-chat-bubble");
  await expect(bubbles).toHaveCount(GOLDEN_MESSAGES.length);

  // each bubble renders its sender AND its text, in order
  for (let i = 0; i < GOLDEN_MESSAGES.length; i++) {
    await expect(bubbles.nth(i).locator(".dash-chat-sender")).toHaveText(GOLDEN_SENDERS[i]);
    await expect(bubbles.nth(i).locator(".dash-chat-text")).toHaveText(GOLDEN_TEXTS[i]);
  }

  // the senders are actually present in the rendered panel
  const panelText = (await page.locator(".dash-chat").textContent()) ?? "";
  for (const sender of GOLDEN_SENDERS) {
    expect(panelText).toContain(sender);
  }
  for (const body of GOLDEN_TEXTS) {
    expect(panelText).toContain(body);
  }

  // the bot message is flagged via data-from-bot, the human one is not
  await expect(bubbles.nth(0)).toHaveAttribute("data-from-bot", "false");
  await expect(bubbles.nth(1)).toHaveAttribute("data-from-bot", "true");

  // a time is rendered for each message (the goldens carry timestamps)
  for (let i = 0; i < GOLDEN_MESSAGES.length; i++) {
    await expect(bubbles.nth(i).locator(".dash-chat-time")).toHaveCount(1);
  }
});
