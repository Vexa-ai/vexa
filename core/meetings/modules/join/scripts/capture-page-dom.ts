/**
 * capture-page-dom.ts — capture a REAL platform page as a checked-in fixture.
 *
 * Runs inside the hot debug container (same Xvfb + humanized X11 + stealth browser
 * as the join harness), so what it captures is what the BOT sees, not what a
 * developer's desktop Chrome sees. Its whole reason to exist is #857: every DOM
 * fixture in this module is fabricated, and a fabricated fixture cannot prove a
 * detector fires on the page production actually renders.
 *
 *   docker run --rm -v $PWD/src:/pkg/src -v $PWD/scripts:/pkg/scripts \
 *     -v $PWD/capture:/pkg/capture \
 *     -e CAPTURE_URL="https://meet.google.com/aaa-bbbb-ccc" \
 *     -e CAPTURE_NAME=gmeet-404-meeting-not-found \
 *     --entrypoint bash meet-join-debug scripts/capture-entrypoint.sh
 *
 * Writes to /pkg/capture/<name>.{html,json,png}: the rendered outerHTML, a
 * metadata sidecar (url, final url, title, html.lang, console lines, response
 * status), and a screenshot. NEVER captures cookies, storage or headers.
 */
import { chromium } from "playwright-extra";
import StealthPlugin from "puppeteer-extra-plugin-stealth";
import { getJoinBrowserArgs } from "../src/index";
import * as fs from "fs";
import * as path from "path";

const url = process.env.CAPTURE_URL || process.argv[2];
const name = process.env.CAPTURE_NAME || "capture";
const settleMs = Number(process.env.CAPTURE_SETTLE_MS || 15000);
const outDir = process.env.CAPTURE_DIR || "/pkg/capture";

if (!url) {
  console.error("Usage: CAPTURE_URL=<url> CAPTURE_NAME=<slug> tsx scripts/capture-page-dom.ts");
  process.exit(1);
}

(async () => {
  const stealth = StealthPlugin();
  stealth.enabledEvasions.delete("iframe.contentWindow");
  stealth.enabledEvasions.delete("media.codecs");
  stealth.enabledEvasions.delete("user-agent-override");
  chromium.use(stealth);

  const browser = await chromium.launch({ headless: false, args: getJoinBrowserArgs() });
  const context = await browser.newContext({ permissions: ["camera", "microphone"], viewport: null });
  const page = await context.newPage();

  const consoleLines: string[] = [];
  page.on("console", (m) => consoleLines.push(`[${m.type()}] ${m.text()}`));
  page.on("pageerror", (e) => consoleLines.push(`[pageerror] ${String(e)}`));

  const resp = await page.goto(url, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(settleMs);

  fs.mkdirSync(outDir, { recursive: true });
  const html = await page.evaluate(() => document.documentElement.outerHTML);
  const meta = await page.evaluate(() => ({
    title: document.title,
    lang: document.documentElement.getAttribute("lang") || "",
    navigatorLanguage: navigator.language || "",
    bodyText: (document.body?.innerText || "").replace(/\s+/g, " ").trim().slice(0, 4000),
    buttonLabels: Array.from(document.querySelectorAll("button")).map(
      (b) => (b.textContent || "").replace(/\s+/g, " ").trim(),
    ),
  }));

  fs.writeFileSync(path.join(outDir, `${name}.html`), html, "utf8");
  fs.writeFileSync(
    path.join(outDir, `${name}.json`),
    JSON.stringify(
      {
        requestedUrl: url,
        finalUrl: page.url(),
        httpStatus: resp ? resp.status() : null,
        capturedAt: new Date().toISOString(),
        settleMs,
        ...meta,
        console: consoleLines,
      },
      null,
      2,
    ),
    "utf8",
  );
  try { await page.screenshot({ path: path.join(outDir, `${name}.png`), fullPage: true }); } catch { /* best-effort */ }

  console.log(`=== CAPTURED ${name}: status=${resp ? resp.status() : "?"} finalUrl=${page.url()} htmlBytes=${html.length} consoleLines=${consoleLines.length} ===`);
  console.log(`--- body text ---\n${meta.bodyText.slice(0, 800)}`);
  console.log(`--- buttons --- ${JSON.stringify(meta.buttonLabels)}`);
  await browser.close();
})();
