/**
 * live-playback.spec.ts — autonomous proof of the DASHBOARD SIDE of #5 + #11 (bbb, no human).
 *
 * Uses an EXISTING completed meeting on bbb that already holds real prior-walk transcript segments
 * (with speakers) + a finalized recording. No human admit / live audio is needed — the data exists. It
 * proves dashboard_new RENDERS attributed transcripts (#5) and PLAYS the recording master with
 * segment-click seek (#11) — the exact rendering the live human walk relies on. (The live-arrival of
 * #5 and audible #6 still need a human-admitted meeting.)
 *
 * Driven against dashboard_new → bbb. Pass MEETING_DBID / EXPECT_SPEAKER for the chosen meeting.
 */
import { test, expect } from "@playwright/test";

const BASE = process.env.DASH_URL || "http://localhost:3002";
const DBID = process.env.MEETING_DBID || "17";
const EXPECT_SPEAKER = process.env.EXPECT_SPEAKER || "Dmitriy Grankin";

test("dashboard_new renders attributed transcript (#5) + plays recording with segment seek (#11)", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (e) => pageErrors.push(String(e)));

  await page.goto(`${BASE}/meetings/${DBID}`);
  await expect(page.locator(".status-pill")).toBeVisible({ timeout: 20_000 });

  // ── #5: attributed transcript renders (bootstrapped from REST — real prior-walk segments) ────────
  const segments = page.getByTestId("transcript-segment");
  await expect(segments.first()).toBeVisible({ timeout: 15_000 });
  const segCount = await segments.count();
  expect(segCount, "expected real transcript segments").toBeGreaterThan(0);
  await expect(page.locator(`[data-testid="transcript-segment"][data-speaker="${EXPECT_SPEAKER}"]`).first())
    .toBeVisible({ timeout: 10_000 });
  console.log(`#5 transcript: ${segCount} segments, speaker "${EXPECT_SPEAKER}" attributed ✓`);

  // ── #11: the recording player mounts an <audio> pointed at the real bytes path (raw_url, proxied) ─
  const audio = page.locator("audio");
  await expect(audio).toHaveCount(1, { timeout: 15_000 });
  const src = await audio.getAttribute("src");
  expect(src, `audio src=${src}`).toContain("/raw");
  console.log(`#11 player: audio src=${src}`);

  // the bytes actually stream + decode in a real browser (readyState ≥ HAVE_METADATA=1)
  await expect
    .poll(async () => page.evaluate(() => {
      const a = document.querySelector("audio") as HTMLAudioElement | null;
      return a ? a.readyState : -1;
    }), { timeout: 25_000, intervals: [500] })
    .toBeGreaterThanOrEqual(1);
  console.log("#11 playback: audio loaded metadata (bytes stream + decode) ✓");

  // ── #11 alignment: clicking a transcript segment seeks the audio (onSegmentClick → seekTo) ────────
  // click the last segment → currentTime should move to a SANE recording offset (relative seconds),
  // NOT an absolute unix epoch (the bug this caught: a ~1.78e9 seek).
  await segments.last().click();
  await expect
    .poll(async () => page.evaluate(() => (document.querySelector("audio") as HTMLAudioElement)?.currentTime ?? 0),
      { timeout: 8_000, intervals: [300] })
    .toBeGreaterThan(0);
  const after = await page.evaluate(() => (document.querySelector("audio") as HTMLAudioElement)?.currentTime ?? 0);
  expect(after, `seek must be a relative recording offset, not an absolute epoch (got ${after})`).toBeLessThan(100_000);
  console.log(`#11 seek: segment click → currentTime=${after.toFixed(1)}s (sane recording offset) ✓`);

  expect(pageErrors, `uncaught page errors: ${pageErrors.join("; ")}`).toHaveLength(0);
});
