/**
 * debug-join.ts — watch the isolated joining layer drive a real meeting.
 *
 *   npx tsx scripts/debug-join.ts "https://meet.google.com/xxx-xxxx-xxx"
 *
 * Linux (Docker/Xvfb): opens the noVNC view so a human sees it live.
 * macOS:               launches HEADED Chromium (real visible window).
 * Either way:          prints the CDP URL so an agent can connectOverCDP and
 *                      drive/inspect the SAME browser the human is watching.
 */
import { chromium } from "playwright-extra";
import StealthPlugin from "puppeteer-extra-plugin-stealth";
import { joinMeeting, startDebugView } from "../src/index";

const url = process.argv[2];
if (!url || !url.includes("meet.google.com")) {
  console.error("Usage: tsx scripts/debug-join.ts <google-meet-url>");
  process.exit(1);
}

(async () => {
  const stealth = StealthPlugin();
  stealth.enabledEvasions.delete("iframe.contentWindow");
  stealth.enabledEvasions.delete("media.codecs");
  stealth.enabledEvasions.delete("user-agent-override");
  chromium.use(stealth);

  const browser = await chromium.launch({
    headless: false, // visible window on macOS; renders to Xvfb :99 on Linux
    args: [
      "--no-sandbox",
      "--disable-blink-features=AutomationControlled",
      "--use-fake-ui-for-media-stream",
      "--use-file-for-fake-video-capture=/dev/null",
      "--remote-debugging-port=9222", // CDP for the agent to attach
    ],
  });
  const context = await browser.newContext({ permissions: ["camera", "microphone"], viewport: null });
  const page = await context.newPage();

  // join.ts hardcodes /app/storage/screenshots (Docker). Redirect locally.
  const dir = process.cwd() + "/debug-screenshots";
  require("fs").mkdirSync(dir, { recursive: true });
  const orig = page.screenshot.bind(page);
  (page as any).screenshot = (o: any = {}) =>
    orig(typeof o.path === "string" && o.path.startsWith("/app/storage")
      ? { ...o, path: o.path.replace("/app/storage/screenshots", dir) } : o);

  const view = await startDebugView();
  console.log("\n────────────────────────────────────────────");
  console.log(" DEBUG VIEW");
  if (view.novncUrl) console.log("  human (pixels):  " + view.novncUrl);
  else               console.log("  human (pixels):  headed window on this desktop");
  console.log("  agent (control): playwright connectOverCDP(\"" + view.cdpUrl + "\")");
  console.log("────────────────────────────────────────────\n");

  const result = await joinMeeting(page, {
    meetingUrl: url,
    botName: "Vexa Join Layer (isolated)",
    debug: true,
    hooks: {
      onState: (s, d) => console.log(`\n>>> [JOIN-STATE] ${s}${d ? " — " + JSON.stringify(d) : ""}\n`),
    },
  });

  console.log(`\n=== RESULT: admitted=${result.admitted} state=${result.state} ===`);
  console.log("Holding 60s so you can watch, then closing.");
  await new Promise((r) => setTimeout(r, 60_000));
  await browser.close();
})();
