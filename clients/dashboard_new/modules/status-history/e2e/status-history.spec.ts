/**
 * status-history.spec.ts — the L4 gate.
 *
 * Loads the fixture page (render.html) in a REAL chromium and asserts the REAL DOM the
 * @vexa/dash-status-history component rendered from golden props: a joining → active → completed
 * timeline, in order, with the newest row marked current. The fixture mounts the REAL component via
 * react-dom over the goldens — so a green here means "the component's output is renderable by a
 * human's browser", not "a node assertion parsed props".
 *
 * The goldens are deliberately supplied out of timestamp order; the in-order DOM below also proves the
 * component sorts oldest → newest before rendering.
 */
import { test, expect } from "@playwright/test";
import {
  GOLDEN_TRANSITIONS,
  GOLDEN_STATUS_LABELS,
  GOLDEN_TO_ORDER,
} from "./fixtures/golden.js";

test("a real browser renders the joining → active → completed status timeline in order", async ({
  page,
}) => {
  // surface any in-page exception as a test failure instead of a silent blank page
  const pageErrors: string[] = [];
  page.on("pageerror", (e) => pageErrors.push(String(e)));

  await page.goto("/");

  // deterministic: the fixture appends #harness-ready once the component has mounted.
  await page.waitForSelector("#harness-ready", { state: "attached", timeout: 10_000 });

  // if mounting threw inside the page, the fixture surfaces it here — fail loudly
  const harnessError = await page.locator("#harness-error").count();
  if (harnessError > 0) {
    const detail = await page.locator("#harness-error").textContent();
    throw new Error("fixture wiring failed in-page:\n" + detail);
  }
  expect(pageErrors, pageErrors.join("\n")).toHaveLength(0);

  // ── the actual L4 assertion: the REAL DOM shows the timeline the component rendered ────────────────
  const rows = page.locator("ol.status-history > li.status-history__row");
  await expect(rows).toHaveCount(GOLDEN_TRANSITIONS.length);

  // all three statuses, in order (oldest → newest) — both the visible label and the data-status attr
  for (let i = 0; i < GOLDEN_STATUS_LABELS.length; i++) {
    await expect(rows.nth(i).locator(".status-history__status")).toHaveText(
      GOLDEN_STATUS_LABELS[i],
    );
    await expect(rows.nth(i)).toHaveAttribute("data-status", GOLDEN_TO_ORDER[i]);
    await expect(rows.nth(i)).toHaveAttribute("data-index", String(i));
  }

  // the newest row (completed) is marked current; the earlier rows are not
  await expect(rows.nth(2)).toHaveAttribute("data-current", "");
  await expect(rows.nth(0)).not.toHaveAttribute("data-current", "");
  await expect(rows.nth(1)).not.toHaveAttribute("data-current", "");

  // and each label is actually present in the rendered list text, in order
  const listText = (await page.locator("ol.status-history").textContent()) ?? "";
  let cursor = 0;
  for (const label of GOLDEN_STATUS_LABELS) {
    const at = listText.indexOf(label, cursor);
    expect(at, `"${label}" should appear after the previous status in document order`).toBeGreaterThanOrEqual(0);
    cursor = at + label.length;
  }

  // a completion reason that was injected on the final transition is rendered
  await expect(rows.nth(2).locator(".status-history__reason")).toHaveText("meeting ended");
});
