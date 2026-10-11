#!/usr/bin/env node
/**
 * overlay-hit — proves in a real browser that a click on an overlay's controls reaches them
 * (terminal design guidelines §3.5 and §4.11). jsdom has no hit-testing, so this cannot be a unit
 * test: a stacking defect only shows where the browser decides what is under the pointer.
 *
 *   node scripts/overlay-hit.mjs <catalogue-url>      e.g. http://localhost:3123/design
 *
 * The 2026-10-11 defect it guards: inside `.vx-dialog-layer` the scrim kept the shared `.vx-scrim`
 * z-index (the drawer stratum) while `.vx-dialog` had none, so the scrim painted over the dialog and
 * every click on a ConfirmDialog button landed on the scrim, which cancels. Delete chat, workspace
 * delete and planned-meeting delete all silently did nothing.
 *
 * Per theme it opens the catalogue's Dialog and ConfirmDialog and asserts that the element under the
 * centre of every button in the dialog IS that button, and that a real mouse click on the confirm
 * reaches its handler; then opens the Drawer and asserts its rows are hit and the area beside it
 * hits the drawer's scrim (the drawer stratum is unchanged).
 *
 * KEYBOARD (the second 2026-10-11 defect): the dialog re-ran its focus setup on every parent render,
 * because callers pass `onClose` as an inline arrow, so the rail's sessions poll moved focus back to
 * the first button (Cancel) and Enter cancelled a delete. The catalogue's "Delete chat…" confirm
 * (`focusConfirm`) re-renders its parent every 250 ms while open; this opens it with Enter, asserts
 * focus starts on "Delete chat" and stays on whichever button the reader moved to across several of
 * those renders, that Enter on "Delete chat" reaches it, and that Escape cancels and returns focus
 * to the opener. Needs Playwright, which is not a
 * terminal dependency — run it where Playwright is installed (CI's terminal-layout job, bbb).
 * Exit 1 on any miss.
 */

/** Runs IN THE PAGE: for each selector match, what element sits under its centre. */
const HITS = (sel) => [...document.querySelectorAll(sel)].map((el) => {
  const r = el.getBoundingClientRect();
  const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
  const name = (n) => n ? `${n.tagName.toLowerCase()}.${String(n.className).split(" ").slice(0, 2).join(".")}` : "null";
  return { label: (el.textContent || "").trim(), hit: !!top && (top === el || el.contains(top)), top: name(top) };
});

