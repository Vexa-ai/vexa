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
import { joinMeeting, resolvePlatform, startDebugView, leaveGoogleMeet, leaveMicrosoftTeams, leaveZoomMeeting, getJoinBrowserArgs, type Platform } from "../src/index";


if (process.platform !== "linux" || !process.env.DISPLAY) {
  console.error("watch mode runs only in the debug container (reproducible env).");
  console.error('Use: make debug URL="https://meet.google.com/xxx-xxxx-xxx"');
  process.exit(1);
}

const url = process.argv[2];
// One rule for "which platform is this URL": the package's own resolver, which matches on the
// hostname rather than a substring of the URL. joinMeeting() below infers the platform the same
// way, so the usage check and the join can no longer disagree about what the URL is.
let platform: Platform | null = null;
try { platform = url ? resolvePlatform(url) : null; } catch { platform = null; }
if (platform !== "google_meet" && platform !== "teams" && platform !== "zoom") {
  console.error("Usage: tsx scripts/debug-join.ts <google-meet, teams, or zoom url>");
  process.exit(1);
}

(async () => {
  const stealth = StealthPlugin();
  stealth.enabledEvasions.delete("iframe.contentWindow");
  stealth.enabledEvasions.delete("media.codecs");
  stealth.enabledEvasions.delete("user-agent-override");
  chromium.use(stealth);

  // AUTH_PROFILE set → authenticated join: a persistent context with a saved login
  // profile (produced by @vexa/remote-browser `make login`). The brick skips guest
  // name-entry and joins as the signed-in account. Args mirror getAuthenticatedBrowserArgs
  // (inlined — the join debug image has no remote-browser dep). NOT incognito: it would
  // wipe the stored cookies that make the join authenticated.
  const AUTH_PROFILE = process.env.AUTH_PROFILE;
  let page: any;
  let cleanup: () => Promise<void>;
  if (AUTH_PROFILE) {
    console.log(`\n>>> [debug-join] AUTHENTICATED mode — persistent profile: ${AUTH_PROFILE}\n`);
    const authArgs = [
      "--no-sandbox", "--disable-setuid-sandbox", "--disable-blink-features=AutomationControlled",
      "--disable-infobars", "--disable-gpu", "--disable-features=IsolateOrigins,site-per-process",
      "--disable-site-isolation-trials", "--in-process-gpu", "--use-fake-ui-for-media-stream",
      "--use-file-for-fake-video-capture=/dev/null", "--disable-features=VizDisplayCompositor",
      "--password-store=basic", "--remote-debugging-port=9222",
    ];
    const ctx = await chromium.launchPersistentContext(AUTH_PROFILE, {
      headless: false, ignoreDefaultArgs: ["--enable-automation"], args: authArgs, viewport: null,
    });
    page = ctx.pages()[0] ?? await ctx.newPage();
    cleanup = () => ctx.close();
  } else {
    const browser = await chromium.launch({
      headless: false, // visible window on macOS; renders to Xvfb :99 on Linux
      // Canonical join launch args (single source of truth) so this harness
      // reproduces the vexa-bot image's browser byte-for-byte — no drift.
      args: [
        ...getJoinBrowserArgs(),
        "--remote-debugging-port=9222", // CDP for the agent to attach
      ],
    });
    const context = await browser.newContext({ permissions: ["camera", "microphone"], viewport: null });
    page = await context.newPage();
    cleanup = () => browser.close();
  }

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
      authenticated: !!AUTH_PROFILE,
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
  console.log("Holding 60s so you can watch, then leaving.");
  await new Promise((r) => setTimeout(r, 60_000));

  // Leave via the platform UI before closing — killing the browser mid-call
  // strands a ghost participant in the meeting (Zoom holds the tile for its
  // reconnect grace period; Teams/Meet can linger too).
  if (result.admitted) {
    try {
      const leave = platform === "zoom" ? leaveZoomMeeting : platform === "teams" ? leaveMicrosoftTeams : leaveGoogleMeet;
      const left = await leave(page, undefined, "debug_harness_done");
      console.log(`Graceful leave: ${left ? "ok" : "leave button not found (already out?)"}`);
    } catch (err: any) {
      console.error(`Graceful leave failed: ${err?.message || err}`);
    }
  }
  await cleanup();
})();
