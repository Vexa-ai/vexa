/**
 * render.spec.ts — the L4 gate for @vexa/dash-transcript-viewer.
 *
 * Loads the fixture page (render.html) in a REAL chromium and asserts the REAL DOM the component
 * rendered: both golden speakers ("Anna", "Zoya") and both golden texts are visible, the live indicator
 * shows because isLive=true, and one segment row exists per golden segment. The fixture mounts the
 * REAL component over the goldens — so a green here means "the component's output is renderable by a
 * human's browser", not "a node test asserted on a virtual DOM". green-in-Playwright ⇒ green-for-human.
 */
import { test, expect } from "@playwright/test";
import { GOLDEN_SPEAKERS, GOLDEN_TEXTS } from "./fixtures/golden.js";

test("a real browser renders both speakers + texts the component paints", async ({
  page,
}) => {
  // surface any in-page exception as a test failure instead of a silent blank page
  const pageErrors: string[] = [];
  page.on("pageerror", (e) => pageErrors.push(String(e)));

  // served by the tiny static server (webServer in playwright.config.ts) at baseURL "/"
  await page.goto("/");

  // deterministic: the fixture appends #harness-ready once React has rendered.
  await page.waitForSelector("#harness-ready", {
    state: "attached",
    timeout: 10_000,
  });

  // if mounting threw inside the page, the fixture surfaces it here — fail loudly
  const harnessError = await page.locator("#harness-error").count();
  if (harnessError > 0) {
    const detail = await page.locator("#harness-error").textContent();
    throw new Error("fixture mounting failed in-page:\n" + detail);
  }
  expect(pageErrors, pageErrors.join("\n")).toHaveLength(0);

  // ── the actual L4 assertion: the REAL DOM shows what the component painted ─────────────────────────

  // one segment row per golden segment
  const segments = page.locator('[data-testid="transcript-segment"]');
  await expect(segments).toHaveCount(GOLDEN_SPEAKERS.length);

  // both speakers are rendered, in order
  const speakers = page.locator('[data-testid="segment-speaker"]');
  await expect(speakers).toHaveCount(GOLDEN_SPEAKERS.length);
  for (let i = 0; i < GOLDEN_SPEAKERS.length; i++) {
    await expect(speakers.nth(i)).toHaveText(GOLDEN_SPEAKERS[i]);
  }

  // both texts are rendered, in order
  const texts = page.locator('[data-testid="segment-text"]');
  await expect(texts).toHaveCount(GOLDEN_TEXTS.length);
  for (let i = 0; i < GOLDEN_TEXTS.length; i++) {
    await expect(texts.nth(i)).toHaveText(GOLDEN_TEXTS[i]);
  }

  // and the speakers + texts are actually present in the body container text
  const bodyText = (await page.locator('[data-testid="transcript-body"]').textContent()) ?? "";
  for (const name of GOLDEN_SPEAKERS) expect(bodyText).toContain(name);
  for (const txt of GOLDEN_TEXTS) expect(bodyText).toContain(txt);

  // the live indicator is shown because the fixture passes isLive=true
  await expect(page.locator('[data-testid="live-indicator"]')).toBeVisible();
});

test("the search box filters the rendered segments", async ({ page }) => {
  await page.goto("/");
  await page.waitForSelector("#harness-ready", { state: "attached", timeout: 10_000 });

  // typing one speaker's name narrows to a single segment row — proves search is wired to the DOM
  await page.locator('[data-testid="transcript-search"]').fill("Zoya");
  const segments = page.locator('[data-testid="transcript-segment"]');
  await expect(segments).toHaveCount(1);
  await expect(page.locator('[data-testid="segment-text"]')).toHaveText(GOLDEN_TEXTS[1]);

  // clearing the box restores both rows
  await page.locator('[data-testid="transcript-search"]').fill("");
  await expect(segments).toHaveCount(GOLDEN_SPEAKERS.length);
});
