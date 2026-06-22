/**
 * join-form.spec.ts — the L4 gate for the JoinForm VIEW brick.
 *
 * Loads the fixture page (join-form.html) in a REAL chromium, which has mounted the REAL JoinForm
 * component with golden props. The spec then acts as a human would: it fills the meeting URL input with
 * GOLDEN_URL and clicks "Start bot". The assertion is that the component fired `onSubmit` with the
 * PARSED CreateBotRequest — platform "google_meet" + native id "abc-defg-hij" + the pre-filled bot name
 * — recorded onto window.__submitted. A green here means "a real browser ran the real component, the
 * real parser turned the pasted URL into (platform, native id), and the real submit handler emitted the
 * right request" — not "a node fake parsed a string".
 *
 * Second test: invalid input must NOT fire onSubmit (the form blocks and shows an inline error).
 */
import { test, expect } from "@playwright/test";
import { GOLDEN_URL, GOLDEN_REQUEST } from "./fixtures/golden.js";

test("filling the URL + submitting fires onSubmit with the parsed platform + native id", async ({
  page,
}) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (e) => pageErrors.push(String(e)));

  await page.goto("/");

  // deterministic: form-entry.tsx appends #harness-ready once the component mounts.
  await page.waitForSelector("#harness-ready", { state: "attached", timeout: 10_000 });
  expect(pageErrors, pageErrors.join("\n")).toHaveLength(0);

  // the real component must have rendered its inputs + submit button
  await expect(page.locator("#join-meeting")).toBeVisible();
  await expect(page.locator(".join-form__submit")).toBeVisible();

  // ── act as a human: paste the meeting URL, then submit ──────────────────────────────────────────
  await page.fill("#join-meeting", GOLDEN_URL);
  await page.click(".join-form__submit");

  // ── the actual L4 assertion: onSubmit fired with the parsed request ─────────────────────────────
  await expect
    .poll(() => page.evaluate(() => window.__submitted?.length ?? 0))
    .toBe(1);

  const submitted = await page.evaluate(() => window.__submitted[0]);
  expect(submitted).toEqual(GOLDEN_REQUEST);

  // and specifically: the platform + native id were PARSED out of the URL, not typed separately
  expect(submitted.platform).toBe("google_meet");
  expect(submitted.native_meeting_id).toBe("abc-defg-hij");

  expect(pageErrors, pageErrors.join("\n")).toHaveLength(0);
});

test("invalid meeting input does NOT fire onSubmit and shows an inline error", async ({ page }) => {
  await page.goto("/");
  await page.waitForSelector("#harness-ready", { state: "attached", timeout: 10_000 });

  await page.fill("#join-meeting", "not a meeting");
  await page.click(".join-form__submit");

  // the form blocked: onSubmit never fired, and the hint became an alert
  await expect(page.locator("#join-meeting-hint")).toHaveText("Enter a valid meeting URL or ID");
  const count = await page.evaluate(() => window.__submitted?.length ?? 0);
  expect(count).toBe(0);
});