async function main() {
  const url = process.argv[2];
  if (!url) { console.error("usage: overlay-hit.mjs <catalogue-url>"); process.exit(2); }
  let chromium;
  try { ({ chromium } = await import("playwright")); }
  catch { console.error("Playwright is not installed here. Run this where it is (set NODE_PATH)."); process.exit(2); }
  const browser = await chromium.launch();
  let failed = false;
  const check = (ok, msg) => { failed ||= !ok; console.log(`${ok ? "✓" : "✗"} ${msg}`); };
  for (const theme of ["dark", "light"]) {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: theme, reducedMotion: "reduce" });   // no slide: geometry is final at once
    const page = await ctx.newPage();
    await page.goto(url, { waitUntil: "networkidle" });
    const dialogSection = page.locator("[data-gallery=dialog]");
    await dialogSection.waitFor();

    for (const [opener, closeLabel] of [["Open a dialog", "Rename chat"], ["Delete workspace…", "Delete workspace"], ["Delete chat…", "Delete chat"]]) {
      // Openers are clicked without Playwright's "stable" wait: a headless browser on a loaded host
      // may render no frames at all (see layout-sweep.mjs), and the opener is not under test.
      await dialogSection.first().evaluate((el) => el.scrollIntoView({ block: "center" }));
      await dialogSection.getByRole("button", { name: opener, exact: true }).first().dispatchEvent("click");
      const dialog = page.getByRole("dialog");
      await dialog.waitFor();
      // Enable a type-to-confirm button so it can be clicked.
      const typed = dialog.locator("input[data-autofocus]");
      if (await typed.count()) {
        const name = await dialog.locator(".vx-field-label strong").textContent();
        await typed.fill(name ?? "", { force: true });
      }
      for (const h of await page.evaluate(HITS, ".vx-dialog-layer [role=dialog] button")) check(h.hit, `${theme} "${opener}": "${h.label}" is under the pointer (top: ${h.top})`);
      // A real pointer click on the act: it closes the dialog only if it reaches the button. A click
      // swallowed by the scrim closes it too (as Cancel), so the handler is told apart by a probe.
      const btn = dialog.getByRole("button", { name: closeLabel, exact: true });
      await btn.evaluate((el) => el.addEventListener("click", () => { window.__vxHit = el.textContent; }, { once: true }));
      const box = await btn.boundingBox();
      await page.evaluate(() => { window.__vxHit = null; });
      await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
      const hit = await page.evaluate(() => window.__vxHit);
      check(hit === closeLabel, `${theme} "${opener}": a mouse click on "${closeLabel}" reaches it (got ${JSON.stringify(hit)})`);
      if (await dialog.count()) await page.keyboard.press("Escape");
    }

    // Keyboard: focus is set once, on open, and survives the parent re-rendering under it.
    {
      const focused = () => page.evaluate(() => (document.activeElement?.textContent || "").trim());
      const opener = dialogSection.getByRole("button", { name: "Delete chat…", exact: true }).first();
      await dialogSection.first().evaluate((el) => el.scrollIntoView({ block: "center" }));
      await opener.focus();
      await page.keyboard.press("Enter");
      const dialog = page.getByRole("dialog", { name: "Delete this chat?" });
      await dialog.waitFor();
      check(await focused() === "Delete chat", `${theme} keyboard: Enter on "Delete chat…" opens the confirm on "Delete chat" (focused: ${JSON.stringify(await focused())})`);
      await page.waitForTimeout(800);   // ≥3 parent renders, each with a new onCancel
      check(await focused() === "Delete chat", `${theme} keyboard: focus stays on "Delete chat" across parent renders (focused: ${JSON.stringify(await focused())})`);
      await page.keyboard.press("Tab");
      await page.waitForTimeout(800);
      check(await focused() === "Cancel", `${theme} keyboard: after Tab, focus stays on "Cancel" across parent renders (focused: ${JSON.stringify(await focused())})`);
      await page.keyboard.press("Shift+Tab");
      await page.waitForTimeout(800);
      const act = dialog.getByRole("button", { name: "Delete chat", exact: true });
      await act.evaluate((el) => { window.__vxKey = null; el.addEventListener("click", () => { window.__vxKey = el.textContent; }, { once: true }); });
      await page.keyboard.press("Enter");
      const got = await page.evaluate(() => window.__vxKey);
      check(got === "Delete chat", `${theme} keyboard: Enter confirms — it reaches "Delete chat" (got ${JSON.stringify(got)})`);
      if (await dialog.count()) await page.keyboard.press("Escape");
      await opener.focus();
      await page.keyboard.press("Enter");
      await dialog.waitFor();
      await page.waitForTimeout(600);
      await page.keyboard.press("Escape");
      await dialog.waitFor({ state: "detached" });
      check(await focused() === "Delete chat…", `${theme} keyboard: Escape cancels and returns focus to the opener (focused: ${JSON.stringify(await focused())})`);
    }

    // The drawer stratum is unchanged: rows hit, the area beside the drawer hits its scrim.
    const sheetSection = page.locator("[data-gallery=sheet]");
    await sheetSection.first().evaluate((el) => el.scrollIntoView({ block: "center" }));
    await sheetSection.getByRole("button", { name: "Open the drawer", exact: true }).first().dispatchEvent("click");
    await page.waitForTimeout(300);
    const drawer = await page.evaluate(() => {
      const sheet = [...document.querySelectorAll("[data-gallery=sheet] [data-pane=rail-drawer]")].find((s) => s.getBoundingClientRect().width > 0 && getComputedStyle(s).visibility !== "hidden");
      if (!sheet) return null;
      const row = sheet.querySelector(".vx-cat-pad > *");
      const rr = row.getBoundingClientRect();
      const s = sheet.getBoundingClientRect();
      const rowTop = document.elementFromPoint(rr.left + rr.width / 2, rr.top + rr.height / 2);
      const beside = document.elementFromPoint(s.right + 30, s.top + s.height / 2);
      const scrim = sheet.parentElement.querySelector(".vx-scrim") ?? document.querySelector("[data-gallery=sheet] .vx-scrim");
      return { rowTop: rowTop ? rowTop.className : null, rowHit: !!rowTop && row.contains(rowTop), besideScrim: !!beside && beside.classList.contains("vx-scrim"), scrimZ: scrim ? getComputedStyle(scrim).zIndex : null };
    });
    check(!!drawer && drawer.rowHit, `${theme} drawer: its rows are under the pointer (top: ${drawer?.rowTop})`);
    check(!!drawer && drawer.besideScrim, `${theme} drawer: the area beside it is its scrim`);
    check(!!drawer && drawer.scrimZ === "29", `${theme} drawer: its scrim keeps the drawer stratum (z-index ${drawer?.scrimZ})`);
    await ctx.close();
  }
  await browser.close();
  process.exit(failed ? 1 : 0);
}

if (import.meta.url === `file://${process.argv[1]}`) main();
