/**
 * ws-render.spec.ts — the L4 gate.
 *
 * Loads the fixture page (ws-render.html) in a REAL chromium and asserts the REAL DOM rendered what the
 * @vexa/dash-ws brick delivered: #status shows the golden meeting status ("active"), and #transcript
 * contains the golden confirmed lines. The fixture runs the brick over a FakeWsTransport and injects
 * the goldens itself — so a green here means "the brick's output is renderable by a human's browser",
 * not "a node fake parsed JSON". This is the instrument that replaces node-ws/curl false-greens:
 * green-in-Playwright ⇒ green-for-human.
 *
 * Later this same spec runs against the real stack — swap the fixture's FakeWsTransport for a real
 * WebSocket-backed transport and inject the goldens via redis; the DOM assertions below are unchanged.
 */
import { test, expect } from "@playwright/test";
import { GOLDEN_LINES, GOLDEN_STATUS_TEXT } from "./fixtures/golden.js";

test("a real browser renders the status + transcript dash-ws delivers", async ({
  page,
}) => {
  // surface any in-page exception as a test failure instead of a silent blank page
  const pageErrors: string[] = [];
  page.on("pageerror", (e) => pageErrors.push(String(e)));

  // served by the tiny static server (webServer in playwright.config.ts) at baseURL "/"
  await page.goto("/");

  // deterministic: the fixture appends #harness-ready once it has emitted the goldens.
  // state:"attached" — the marker is an empty div (zero-size → never "visible"); presence is the signal.
  await page.waitForSelector("#harness-ready", {
    state: "attached",
    timeout: 10_000,
  });

  // if the wiring threw inside the page, the fixture surfaces it here — fail loudly
  const harnessError = await page.locator("#harness-error").count();
  if (harnessError > 0) {
    const detail = await page.locator("#harness-error").textContent();
    throw new Error("fixture wiring failed in-page:\n" + detail);
  }
  expect(pageErrors, pageErrors.join("\n")).toHaveLength(0);

  // ── the actual L4 assertion: the REAL DOM shows what the brick delivered ──────────────────────────
  await expect(page.locator("#status")).toHaveText(GOLDEN_STATUS_TEXT);

  const lines = page.locator("#transcript .line");
  await expect(lines).toHaveCount(GOLDEN_LINES.length);
  for (let i = 0; i < GOLDEN_LINES.length; i++) {
    await expect(lines.nth(i)).toHaveText(GOLDEN_LINES[i]);
  }

  // and the golden text is actually present in the rendered transcript container
  const transcriptText = await page.locator("#transcript").textContent();
  for (const line of GOLDEN_LINES) {
    expect(transcriptText ?? "").toContain(line);
  }
});
