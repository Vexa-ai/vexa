/**
 * list-render.spec.ts — the L4 gate for @vexa/dash-meetings-list.
 *
 * Loads the fixture page (list-render.html) in a REAL chromium and asserts the REAL DOM that the
 * MeetingsList component rendered over the two golden meetings: both rows present, each carrying its
 * raw status + the rendered status label + duration + native id; then clicks the active row and proves
 * the injected `onOpen` fired with that meeting (read off window.__lastOpenedId / window.__opened).
 *
 * A green here means "the brick renders + is interactive in a human's browser" — not "a node fake
 * parsed props". props in (golden MeetingResponse[]) → DOM out, exactly the brick's contract.
 */
import { test, expect } from "@playwright/test";
import { EXPECTED_ROWS, GOLDEN_MEETINGS } from "./fixtures/golden.js";

test("a real browser renders both meeting rows and click → onOpen", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (e) => pageErrors.push(String(e)));

  await page.goto("/");

  await page.waitForSelector("#harness-ready", { state: "attached", timeout: 10_000 });

  // if mounting threw in-page, surface it loudly
  const harnessError = await page.locator("#harness-error").count();
  if (harnessError > 0) {
    const detail = await page.locator("#harness-error").textContent();
    throw new Error("fixture wiring failed in-page:\n" + detail);
  }
  expect(pageErrors, pageErrors.join("\n")).toHaveLength(0);

  // ── the list mounted ──────────────────────────────────────────────────────────────────────────
  await expect(page.locator('[data-testid="meetings-list"]')).toBeVisible();

  const rows = page.locator('[data-testid="meeting-row"]');
  await expect(rows).toHaveCount(GOLDEN_MEETINGS.length);

  // ── each golden row rendered with its status, label, duration, native id ────────────────────────
  for (let i = 0; i < EXPECTED_ROWS.length; i++) {
    const exp = EXPECTED_ROWS[i];
    const row = rows.nth(i);

    await expect(row).toHaveAttribute("data-meeting-id", exp.id);
    await expect(row).toHaveAttribute("data-status", exp.status);

    const statusCell = row.locator('[data-testid="meeting-status"]');
    await expect(statusCell).toHaveAttribute("data-status", exp.status);
    await expect(statusCell).toContainText(exp.statusLabel);

    await expect(row.locator('[data-testid="meeting-duration"]')).toHaveText(exp.duration);
    await expect(row.locator('[data-testid="meeting-native-id"]')).toContainText(exp.nativeId);
  }

  // explicit: the active and the completed rows both surface their (distinct) statuses
  const statuses = await page.locator('[data-testid="meeting-status"]').allInnerTexts();
  expect(statuses.join("|")).toContain("Active");
  expect(statuses.join("|")).toContain("Completed");

  // ── click the active row → injected onOpen fired with THAT meeting ──────────────────────────────
  const activeRow = page.locator('[data-testid="meeting-row"][data-status="active"]');
  await expect(activeRow).toHaveCount(1);
  await activeRow.click();

  await expect
    .poll(async () => page.evaluate(() => (window as any).__lastOpenedId))
    .toBe(EXPECTED_ROWS[0].id);

  const openedCount = await page.evaluate(() => ((window as any).__opened ?? []).length);
  expect(openedCount).toBe(1);

  const openedId = await page.evaluate(() => ((window as any).__opened ?? [])[0]?.id);
  expect(String(openedId)).toBe(EXPECTED_ROWS[0].id);
});
