/**
 * debug-join.ts — drives the joining layer inside the HOT DEBUG CONTAINER.
 *
 * Do not run on a host. The only supported invocation is:
 *
 *   make debug URL="https://meet.google.com/xxx-xxxx-xxx"
 *
 * which builds the self-contained image (Xvfb + humanized X11 + noVNC) and
 * serves the live view at http://localhost:6080/vnc.html — the same
 * environment every run, every machine: the watch harness is reproducible
 * or it is not evidence.
 * The CDP URL is printed so an agent can connectOverCDP and drive/inspect
 * the SAME browser a human is watching.
 */
import { chromium } from "playwright-extra";
import StealthPlugin from "puppeteer-extra-plugin-stealth";
import { joinMeeting, startDebugView } from "../src/index";


if (process.platform !== "linux" || !process.env.DISPLAY) {
  console.error("watch mode runs only in the debug container (reproducible env).");
  console.error('Use: make debug URL="https://meet.google.com/xxx-xxxx-xxx"');
  process.exit(1);
}

const url = process.argv[2];
const isMeetUrl = !!url && url.includes("meet.google.com");
const isTeamsUrl = !!url && (url.includes("teams.microsoft.com") || url.includes("teams.live.com"));
if (!isMeetUrl && !isTeamsUrl) {
  console.error("Usage: tsx scripts/debug-join.ts <google-meet-or-teams-url>");
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

  // Teams admission throws on rejection/timeout (monolith behavior) — catch so
  // the harness always prints a RESULT line instead of dying unhandled.
  let result: { admitted: boolean; state: string };
  try {
    result = await joinMeeting(page, {
      meetingUrl: url,
      botName: "Vexa Join Layer (isolated)",
      debug: true,
      hooks: {
        onState: (s, d) => console.log(`\n>>> [JOIN-STATE] ${s}${d ? " — " + JSON.stringify(d) : ""}\n`),
      },
    });
  } catch (err: any) {
    console.error(`\n=== JOIN ERROR: ${err?.message || err} ===`);
    result = { admitted: false, state: "error" };
  }

  console.log(`\n=== RESULT: admitted=${result.admitted} state=${result.state} ===`);
  console.log("Holding 60s so you can watch, then closing.");
  await new Promise((r) => setTimeout(r, 60_000));
  await browser.close();
})();
