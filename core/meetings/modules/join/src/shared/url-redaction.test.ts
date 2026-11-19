/**
 * Meeting-URL redaction: the shared vector table, then every call site in this brick that names a
 * URL in a log line or an error, driven with a fake page and a captured console.
 *
 * Positive control first: the same capture sees the secret when a line prints the raw URL, so a
 * green result below means the call site redacted it, not that the capture missed it.
 *
 * Run: npx tsx src/shared/url-redaction.test.ts
 */
import vectors from "./meeting-url-redaction-vectors.json";
import { redactMeetingUrl, redactUrlsIn, rethrowRedacted } from "./url-redaction";
import { log } from "../_host";
import { buildZoomWebClientUrl, joinZoomMeeting } from "../zoom/join";
import { describeZoomPreJoin } from "../zoom/prejoin-state";
import { startZoomRemovalMonitor } from "../zoom/removal";
import { buildJitsiMeetingUrl, joinJitsiMeeting } from "../jitsi/join";
import { startJitsiRemovalMonitor } from "../jitsi/removal";
import { joinMicrosoftTeams } from "../msteams/join";
import { joinGoogleMeeting, describeSelectorMiss } from "../googlemeet/join";
import { dumpAdmissionState } from "../googlemeet/admission";
import { resolvePlatform } from "../index";

let passed = 0;
let failed = 0;
function check(name: string, ok: boolean, detail = "") {
  if (ok) { console.log(`  \x1b[32mPASS\x1b[0m  ${name}`); passed++; }
  else { console.log(`  \x1b[31mFAIL\x1b[0m  ${name}${detail ? `\n        ${detail}` : ""}`); failed++; }
}

// ── the vector table ───────────────────────────────────────────────────────────────────────────
console.log("\n=== vectors ===");
for (const v of vectors.redact_url) {
  const got = redactMeetingUrl(v.in);
  check(`redact_url: ${v.why}`, got === v.out, `got ${JSON.stringify(got)}`);
}
for (const v of vectors.redact_urls_in) {
  const got = redactUrlsIn(v.in);
  check(`redact_urls_in: ${v.why}`, got === v.out, `got ${JSON.stringify(got)}`);
}

// ── capture ────────────────────────────────────────────────────────────────────────────────────
const SECRET = "TESTPASSCODE";
const ZOOM = `https://us05web.zoom.us/j/81234567890?pwd=${SECRET}`;
const ZOOM_LABEL = `https://zoom-lfx.platform.linuxfoundation.org/meeting/91234567890?password=${SECRET}&zak=${SECRET}`;
const TEAMS = `https://teams.microsoft.com/meet/3741234567890?p=${SECRET}`;
const JITSI = `https://meet.jit.si/VexaRoom42?jwt=${SECRET}`;
const MEET = `https://meet.google.com/abc-defg-hij?token=${SECRET}`;

async function capture(fn: () => unknown | Promise<unknown>): Promise<{ out: string; error: string }> {
  const lines: string[] = [];
  const orig = console.log;
  console.log = (...a: unknown[]) => { lines.push(a.map(String).join(" ")); };
  let error = "";
  try { await fn(); } catch (e: any) { error = `${e?.message ?? e}\n${e?.stack ?? ""}`; }
  finally { console.log = orig; }
  return { out: lines.join("\n"), error };
}

function noSecret(name: string, c: { out: string; error: string }, keep: string[]) {
  const all = `${c.out}\n${c.error}`;
  check(`${name}: no secret in log or error`, !all.includes(SECRET), all.slice(0, 400));
  for (const k of keep) check(`${name}: keeps ${k}`, all.includes(k), all.slice(0, 400));
}

/** A page whose navigation fails the way Playwright's does — quoting the URL. */
function failingPage(current = "about:blank"): any {
  const handlers: Record<string, Function[]> = {};
  return {
    handlers,
    async goto(url: string) {
      const e = new Error(`page.goto: Timeout 60000ms exceeded.\nCall log:\n  - navigating to "${url}", waiting until "domcontentloaded"`);
      e.name = "TimeoutError";
      throw e;
    },
    on(ev: string, h: Function) { (handlers[ev] ||= []).push(h); },
    off() {},
    url: () => current,
    title: async () => "Zoom",
    isClosed: () => true,
    mainFrame() { return MAIN; },
    async screenshot() {},
    async evaluate() { return { lang: "en", nav: "en-US" }; },
    async bringToFront() {},
    locator() { return { first: () => ({ isVisible: async () => false }), evaluateAll: async () => [] }; },
    frames: () => [],
    async waitForTimeout() {},
  };
}
const MAIN = { url: () => "" };
const cfg: any = { platform: "zoom", meetingUrl: ZOOM, botName: "Vexa" };

