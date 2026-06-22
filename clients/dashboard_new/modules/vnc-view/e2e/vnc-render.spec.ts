/**
 * vnc-render.spec.ts — the L4 gate for @vexa/dash-vnc-view.
 *
 * Loads the two fixture pages in a REAL chromium and asserts the REAL DOM the bundled <VncView>
 * rendered:
 *   1. golden vncUrl → an <iframe> whose src is exactly the golden per-bot noVNC URL renders
 *      (and the placeholder does NOT).
 *   2. empty vncUrl  → the loading placeholder renders with its text (and NO iframe).
 *
 * The fixtures mount the REAL component (esbuilt from src) and inject the goldens — a green here means
 * "the component's output is renderable by a human's browser", not "a node fake stringified props".
 * green-in-Playwright ⇒ green-for-human.
 */
import { test, expect } from "@playwright/test";
import { GOLDEN_VNC_URL, GOLDEN_PLACEHOLDER_TEXT } from "./golden.js";

async function gotoFixture(page: import("@playwright/test").Page, path: string) {
  const pageErrors: string[] = [];
  page.on("pageerror", (e) => pageErrors.push(String(e)));

  await page.goto(path);

  // deterministic: each fixture appends #harness-ready once the component has mounted.
  await page.waitForSelector("#harness-ready", { state: "attached", timeout: 10_000 });

  // if mounting threw in-page, surface it as a loud failure instead of a silent blank page.
  const harnessError = await page.locator("#harness-error").count();
  if (harnessError > 0) {
    const detail = await page.locator("#harness-error").textContent();
    throw new Error("fixture wiring failed in-page:\n" + detail);
  }
  expect(pageErrors, pageErrors.join("\n")).toHaveLength(0);
}

test("golden vncUrl → a real browser renders the noVNC <iframe> with that src", async ({ page }) => {
  await gotoFixture(page, "/vnc-url.html");

  const iframe = page.locator('[data-testid="vnc-iframe"]');
  await expect(iframe).toHaveCount(1);
  await expect(iframe).toHaveAttribute("src", GOLDEN_VNC_URL);
  // mirrors the vendored viewer so noVNC clipboard sync works
  await expect(iframe).toHaveAttribute("allow", "clipboard-read; clipboard-write");

  // the non-empty case must NOT show the placeholder
  await expect(page.locator('[data-testid="vnc-placeholder"]')).toHaveCount(0);
});

test("empty vncUrl → a real browser renders the loading placeholder, no iframe", async ({ page }) => {
  await gotoFixture(page, "/vnc-empty.html");

  const placeholder = page.locator('[data-testid="vnc-placeholder"]');
  await expect(placeholder).toHaveCount(1);
  await expect(page.locator('[data-testid="vnc-placeholder-text"]')).toHaveText(
    GOLDEN_PLACEHOLDER_TEXT,
  );

  // the empty case must NOT mount an iframe
  await expect(page.locator('[data-testid="vnc-iframe"]')).toHaveCount(0);
});
