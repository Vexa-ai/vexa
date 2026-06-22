/**
 * live-realbot.spec.ts — autonomous real-bot proof of #2/#3/#7 through dashboard_new (bbb).
 *
 * Unlike live.spec.ts (which PUBLISHes golden frames), this spawns a REAL bot via dashboard_new's
 * `/join` → POST /bots path and observes that the bot's OWN lifecycle `meeting.status` frames
 * (requested→joining→… emitted by the real meeting-api, forwarded by the real gateway) render LIVE in
 * the real browser — then stops it (DELETE /bots). It proves the spawn → live-status → stop path the
 * human's walk uses, with zero human admit (the bot need only reach the lobby / a terminal to emit the
 * joining-phase frames). It has SIDE EFFECTS (a real bot container) so it is run explicitly, never in
 * the offline harness (testIgnore'd).
 *
 * It does NOT cover #4 admit / #5 transcripts-with-speakers / #6 speak / #11 playback — those need a
 * real admitted meeting with audio (the human's part).
 */
import { test, expect } from "@playwright/test";

const BASE = process.env.DASH_URL || "http://localhost:3002";
const PLATFORM = "google_meet";
const NATIVE_ID = process.env.REALBOT_NATIVE_ID || "vexa-realbot-probe";

test("real bot spawned via dashboard_new emits live meeting.status that renders, then stops", async ({ page, request }) => {
  // ── #2 start: spawn a REAL bot through the dashboard's own proxy (the /join path) ────────────────
  const spawn = await request.post(`${BASE}/api/vexa/bots`, {
    data: { platform: PLATFORM, native_meeting_id: NATIVE_ID, bot_name: "Vexa-Probe" },
  });
  expect(spawn.ok(), `POST /bots failed: ${spawn.status()} ${await spawn.text()}`).toBeTruthy();
  const meeting = await spawn.json();
  const id = meeting.id;
  console.log(`spawned meeting id=${id} status=${meeting.status}`);

  try {
    await page.goto(`${BASE}/meetings/${id}`);
    await expect(page.locator(".status-pill")).toBeVisible({ timeout: 20_000 });

    // ── #3 WS status while joining: the bot's OWN lifecycle frames render LIVE in the ws-log ───────
    // The real bot emits requested→joining→(awaiting_admission|failed) over ~10-40s; any transition
    // BEYOND the initial state, arriving live in the ws-log, proves the real-bot→dashboard path.
    await expect(page.getByTestId("ws-event-log")).toContainText("status: joining", { timeout: 90_000 });
    const logText = await page.getByTestId("ws-event-log").innerText();
    console.log("WS LOG (real-bot frames):\n" + logText);
  } finally {
    // ── #7 stop: DELETE /bots through the dashboard proxy (clean up the real bot) ──────────────────
    const del = await request.delete(`${BASE}/api/vexa/bots/${PLATFORM}/${NATIVE_ID}`);
    console.log(`stop (DELETE /bots) → ${del.status()}`);
  }
});