async function main() {
  console.log("\n=== positive control ===");
  const raw = await capture(() => log(`navigating to ${ZOOM}`));
  check("an unredacted line IS seen by the capture", raw.out.includes(SECRET));

  console.log("\n=== rethrowRedacted keeps the error object ===");
  const te = new Error(`navigating to "${ZOOM}"`); te.name = "TimeoutError";
  let caught: any;
  try { rethrowRedacted(te); } catch (e) { caught = e; }
  check("same object, same name", caught === te && caught.name === "TimeoutError");
  check("message redacted", !String(caught.message).includes(SECRET) && caught.message.includes("81234567890"));

  console.log("\n=== Zoom join ===");
  noSecret("joinZoomMeeting navigation log + goto error", await capture(() => joinZoomMeeting(failingPage(), ZOOM, "Vexa", cfg)),
    ["app.zoom.us/wc/81234567890/join"]);
  noSecret("joinZoomMeeting white-label navigation", await capture(() => joinZoomMeeting(failingPage(), ZOOM_LABEL, "Vexa", cfg)),
    ["zoom-lfx.platform.linuxfoundation.org/meeting/91234567890"]);
  noSecret("buildZoomWebClientUrl: no meeting id", await capture(() => buildZoomWebClientUrl(`https://zoom.us/x/abc?pwd=${SECRET}`)),
    ["zoom.us/x/abc"]);
  noSecret("describeZoomPreJoin: unparseable url fallback",
    await capture(() => log(describeZoomPreJoin({ url: `not a url?pwd=${SECRET}`, title: "t", text: "x", hold: null } as any))),
    ["not a url"]);

  console.log("\n=== Zoom removal monitor ===");
  {
    const page = failingPage(`https://app.zoom.us/wc/81234567890/join?pwd=${SECRET}`);
    const realNow = Date.now;
    const c = await capture(async () => {
      const stop = startZoomRemovalMonitor(page, () => {});
      const nav = page.handlers["framenavigated"][0];
      nav({ url: () => `https://app.zoom.us/wc/81234567890/leave?pwd=${SECRET}` }); // inside the grace period
      (MAIN as any).url = () => "";
      Date.now = () => realNow() + 60_000;
      nav(Object.assign(MAIN, { url: () => `https://zoom.us/signin?zak=${SECRET}` }));
      await new Promise((r) => setTimeout(r, 10));
      Date.now = realNow;
      stop();
    });
    noSecret("zoom removal: grace log, removal reason, current URL", c, ["zoom.us/signin", "app.zoom.us/wc/81234567890"]);
  }

  console.log("\n=== Jitsi ===");
  noSecret("joinJitsiMeeting navigation log + goto error", await capture(() => joinJitsiMeeting(failingPage(), JITSI, "Vexa", cfg)),
    ["meet.jit.si/VexaRoom42"]);
  noSecret("buildJitsiMeetingUrl: no room", await capture(() => buildJitsiMeetingUrl(`https://meet.jit.si/?jwt=${SECRET}`)),
    ["meet.jit.si"]);
  {
    const page = failingPage(`https://meet.jit.si/VexaRoom42?jwt=${SECRET}`);
    const realNow = Date.now;
    const c = await capture(async () => {
      const stop = startJitsiRemovalMonitor(page, () => {});
      Date.now = () => realNow() + 60_000;
      page.handlers["framenavigated"][0](Object.assign(MAIN, { url: () => `https://example.org/closed?jwt=${SECRET}` }));
      await new Promise((r) => setTimeout(r, 10));
      Date.now = realNow;
      stop();
    });
    noSecret("jitsi removal: reason and current URL", c, ["example.org/closed", "meet.jit.si/VexaRoom42"]);
  }

  console.log("\n=== Teams ===");
  noSecret("joinMicrosoftTeams goto error", await capture(() => joinMicrosoftTeams(failingPage(), TEAMS, "Vexa", cfg)),
    ["teams.microsoft.com/meet/3741234567890"]);

  console.log("\n=== Google Meet ===");
  noSecret("joinGoogleMeeting goto error", await capture(() => joinGoogleMeeting(failingPage(), MEET, "Vexa", cfg)),
    ["meet.google.com/abc-defg-hij"]);
  noSecret("describeSelectorMiss page context", await capture(async () => log(await describeSelectorMiss(failingPage(MEET), ["#x"], 10, "join button", null))),
    ["meet.google.com/abc-defg-hij"]);
  process.env.DEBUG_ADMISSION = "1";
  noSecret("dumpAdmissionState url", await capture(() => dumpAdmissionState(failingPage(MEET), "t")),
    ["meet.google.com/abc-defg-hij"]);
  delete process.env.DEBUG_ADMISSION;

  console.log("\n=== platform inference ===");
  noSecret("resolvePlatform: unknown host", await capture(() => resolvePlatform(`https://video.example.org/r/42?pwd=${SECRET}`)),
    ["video.example.org/r/42"]);

  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
}
main();
