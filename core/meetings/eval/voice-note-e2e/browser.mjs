// The voice-note E2E through a REAL BROWSER (#1783): Chromium with a fake microphone playing a speech
// WAV, signed in to a running Minutes, starts a voice note from the UI exactly as a person does (the
// empty chat's "Record a voice note" suggestion, or asking the agent, whose record_voice_note tool opens
// the recorder), and follows the audio the whole way: frames the page sends on
// `/voice-notes/{id}/stream`, the progress meeting-api acknowledges, transcript lines while it records,
// then Stop and the finished transcript. `client.py` drives the socket directly and so skips everything
// this covers: the microphone, the AudioWorklet, the terminal server's cookie-to-key relay, the UI.
//
//   SIGNIN_LINK_FILE=link.txt WAV=speech.wav node browser.mjs            # desktop
//   MOBILE=1 ...                                                          # a phone (Pixel 7 profile)
//   STALE_PARENT_COOKIE=1 ...                                             # a stale parent-domain sign-in
//                                                                          # cookie beside this host's
//   START=agent ...                                                       # ask the agent instead of the chip
//   SHOTS=dir ...                                                          # screenshots: the empty chat, recording
//
// The sign-in link is a credential: read from a file, never printed. Prints one JSON report and exits
// non-zero unless frames were acknowledged, a transcript line arrived WHILE recording, and Stop
// completed the note. Runs where Playwright's Chromium does (the Playwright image on bbb).
import { chromium, devices } from "playwright";
import fs from "node:fs";

const linkFile = process.env.SIGNIN_LINK_FILE || "link.txt";
const link = (fs.readFileSync(linkFile, "utf8").match(/https?:\/\/\S+/) || [])[0];
if (!link) { console.error(`no sign-in link in ${linkFile}`); process.exit(2); }
const wav = process.env.WAV || "speech.wav";
const recordSec = Number(process.env.RECORD_SEC || 30);
const mobile = process.env.MOBILE === "1";
const start = process.env.START || "chip";       // chip | agent
const shots = process.env.SHOTS || "";
const host = new URL(link).hostname;

const browser = await chromium.launch({
  args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", `--use-file-for-fake-audio-capture=${fs.realpathSync(wav)}`],
});
// VIEWPORT=2400x1200 PAGES_W=1380: a wide desktop with the pages pane dragged wide (the meeting view
// renders there) — dragged with the mouse on its splitter, as a person does.
const vp = (process.env.VIEWPORT || "").match(/^(\d+)x(\d+)$/);
const ctx = await browser.newContext({ ...(mobile ? devices["Pixel 7"] : {}), ...(vp ? { viewport: { width: +vp[1], height: +vp[2] } } : {}), permissions: ["microphone"] });

if (process.env.STALE_PARENT_COOKIE === "1") {
  // What a phone that once signed in on the parent domain carries: an older cookie of the same name,
  // sent FIRST. Pages and sockets must still agree on this host's own key.
  const parent = "." + host.split(".").slice(-2).join(".");
  await ctx.addCookies([{ name: "vexa-token", value: "stale-parent-domain-token", domain: parent, path: "/", secure: true, httpOnly: true, sameSite: "Lax" }]);
}
const page = await ctx.newPage();
const shot = async (name) => { if (shots) { fs.mkdirSync(shots, { recursive: true }); await page.screenshot({ path: `${shots}/${mobile ? "phone" : "desktop"}-${start}-${name}.png` }); } };
const report = { mobile, start, chip_icons: null, stale_parent_cookie: process.env.STALE_PARENT_COOKIE === "1", frames_sent: 0, sent_sec: 0, received_sec: 0, errors: [], lines_while_recording: 0, lines_final: 0, transcribed_sec: 0, stopped: false, native_meeting_id: null };
report.sockets = [];
report.streams = [];
page.on("response", (r) => { const u = new URL(r.url()); if (u.pathname === "/api/meeting/stream") report.streams.push({ status: r.status(), at: Date.now() }); });
page.on("requestfailed", (r) => { const u = new URL(r.url()); if (u.pathname === "/api/meeting/stream") report.streams.push({ failed: r.failure()?.errorText ?? "failed", at: Date.now() }); });
page.on("websocket", (ws) => {
  const row = { path: new URL(ws.url()).pathname, opened: Date.now(), closed_after_ms: null, frames_in: 0 };
  report.sockets.push(row);
  ws.on("framereceived", () => { row.frames_in++; });
  ws.on("close", () => { row.closed_after_ms = Date.now() - row.opened; });
  if (!ws.url().includes("/voice-notes/")) return;
  report.native_meeting_id = decodeURIComponent(ws.url().split("/voice-notes/")[1].split("/")[0]);
  ws.on("framesent", (f) => { if (typeof f.payload !== "string") { report.frames_sent++; report.sent_sec += (f.payload.length - 12) / 4 / 16000; } });
  ws.on("framereceived", (f) => {
    if (typeof f.payload !== "string") return;
    try {
      const m = JSON.parse(f.payload);
      if (typeof m.received_sec === "number") report.received_sec = m.received_sec;
      if (m.type === "error") report.errors.push(m.error);
      if (m.type === "stopped") report.stopped = true;
    } catch { /* not ours */ }
  });
});

