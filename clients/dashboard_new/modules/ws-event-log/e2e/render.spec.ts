/**
 * render.spec.ts — the L4 gate for @vexa/dash-ws-event-log.
 *
 * Loads the fixture page (mount.html) in a REAL chromium and asserts the REAL DOM that React rendered
 * from the brick's WsEventLog component over the golden events: one row per event, NEWEST FIRST, each
 * showing its type tag + summary. The fixture mounts the actual component source (bundled by esbuild) —
 * so a green here means "a human's browser renders the rows the brick produces", not "a node fake
 * stringified some props". This is the instrument that replaces jsdom/snapshot false-greens:
 * green-in-Playwright ⇒ green-for-human.
 */
import { test, expect } from "@playwright/test";
import { GOLDEN_EVENTS, GOLDEN_ROWS_RENDERED } from "./fixtures/golden.js";

test("a real browser renders the WS frame log newest-first", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (e) => pageErrors.push(String(e)));

  await page.goto("/");

  // deterministic: the fixture appends #harness-ready after React's render call returns
  await page.waitForSelector("#harness-ready", { state: "attached", timeout: 10_000 });

  // if mounting threw inside the page, the fixture surfaces it — fail loudly
  const harnessError = await page.locator("#harness-error").count();
  if (harnessError > 0) {
    const detail = await page.locator("#harness-error").textContent();
    throw new Error("fixture mount failed in-page:\n" + detail);
  }
  expect(pageErrors, pageErrors.join("\n")).toHaveLength(0);

  // ── the actual L4 assertion: the REAL DOM shows the rows the component rendered ──────────────────
  await expect(page.locator('[data-testid="ws-event-log"]')).toBeVisible();

  const rows = page.locator('[data-testid="ws-event-row"]');
  await expect(rows).toHaveCount(GOLDEN_EVENTS.length);

  // newest first: row 0 is the LAST golden event, etc. — assert type + summary + ts per row.
  for (let i = 0; i < GOLDEN_ROWS_RENDERED.length; i++) {
    const expected = GOLDEN_ROWS_RENDERED[i];
    const row = rows.nth(i);
    await expect(row.locator('[data-testid="ws-event-type"]')).toHaveText(expected.type);
    await expect(row.locator('[data-testid="ws-event-summary"]')).toHaveText(expected.summary);
    await expect(row.locator('[data-testid="ws-event-ts"]')).toHaveText(expected.ts);
  }

  // the footer count reflects the number of events
  await expect(page.locator('[data-testid="ws-event-count"]')).toHaveText(
    `${GOLDEN_EVENTS.length} events`,
  );

  // sanity: the meeting.status summary is present somewhere in the rendered log
  const logText = await page.locator('[data-testid="ws-event-log"]').textContent();
  expect(logText ?? "").toContain("status: active");
});

test("a real browser renders the empty state when no events are injected", async ({
  page,
}) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (e) => pageErrors.push(String(e)));

  // dedicated fixture page that mounts WsEventLog with events=[]
  await page.goto("/mount-empty.html");
  await page.waitForSelector("#harness-ready", { state: "attached", timeout: 10_000 });

  const harnessError = await page.locator("#harness-error").count();
  if (harnessError > 0) {
    const detail = await page.locator("#harness-error").textContent();
    throw new Error("fixture mount failed in-page:\n" + detail);
  }
  expect(pageErrors, pageErrors.join("\n")).toHaveLength(0);

  // the container renders, with the empty marker and zero rows
  await expect(page.locator('[data-testid="ws-event-log"]')).toBeVisible();
  await expect(page.locator('[data-testid="ws-event-empty"]')).toBeVisible();
  await expect(page.locator('[data-testid="ws-event-row"]')).toHaveCount(0);
  await expect(page.locator('[data-testid="ws-event-count"]')).toHaveText("0 events");
});