async function transcript() {
  if (!report.native_meeting_id) return { lines: 0, end: 0, status: null };
  return page.evaluate(async (id) => {
    const r = await fetch(`/api/transcripts/voice_note/${encodeURIComponent(id)}`, { cache: "no-store" });
    if (!r.ok) return { lines: 0, end: 0, status: `http ${r.status}` };
    const b = await r.json();
    const segs = b.segments || [];
    return { lines: segs.length, end: segs.reduce((x, s) => Math.max(x, Number(s.end_time ?? s.end ?? 0)), 0), status: b.status ?? null };
  }, report.native_meeting_id);
}

try {
  await page.goto(link, { waitUntil: "load" });
  await page.waitForTimeout(3000);
  if (process.env.PAGES_W) {
    const sep = page.locator('[role="separator"][aria-label="Resize pages panel"]').first();
    const now = Number(await sep.getAttribute("aria-valuenow"));
    const b = await sep.boundingBox();
    if (b && now) {
      await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2);
      await page.mouse.down();
      await page.mouse.move(b.x + b.width / 2 - (Number(process.env.PAGES_W) - now), b.y + b.height / 2, { steps: 10 });
      await page.mouse.up();
      report.pages_w = Number(await sep.getAttribute("aria-valuenow"));
    }
  }
  // The empty chat's suggestions: each leads with an icon. Not landed on an empty chat (the last
  // one opened was a meeting)? Open a new one, as a person does — on a phone, from the chat list.
  if (!(await page.locator('[data-proposal="voice-note"]').locator("visible=true").count())) {
    const plus = page.locator('button[aria-label="New chat"]').locator("visible=true");
    if (!(await plus.count())) await page.locator('button[aria-label="Open the chat list"], button[aria-label="Show the chat list"]').locator("visible=true").first().click();
    await page.locator('button[aria-label="New chat"]').locator("visible=true").first().click({ timeout: 10000 });
  }
  await page.locator('[data-proposal="voice-note"]').locator("visible=true").first().waitFor({ timeout: 20000 });
  report.chip_icons = await page.locator("[data-proposal]").evaluateAll((bs) =>
    bs.map((b) => ({ kind: b.getAttribute("data-proposal"), icon: !!b.querySelector(".vx-chip-icon svg") })));
  await shot("empty-chat");
  if (start === "agent") {
    const box = page.locator("textarea").locator("visible=true").first();
    await box.fill("Record a voice note for me.");
    await box.press("Enter");
    report.asked_agent = true;
  } else {
    await page.locator('[data-proposal="voice-note"]').locator("visible=true").first().click({ timeout: 15000 });
  }
  // the recorder is open once its socket is
  for (let i = 0; i < 90 && !report.native_meeting_id; i++) await page.waitForTimeout(1000);
  if (!report.native_meeting_id) throw new Error("the recorder did not open");
  await page.waitForTimeout(4000);
  await shot("recording");
  await page.waitForTimeout(12000);
  await shot("recording-later");
  const until = Date.now() + recordSec * 1000;
  while (Date.now() < until) {
    await page.waitForTimeout(3000);
    const t = await transcript();
    report.lines_while_recording = Math.max(report.lines_while_recording, t.lines);
    const alert = await page.locator('[role="alert"]').allInnerTexts().catch(() => []);
    if (alert.length) report.ui_alert = alert.join(" | ");
  }
  // Stop: the meeting controls' Stop, or the chats rail's running clock (it stops the note too).
  const stop = page.locator('section[aria-label="Meeting controls"] button', { hasText: /^Stop$/ }).locator("visible=true");
  if (await stop.count()) await stop.first().click({ timeout: 10000 });
  else {
    const clock = page.locator('[data-voice-note="recording"]').locator("visible=true");
    // on a phone the chat list is a closed drawer: open it the way a person does
    if (!(await clock.count())) await page.locator('button[aria-label="Open the chat list"], button[aria-label="Show the chat list"]').locator("visible=true").first().click();
    await page.locator('[data-voice-note="recording"]').locator("visible=true").first().click({ timeout: 10000 });
    report.stopped_from = "rail";
  }
  for (let i = 0; i < 40; i++) {
    await page.waitForTimeout(3000);
    const t = await transcript();
    report.lines_final = t.lines; report.transcribed_sec = t.end; report.final_status = t.status;
    if (report.stopped && t.status && /complet/.test(String(t.status))) break;
  }
  await shot("stopped");
} catch (e) {
  report.errors.push(String(e).split("\n")[0]);
} finally {
  await browser.close();
}
report.sent_sec = Math.round(report.sent_sec * 10) / 10;
console.log(JSON.stringify(report, null, 2));
const ok = report.received_sec > 0 && report.lines_while_recording > 0 && report.stopped && !report.errors.length;
process.exit(ok ? 0 : 1);
